"""jev_naics API (JEV-48, JEV-50): search NAICS 2022 by its official text in Postgres, classify
a description (search then Jev picks, the tree walk, or the hybrid of the two), look codes up."""

from __future__ import annotations

import hashlib
import json
import os
import re
import time
import uuid
from contextlib import asynccontextmanager
from collections.abc import Callable
from typing import Any, Literal

import uvicorn
from fastapi import APIRouter, Depends, FastAPI, HTTPException, Path as PathParam, Query, Request, Response
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import JSONResponse
from jev_naics._jev.openapi import SWAGGER_UI
from jev_naics._jev import Decider, ReplayMissing, decider_for
from pydantic import BaseModel, ConfigDict, Field, field_validator

from jev_naics import __version__, database_searcher, db, logs
from jev_naics.api.auth import bind_host, check_startup, require_api_key
from jev_naics.env import load_typesafe_api_key
from jev_naics.full import FULL_TRANSCRIPT, HoldReason, classify_full, level_name
from jev_naics.full_eval import FULL_EVALS
from jev_naics.questions import (
    NOUL_FULL_FIT_HOLD,
    NOUL_FULL_UNDERSPECIFIED,
    NOUL_SEARCH_FIT_HOLD,
    NOUL_SEARCH_UNDERSPECIFIED,
    PROB_FULL_PATH,
    PROB_SEARCH_PICK,
    SEARCH_K,
    SPELL_MAX_EDITS,
    SPELL_WEIGHT,
    TERM_WEIGHT,
)
from jev_naics.search_data import load_search_data
from jev_naics.search_pick import SEARCH_TRANSCRIPT, Searcher, classify_search
from jev_naics.terms import expand_terms
from jev_naics.tree import ROOT, load_tree, reference

log = logs.log


_TRUE, _FALSE = {"", "1", "true", "yes", "on"}, {"0", "false", "no", "off"}


def migrate_on_start() -> bool:
    """NAICS_MIGRATE_ON_START (default true). False is the production shape (JEV-56): the database
    is built by a one-off deploy step, `python -m jev_naics.db_cli build`, never by the API, and
    /v1/health answers 503 until that has run. Any other value refuses to start."""
    value = os.environ.get("NAICS_MIGRATE_ON_START", "").strip().lower()
    if value in _TRUE:
        return True
    if value in _FALSE:
        return False
    raise ValueError(f"NAICS_MIGRATE_ON_START must be true or false; got {value!r}")


@asynccontextmanager
async def lifespan(_app: FastAPI):
    """With NAICS_DATABASE_URL set and NAICS_MIGRATE_ON_START true (the default), migrate and load
    the search data (only when it changed). Without a database the API still serves: search answers
    503 and search mode holds. With NAICS_MIGRATE_ON_START false it never touches the database here."""
    check_startup()  # no keys (and no loopback-only opt-out) refuses to start (SECURITY.md, service authentication)
    migrate = migrate_on_start()
    logs.event("startup", version=__version__, migrate_on_start=migrate)
    if migrate and os.environ.get("NAICS_DATABASE_URL", "").strip():
        try:
            data = load_search_data()
            with db.connect() as conn:
                _applied, loaded = db.migrate_and_load(conn, data)
            logs.event("data_load", data_version=data.version, industries=len(data.industries), reloaded=loaded)
        except Exception as exc:  # noqa: BLE001 - the tree still works; search reports the failure
            logs.error(exc, stage="data_load")
    yield

app = FastAPI(
    title="jev-naics",
    description=(
        "NAICS from a description of operations, with TypeSafe Jev choosing and code deciding.\n\n"
        "1. **classify**: `search` finds the best-matching industries with Postgres full-text search over the "
        "official Census text, then Jev picks one of only those in one call, with a fit check. `full` walks "
        "all of NAICS 2022 (1,012 industries) down the official tree (about 5 calls). `hybrid` tries `search` "
        "first and falls back to the tree walk only when search is unsure.\n"
        "2. **naics**: full-text search of the Census text, or look up any code. No model call.\n"
        "3. **service**: health and the eval descriptions.\n\n"
        "**Notes.**\n"
        "- Classifying calls Jev and **costs TypeSafe credits** (with no key it replays recorded answers).\n"
        "- A hold is a normal 200 result: too vague, unsure, or the picked industry does not fit.\n"
        "- Routes are versioned under `/v1`; the unversioned paths still work but are deprecated.\n"
        "- **Auth:** every route except health and the reference export needs `Authorization: Bearer <key>`, a "
        "key from the service's `NAICS_API_KEYS`. No credential is 401; a wrong one is 403.\n"
        "- Every response carries an `X-Request-ID` header: the caller's own (1-64 of `A-Z a-z 0-9 _ -`), else "
        "a new one. It is on the request's log line, so a caller can correlate its logs with the service's."
    ),
    version=__version__,
    lifespan=lifespan,
    swagger_ui_parameters=SWAGGER_UI,
    openapi_tags=[
        {"name": "classify", "description": "Description in, NAICS code or a hold out (costs TypeSafe credits)."},
        {"name": "naics", "description": "The official NAICS 2022 text: full-text search, or look up a code. "
                                         "No model call."},
        {"name": "service", "description": "Health and the eval descriptions. No model call."},
    ],
)

router = APIRouter()
_LOG_FIELDS = "jev_naics.log_fields"  # per-request, in the ASGI scope
# Routes added after /v1 existed are served at /v1 only, with no deprecated unversioned alias.
v1_only = APIRouter()


def get_full_decider() -> Decider:
    load_typesafe_api_key()
    return decider_for(FULL_TRANSCRIPT)


def get_search_decider() -> Decider:
    load_typesafe_api_key()
    return decider_for(SEARCH_TRANSCRIPT)


def get_hybrid_decider() -> Decider:
    from jev_naics.hybrid import HYBRID_TRANSCRIPT

    load_typesafe_api_key()
    return decider_for(HYBRID_TRANSCRIPT)


def get_consensus_decider() -> Decider:
    from jev_naics.consensus import CONSENSUS_TRANSCRIPT

    load_typesafe_api_key()
    return decider_for(CONSENSUS_TRANSCRIPT)


def get_assist_decider() -> Decider:
    from jev_naics.assist import ASSIST_TRANSCRIPT

    load_typesafe_api_key()
    return decider_for(ASSIST_TRANSCRIPT)


def get_refine_decider() -> Decider:
    from jev_naics.refine import REFINE_TRANSCRIPT

    load_typesafe_api_key()
    return decider_for(REFINE_TRANSCRIPT)


Spelling = Callable[[str], list[tuple[str, str]]]


def get_spelling() -> Spelling:
    """The search's spelling corrections for a text (JEV-52), one connection per call."""
    def corrections(text: str) -> list[tuple[str, str]]:
        with db.connect() as conn:
            return db.spelling_corrections(conn, text)

    return corrections


def _search_notes(text: str, spelling: Spelling) -> dict[str, Any]:
    """How search read the text. Informational: a failure here shows no notes, never an error."""
    try:
        fixes = spelling(text)
    except (db.psycopg.Error, OSError, RuntimeError, ValueError):
        fixes = []
    return {"corrections": [{"typed": t, "corrected": c} for t, c in fixes],
            "expanded": [{"term": t, "census": c} for t, c in expand_terms(text)]}


def get_searcher() -> Searcher:
    """Full-text search in Postgres, one connection per search."""
    return database_searcher()


# ---- models ---------------------------------------------------------------------------

class ErrorOut(BaseModel):
    """An error. Most are a sentence; request validation returns FastAPI's list of problems."""

    detail: str | list[dict[str, Any]] = Field(description="Why the request failed.")
    model_config = ConfigDict(json_schema_extra={"examples": [{"detail": "no NAICS 2022 code 999999"}]})


class ClassifyIn(BaseModel):
    """A description of what the business does."""

    description: str = Field(max_length=4000, description="The description of operations. Must not be blank.")
    mode: Literal["hybrid", "search", "full", "tree"] = Field(
        default="hybrid", description="hybrid: search first; only when search holds because the right industry may "
        "be missing, the tree walk adds its finalists and Jev picks once over both (1 call, about 7 on a "
        "fallback). search: full-text search, then Jev picks among the results (one call). full: walk all of "
        "NAICS 2022 down the tree. tree: same as full (the library and README call this method `tree`; the "
        "response echoes whichever of the two was sent).")
    model_config = ConfigDict(json_schema_extra={"examples": [
        {"description": "Mobile car detailing at customers' homes.", "mode": "hybrid"},
        {"description": "Small-animal veterinary clinic.", "mode": "search"},
        {"description": "Family dental practice with two dentists and a hygienist.", "mode": "full"}]})

    @field_validator("description")
    @classmethod
    def _not_blank(cls, value: str) -> str:
        if not value.strip():
            raise ValueError("description must not be blank")
        return value.strip()


class StepOut(BaseModel):
    """One node on the path from sector to industry."""

    code: str = Field(description="NAICS code (sectors 31-33, 44-45, 48-49 are ranges).")
    title: str = Field(description="Official NAICS 2022 title.")
    level: str = Field(description="sector, subsector, industry group, NAICS industry, or national industry.")


class OptionOut(BaseModel):
    """One option Jev weighed at a node."""

    code: str = Field(description="The child code.")
    title: str = Field(description="Its official title.")
    p: float = Field(description="Jev's probability for this child.")


class AskedOut(BaseModel):
    """One node asked at a level: its children were the options."""

    node: str = Field(description="The node whose children were offered (root for sectors).")
    title: str = Field(description="The node's title.")
    valid: bool = Field(description="False when the answer was malformed; that path was dropped.")
    options: list[str] = Field(description="Every child offered (the closed set).")
    top: list[OptionOut] = Field(description="The five most probable children.")


class BeamPathOut(BaseModel):
    """A path kept after a level."""

    path: list[str] = Field(description="Codes from sector down.")
    score: float = Field(description="Geometric mean of the path's edge probabilities.")


class LevelOut(BaseModel):
    """One level of the walk: one call, every open node asked in parallel."""

    level: int = Field(description="1 = sectors.")
    asked: list[AskedOut] = Field(description="Nodes asked at this level.")
    beam: list[BeamPathOut] = Field(description="The paths kept (up to 3).")


class FinalistOut(BaseModel):
    """A candidate industry (a walk finalist or a search result) and how the final step judged it."""

    code: str = Field(description="Six-digit industry.")
    title: str = Field(description="Its official title.")
    p: float | None = Field(description="Probability in the final pick; null when there was nothing to pick "
                            "between (one search result) or the pick was malformed.")
    path_score: float | None = Field(default=None, description="full: its path score from the walk.")
    search_rank: int | None = Field(default=None, description="search: its place in the results (1 = best).")
    source: Literal["search", "tree"] | None = Field(
        default=None, description="hybrid fallback: where the candidate came from (tree: a walk finalist search "
        "never returned; it has no search rank or score).")
    search_score: float | None = Field(default=None, description="search: its full-text score.")
    fit: float | None = Field(description="Probability this industry describes the business.")


class ClassifyOut(BaseModel):
    """The result: a NAICS code, or a hold, and how Jev got there."""

    mode: Literal["hybrid", "search", "full", "tree"] = Field(
        description="Which classifier ran: echoes what was sent (full and tree are the same walk).")
    gate: Literal["auto_act", "hold"] = Field(description="auto_act: code emitted. hold: a person decides.")
    code: str | None = Field(description="The NAICS 2022 code; null on a hold.")
    title: str | None = Field(description="Its official title.")
    probability: float | None = Field(description=f"Probability of the picked code; acts at {PROB_SEARCH_PICK:.2f} "
                                      f"or more (search) or {PROB_FULL_PATH:.2f} (full). A single search result "
                                      "holds without a pick: nothing to compare.")
    path_score: float | None = Field(
        description="full only: the joint probability of the picked path across the walk (reported, not gated).")
    path: list[StepOut] = Field(description="Sector to industry for the answer; empty on a hold.")
    model_path: list[StepOut] = Field(
        description="On a hold, Jev's best path: a diagnostic only, never an answer to act on.")
    hold_reason: HoldReason | None = Field(description=(
        "Why it held, if it did. too_vague: the description is too vague or mixes unrelated lines "
        "of business. low_probability: the pick's probability is below its bar, or (search/hybrid) "
        "search found only one candidate, so there is nothing to compare. misfit: Jev doubts the "
        "picked industry describes the business (its fit check is below the bar). no_match: "
        "(search/hybrid) no NAICS industry's official text matches these words; Jev was not asked. "
        "invalid_answer: an answer was malformed or missing; nothing is acted on. service_error: no "
        "TypeSafe key and no recorded answer, or a TypeSafe call failed."))
    reason: str = Field(description="One sentence.")
    underspecified_noul: float | None = Field(description="Probability the description is too vague; holds at "
                                              f"{NOUL_SEARCH_UNDERSPECIFIED:.2f} (search) or "
                                              f"{NOUL_FULL_UNDERSPECIFIED:.2f} (full).")
    fit_noul: float | None = Field(description="Probability the picked industry fits; holds below "
                                   f"{NOUL_SEARCH_FIT_HOLD:.2f} (search) or {NOUL_FULL_FIT_HOLD:.2f} (full).")
    finalists: list[FinalistOut] = Field(description="search: the search results Jev picked among. full: the "
                                         "industries the walk ended on.")
    trace: list[LevelOut] = Field(description="full only: every level of the walk.")
    search_notes: SearchNotesOut | None = Field(default=None, description="search and hybrid: how search read the text "
                                                "(spelling corrections, modern terms).")
    model: str | None = Field(description="The Jev model that answered.")
    calls: int = Field(description="Jev calls made.")
    seconds: float = Field(description="Wall time.")
    fallback: bool = Field(default=False, description="hybrid: the tree walk ran because search held.")
    model_config = ConfigDict(json_schema_extra={"examples": [{
        "mode": "full", "gate": "auto_act", "code": "621210", "title": "Offices of Dentists", "probability": 1.0,
        "path_score": 1.0, "path": [{"code": "62", "title": "Health Care and Social Assistance", "level": "sector"}],
        "model_path": [],
        "hold_reason": None, "reason": "Picked 621210 at p 1.00; fit 0.98.", "underspecified_noul": 0.08,
        "fit_noul": 0.98, "finalists": [], "trace": [], "model": "jev", "calls": 5, "seconds": 2.4}]})


class ConsensusIn(BaseModel):
    """A description of what the business does."""

    description: str = Field(max_length=4000, description="The description of operations. Must not be blank.")
    model_config = ConfigDict(json_schema_extra={"examples": [{"description": "Mobile car detailing at customers' homes."}]})

    @field_validator("description")
    @classmethod
    def _not_blank(cls, value: str) -> str:
        if not value.strip():
            raise ValueError("description must not be blank")
        return value.strip()


class MethodOut(BaseModel):
    """What one method answered on its own."""

    gate: Literal["auto_act", "hold"] = Field(description="auto_act: it picked a code. hold: it held.")
    code: str | None = Field(description="Its code; null on a hold.")
    probability: float | None = Field(description="Its pick's probability (on a hold, the pick it leaned to).")
    fit_noul: float | None = Field(description="Its pick's fit.")
    hold_reason: HoldReason | None = Field(description="Why it held, if it did.")
    calls: int = Field(description="Jev calls it made.")


class CandidateOut(BaseModel):
    """An industry the judge was offered: one that the hybrid or the tree walk considered."""

    code: str = Field(description="Six-digit industry.")
    title: str = Field(description="Its official title.")
    p: float | None = Field(description="The judge's probability for it.")
    fit: float | None = Field(description="Probability this industry describes the business.")
    sources: list[Literal["hybrid", "tree"]] = Field(description="Which methods considered it.")
    hybrid_code: bool = Field(description="The hybrid picked it.")
    tree_code: bool = Field(description="The tree walk picked it.")


class ConsensusOut(BaseModel):
    """The consensus answer or hold, the step that produced it, and what each method said."""

    gate: Literal["auto_act", "hold"] = Field(description="auto_act: code emitted. hold: a person decides.")
    code: str | None = Field(description="The NAICS 2022 code; null on a hold.")
    title: str | None = Field(description="Its official title.")
    probability: float | None = Field(description="agreement: the hybrid's probability. judge: the judge's.")
    fit_noul: float | None = Field(description="The picked industry's fit.")
    hold_reason: HoldReason | Literal["methods_disagree"] | None = Field(description=(
        "Why it held, if it did: the reasons of /v1/classify, or (tree-first) methods_disagree: the tree walk "
        "and the hybrid picked different codes."))
    step: Literal["agreement", "tree_only", "judge", "hold", "broker"] = Field(description=(
        "agreement: the hybrid and the tree walk picked the same code; no further call. tree_only (triage, "
        "tree-first): only the tree walk answered and the hybrid held unsure; the tree's answer stands. judge: Jev picked once over "
        "every industry either method considered, with search's gates. hold (consensus) or broker (triage, tree-first): a "
        "method held as too vague, on a malformed answer, or on a service error, there were fewer than two "
        "candidates, (triage) the judge held, or (tree-first) the methods disagreed; a person decides."))
    reason: str = Field(description="One sentence.")
    methods: dict[Literal["hybrid", "tree"], MethodOut] = Field(description="Each method's own answer.")
    candidates: list[CandidateOut] = Field(description="judge only: what the judge was offered.")
    model: str | None = Field(description="The Jev model that answered.")
    calls: int = Field(description="Jev calls made in all.")
    seconds: float = Field(description="Wall time.")
    model_config = ConfigDict(json_schema_extra={"examples": [{
        "gate": "auto_act", "code": "811192", "title": "Car Washes", "probability": 0.99, "fit_noul": 0.95,
        "hold_reason": None, "step": "agreement",
        "reason": "The hybrid and the tree walk both picked 811192 (p 0.99 and 0.97).",
        "methods": {"hybrid": {"gate": "auto_act", "code": "811192", "probability": 0.99, "fit_noul": 0.95,
                               "hold_reason": None, "calls": 1},
                    "tree": {"gate": "auto_act", "code": "811192", "probability": 0.97, "fit_noul": 0.93,
                             "hold_reason": None, "calls": 5}},
        "candidates": [], "model": "jev", "calls": 6, "seconds": 8.1}]})


class DeliveryOut(BaseModel):
    """How Jev read the delivery of what the business sells (a closed Choice with a not_stated option)."""

    choice: str = Field(description="own_software_product, custom_work_per_client, ongoing_managed_service, "
                        "makes_physical_goods, resells_to_businesses, sells_to_consumers, in_person_service, or "
                        "not_stated.")
    p: float | None = Field(description="Its probability.")


class ChecksOut(BaseModel):
    """The completeness checks: probabilities that each holds for the text."""

    is_operations_text: float | None = Field(description="It describes what the business does, not only revenue.")
    states_activity: float | None = Field(description="The activity is specific enough to identify the industry.")
    states_customers: float | None = Field(description="It says who the customers are.")
    single_primary_activity: float | None = Field(description="One main activity is clear.")
    underspecified: float | None = Field(description="The tree walk's vagueness check (too vague or mixed).")
    delivery_mode: DeliveryOut | None = Field(description="How it is delivered.")


class PickOptionOut(BaseModel):
    """One answer on a pick-list."""

    code: str = Field(description="The NAICS 2022 code this answer means.")
    label: str = Field(description="The answer as shown to the broker.")


class PicklistOut(BaseModel):
    """One question that settles a commonly confused pair; the broker's answer is the code."""

    id: str = Field(description="Which pick-list (assist.py PICKLISTS, or make_or_resell).")
    question: str = Field(description="Fixed copy.")
    options: list[PickOptionOut] = Field(description="The answers and the code each one means.")


class AssistCandidateOut(BaseModel):
    """A tree-walk finalist, best first."""

    code: str = Field(description="Six-digit industry, a tree-walk finalist.")
    title: str = Field(description="Its official title.")
    p: float | None = Field(description="Its probability in the walk's final pick.")
    path_score: float | None = Field(description="Its path score from the walk.")
    fit: float | None = Field(description="Probability this industry describes the business.")


class SectorOut(BaseModel):
    """The sector the text leans to."""

    code: str = Field(description="The sector Jev leans to (31-33, 44-45, 48-49 are ranges).")
    title: str = Field(description="Its official title.")
    p: float = Field(description="Its probability.")


class AssistIn(BaseModel):
    """The description as typed so far."""

    description: str = Field(max_length=4000, description="The description of operations. Must not be blank.")
    model_config = ConfigDict(json_schema_extra={"examples": [{"description": "Mobile car detailing at customers' homes."}]})

    @field_validator("description")
    @classmethod
    def _not_blank(cls, value: str) -> str:
        if not value.strip():
            raise ValueError("description must not be blank")
        return value.strip()


class AssistOut(BaseModel):
    """How specific a NAICS code the text supports so far, and what to add."""

    status: Literal["CODED", "PARTIAL", "AMBIGUOUS", "NEEDS_INFO", "ERROR"] = Field(description=(
        "CODED: a 6-digit code. PARTIAL: an industry group or a sector. AMBIGUOUS: between commonly confused "
        "codes; see picklist. NEEDS_INFO: the text does not say enough yet (never a 6-digit code). ERROR: a call "
        "failed or an answer was malformed; show nothing."))
    naics: str | None = Field(description="The 6-digit code; only when CODED.")
    naics_title: str | None = Field(description="Its official title.")
    naics_partial: str | None = Field(description="The deepest level the text supports: 6, 4, or 2 digits.")
    partial_title: str | None = Field(description="Its official title.")
    level: Literal[6, 4, 2] | None = Field(description="Digits of naics_partial.")
    candidates: list[AssistCandidateOut] = Field(description="The walk's top finalists, best first.")
    checks: ChecksOut | None = Field(description="The completeness checks.")
    sector: SectorOut | None = Field(description="The sector Jev leans to: a diagnostic, shown on the meter only "
                                     "at its bar.")
    tooltip_id: str | None = Field(description="The one missing thing: is_operations_text, states_activity, "
                                   "delivery_mode, states_customers, or single_primary_activity.")
    tooltip: str | None = Field(description="Its fixed copy.")
    picklist: PicklistOut | None = Field(description="AMBIGUOUS only: the question that settles it.")
    reason: str = Field(description="One sentence.")
    model: str | None = Field(description="The Jev model that answered.")
    calls: int = Field(description="Jev calls made (1 when the text needs more information).")
    seconds: float = Field(description="Wall time.")
    config_version: str = Field(description="Version of the assist questions and bars (questions.py).")
    model_config = ConfigDict(json_schema_extra={"examples": [{
        "status": "NEEDS_INFO", "naics": None, "naics_title": None, "naics_partial": "51",
        "partial_title": "Information", "level": 2, "candidates": [],
        "checks": {"is_operations_text": 0.9, "states_activity": 0.2, "states_customers": 0.1,
                   "single_primary_activity": 0.9, "underspecified": 0.6,
                   "delivery_mode": {"choice": "own_software_product", "p": 0.8}},
        "sector": {"code": "51", "title": "Information", "p": 0.85}, "tooltip_id": "states_activity",
        "tooltip": "What exactly does the business do or sell? Be specific: 'scheduling software for dental "
                   "offices' rather than 'software'.", "picklist": None,
        "reason": "The text does not yet say enough to assign a code.", "model": "jev", "calls": 1,
        "seconds": 0.3, "config_version": "2026-10-05"}]})


class RefineIn(AssistIn):
    """The description, and which refine variant to run."""

    variant: Literal["wide", "verify", "score", "scoreb", "agree", "cascade", "cascadeb", "union"] = Field(
        default="cascade", description="Which refine variant (experiments/EXPERIMENT.md, E9 and E10). Provisional "
        "default: verify first, then the score step only when verify does not code.")
    lexicon: bool = Field(default=True, description=(
        "Read the text against the lexicon first (E15): a recognised term or abbreviation ('SaaS', 'HVAC') is checked "
        "even when it is a single word, and its Census wording is added for Jev when the text is short."))


class RefineOut(AssistOut):
    """An assist result from the refine experiment: the same fields, plus what the wide pick and verify saw."""

    status: Literal["CODED", "PARTIAL", "AMBIGUOUS", "NEEDS_INFO", "TOO_SHORT", "ERROR"] = Field(description=(
        "As assist, plus TOO_SHORT: fewer than three words, so nothing was asked (0 calls). It is not an error and "
        "not a vague-text result."))
    variant: str = Field(description="The variant that ran.")
    wide: list[dict[str, Any]] = Field(description="The wide pick's candidates, best first.")
    verify: list[dict[str, Any]] = Field(description="Each verified candidate: p, p_reversed, level, fit.")
    agree: bool | None = Field(description="score variants: the two opposite-order picks agree.")
    usage: dict[str, int] = Field(description="Jev token usage summed over the calls.")
    reading: list[dict[str, str]] = Field(description="Lexicon terms read in the text and the Census wording each means "
                                          "(empty wording: recognised, nothing added).")


class UserPickIn(BaseModel):
    """The broker's answer to a pick-list."""

    picklist_id: str = Field(max_length=64, description="The pick-list the broker answered.")
    code: str = Field(pattern=r"^[0-9]{6}$", description="The code the chosen answer means.")


class FinalizeIn(AssistIn):
    """The final description, and the broker's pick-list answer if they gave one."""

    user_pick: UserPickIn | None = Field(default=None, description="The broker's pick-list answer, if any.")
    model_config = ConfigDict(json_schema_extra={"examples": [
        {"description": "Mobile car detailing at customers' homes."},
        {"description": "We build software for clients.", "user_pick": {"picklist_id": "541511_513210",
                                                                         "code": "541511"}}]})


class FinalizeOut(BaseModel):
    """The final code or hold, and where the code came from."""

    gate: Literal["auto_act", "hold"] = Field(description="auto_act: a code. hold: a person reviews it.")
    code: str | None = Field(description="The NAICS 2022 code; null on a hold.")
    title: str | None = Field(description="Its official title.")
    source: Literal["jev_ensemble", "user_pick"] | None = Field(description=(
        "jev_ensemble: tree-first agreed or stood. user_pick: the broker's pick-list answer. Null on a hold."))
    hold_reason: HoldReason | Literal["methods_disagree"] | None = Field(description="Why it held, if it did.")
    reason: str = Field(description="One sentence.")
    ensemble: ConsensusOut = Field(description="What tree-first said, kept for audit even under a user pick.")
    config_version: str = Field(description="Version of the assist questions and bars.")
    model_config = ConfigDict(json_schema_extra={"examples": [{
        "gate": "auto_act", "code": "811192", "title": "Car Washes", "source": "jev_ensemble", "hold_reason": None,
        "reason": "The tree walk and the hybrid both picked 811192 (p 0.99 and 0.99).",
        "ensemble": {"gate": "auto_act", "code": "811192", "title": "Car Washes", "probability": 0.99,
                     "fit_noul": 0.95, "hold_reason": None, "step": "agreement", "reason": "…",
                     "methods": {}, "candidates": [], "model": "jev", "calls": 6, "seconds": 1.0},
        "config_version": "2026-10-05"}]})


class NodeOut(BaseModel):
    """A NAICS 2022 node with its official text."""

    code: str = Field(description="The code.")
    title: str = Field(description="Official title.")
    level: str = Field(description="Its level in the tree.")
    description: str = Field(description="Official Census description (first paragraph).")
    index_terms: list[str] = Field(description="Census index terms (industries only; the first 12).")
    path: list[StepOut] = Field(description="Its ancestors, sector first.")
    children: list[StepOut] = Field(description="Its children; empty for an industry.")
    model_config = ConfigDict(json_schema_extra={"examples": [{
        "code": "621210", "title": "Offices of Dentists", "level": "national industry",
        "description": "This industry comprises establishments of health practitioners ...",
        "index_terms": ["Dentists' offices"], "path": [], "children": []}]})


class CorrectionOut(BaseModel):
    """A typed word search corrected to the nearest Census word."""

    typed: str = Field(description="The word as typed.")
    corrected: str = Field(description="The Census word searched with it (at reduced weight).")


class ExpandedOut(BaseModel):
    """A modern term search read as Census wording (from the reviewed list)."""

    term: str = Field(description="The term found in the text.")
    census: str = Field(description="The Census wording searched with it.")


class SearchNotesOut(BaseModel):
    """How search read the text: spelling corrections and modern terms. Never a code."""

    corrections: list[CorrectionOut] = Field(description="Typed words corrected to Census words.")
    expanded: list[ExpandedOut] = Field(description="Modern terms read as Census wording.")


class FullTextHitOut(BaseModel):
    """An industry whose official text matches the words searched."""

    code: str = Field(description="Six-digit industry.")
    title: str = Field(description="Official title.")
    rank: float = Field(description="Full-text score: each matched word's weighted rank times its rarity (IDF).")
    snippet: str = Field(description="The official description or index entries around the matching words.")
    model_config = ConfigDict(json_schema_extra={"examples": [
        {"code": "541940", "title": "Veterinary Services", "rank": 1.9,
         "snippet": "licensed <b>veterinary</b> practitioners primarily engaged in the practice"}]})


class SearchOut(SearchNotesOut):
    """Full-text search results, and how the text was read."""

    hits: list[FullTextHitOut] = Field(description="Industries ranked by their official text, best first.")
    model_config = ConfigDict(json_schema_extra={"examples": [{
        "hits": [{"code": "541940", "title": "Veterinary Services", "rank": 1.9, "snippet": "..."}],
        "corrections": [{"typed": "vetrinary", "corrected": "veterinary"}], "expanded": []}]})


class SearchHitOut(BaseModel):
    """An industry whose title or index terms match."""

    code: str = Field(description="Six-digit industry.")
    title: str = Field(description="Official title.")
    matched: str = Field(description="The title or index term that matched.")
    model_config = ConfigDict(json_schema_extra={"examples": [
        {"code": "812199", "title": "Other Personal Care Services", "matched": "Tattoo parlors"}]})


class EvalOut(BaseModel):
    """An eval description with its gold answer."""

    id: str = Field(description="Case id.")
    ops_description: str = Field(description="The description.")
    expect_code: str | None = Field(description="Gold NAICS 2022 code; null when it must hold.")
    expect_hold: bool = Field(description="True when the right answer is a hold.")
    model_config = ConfigDict(json_schema_extra={"examples": [
        {"id": "dental-office", "ops_description": "...", "expect_code": "621210", "expect_hold": False}]})


class ReferenceOut(BaseModel):
    """The public NAICS 2022 reference data: every industry and the 2017 -> 2022 concordance."""

    version: str = Field(description="A hash of `industries` and `from_2017` together: it changes only when "
                         "the data does, so a caller's saved copy can be checked against the service.")
    industries: dict[str, str] = Field(description="Every NAICS 2022 six-digit industry (1,012): code -> "
                                       "official title.")
    from_2017: dict[str, list[str]] = Field(
        description="A NAICS 2017 industry code that 2022 retired or partly moved -> every 2022 industry it "
        "became (more than one when it split), from the Census concordance. Codes 2022 kept unchanged are not "
        "listed.")
    model_config = ConfigDict(json_schema_extra={"examples": [{
        "version": "naics2022-0123456789abcdef",
        "industries": {"541940": "Veterinary Services", "621210": "Offices of Dentists"},
        "from_2017": {"212111": ["212114"], "212113": ["212114", "212115"]}}]})


class HealthOut(BaseModel):
    """The tree is loaded and, in the deploy shape (NAICS_MIGRATE_ON_START=false), the database too."""

    ok: bool = Field(description="True on a 200. False (with a 503) when the database is checked and not ready.")
    industries: int = Field(description="Six-digit industries in the tree.")
    sectors: int = Field(description="Sectors in the tree.")
    database: Literal["ready", "not_ready", "not_checked"] = Field(
        description="not_checked: NAICS_MIGRATE_ON_START is true (the default), so the API built the database "
        "itself at start and health checks only the tree. ready / not_ready: NAICS_MIGRATE_ON_START is false, so "
        "health checks that `db_cli build` has loaded the current data (1,012 industries, this code's data "
        "version, a known query).")
    problems: list[str] = Field(default_factory=list, description="What is wrong with the database; empty when ready.")
    model_config = ConfigDict(json_schema_extra={"examples": [
        {"ok": True, "industries": 1012, "sectors": 20, "database": "ready", "problems": []},
        {"ok": False, "industries": 1012, "sectors": 20, "database": "not_ready",
         "problems": ["no data loaded (run: python -m jev_naics.db_cli build)"]}]})


def _err(text: str) -> dict[str, Any]:
    return {"model": ErrorOut, "description": text}


# Every route except /health and /naics/reference needs a key (SECURITY.md). The dependency sits on each
# protected route, not on an include, so the deprecated unversioned copies are protected too.
_PROTECTED = [Depends(require_api_key)]
_AUTH_ERRORS: dict[int | str, dict[str, Any]] = {
    401: _err("No credential: send `Authorization: Bearer <key>`."),
    403: _err("The credential is not one of the service's keys."),
    503: _err("Service authentication is not configured (no `NAICS_API_KEYS`)."),
}


def _steps(codes: list[str]) -> list[dict[str, str]]:
    tree = load_tree()
    return [{"code": c, "title": tree.title(c), "level": level_name(c)} for c in codes if c in tree]


# ---- classify -------------------------------------------------------------------------

def classify_context(request: Request, body: ClassifyIn) -> ClassifyIn:
    """Resolved before the deciders and the searcher, so the request's log line knows the mode and
    the description's hash and length (never its text) even when one of those fails."""
    text = body.description
    request.scope[_LOG_FIELDS] = {
        "event": "classify", "mode": body.mode, "description_len": len(text),
        "description_sha256": hashlib.sha256(text.encode("utf-8")).hexdigest()[:12],
        "data_version": load_search_data().version}
    return body


@router.post(
    "/classify", dependencies=_PROTECTED, response_model=ClassifyOut,
    responses={**_AUTH_ERRORS, 409: _err("No TypeSafe key is set and no answer is recorded for this exact description."),
               422: _err("The description is blank or too long, or mode is not hybrid, search, full, or tree.")},
    tags=["classify"], summary="Classify a description",
    response_description="A NAICS code or a hold, with the path and every step Jev took.",
    description=(
        "`hybrid` **(1 Jev call, or about 7 on a fallback)** tries `search` first and falls back to `full` only "
        "when search is unsure. "
        f"`search` **(1 Jev call)** runs the full-text search, then Jev picks one of the top {SEARCH_K} results "
        "in one call and checks each one's fit; without the search database it holds. "
        "`full` **(about 5 Jev calls)** walks the Census tree: one call per level with every kept node asked in "
        "parallel, the best 3 paths kept, then one call that picks among the finalists and checks each one's "
        "fit. `tree` is the same walk as `full` under the name the library and README use; the response's "
        "`mode` echoes whichever of the two was sent. Every mode **costs TypeSafe credits**. Code holds when "
        "the description is too vague, the pick's probability is under its bar, or the picked industry does "
        "not fit (bars in the response field descriptions)."
    ),
)
def classify_endpoint(request: Request, body: ClassifyIn = Depends(classify_context),
                      full_decider: Decider = Depends(get_full_decider),
                      search_decider: Decider = Depends(get_search_decider),
                      hybrid_decider: Decider = Depends(get_hybrid_decider),
                      searcher: Searcher = Depends(get_searcher),
                      spelling: Spelling = Depends(get_spelling)) -> dict[str, Any]:
    try:
        if body.mode == "hybrid":
            from jev_naics.hybrid import classify_hybrid

            r = classify_hybrid(body.description, decider=hybrid_decider, searcher=searcher, strict=True)
        elif body.mode == "search":
            r = classify_search(body.description, decider=search_decider, searcher=searcher, strict=True)
        else:
            r = classify_full(body.description, decider=full_decider, strict=True)
        request.scope[_LOG_FIELDS].update(
            gate=r.gate, hold_reason=r.hold_reason, code=r.code, probability=r.probability, fit_noul=r.fit_noul,
            fallback=r.fallback, calls=r.calls, model=r.model)
        return {"mode": body.mode, "gate": r.gate, "code": r.code, "title": r.title, "probability": r.probability,
                "path_score": r.path_score, "path": _steps(r.path), "model_path": _steps(r.model_path),
                "hold_reason": r.hold_reason,
                "reason": r.reason, "underspecified_noul": r.underspecified_noul, "fit_noul": r.fit_noul,
                "finalists": r.finalists, "trace": r.trace, "model": r.model, "calls": r.calls,
                "search_notes": (_search_notes(body.description, spelling)
                                 if body.mode in ("search", "hybrid") else None),
                "seconds": r.seconds, "fallback": r.fallback}
    except ReplayMissing as exc:
        raise HTTPException(status_code=409, detail=f"no recorded answer and no TypeSafe key: {exc}") from exc


def _consensus_response(request: Request, event: str, text: str, run, decider: Decider,
                        searcher: Searcher) -> dict[str, Any]:
    request.scope[_LOG_FIELDS] = {
        "event": event, "description_len": len(text),
        "description_sha256": hashlib.sha256(text.encode("utf-8")).hexdigest()[:12],
        "data_version": load_search_data().version}
    try:
        out = run(text, decider=decider, searcher=searcher, strict=True)
    except ReplayMissing as exc:
        raise HTTPException(status_code=409, detail=f"no recorded answer and no TypeSafe key: {exc}") from exc
    r = out.result
    request.scope[_LOG_FIELDS].update(gate=r.gate, hold_reason=r.hold_reason, code=r.code, step=out.step,
                                      probability=r.probability, calls=r.calls, model=r.model)

    def method(m) -> dict[str, Any]:
        return {"gate": m.gate, "code": m.code, "probability": m.probability, "fit_noul": m.fit_noul,
                "hold_reason": m.hold_reason, "calls": m.calls}

    return {"gate": r.gate, "code": r.code, "title": r.title, "probability": r.probability, "fit_noul": r.fit_noul,
            "hold_reason": r.hold_reason, "step": out.step, "reason": r.reason,
            "methods": {"hybrid": method(out.hybrid), "tree": method(out.tree)}, "candidates": out.candidates,
            "model": r.model, "calls": r.calls, "seconds": r.seconds}


@v1_only.post(
    "/consensus", dependencies=_PROTECTED, response_model=ConsensusOut,
    responses={**_AUTH_ERRORS, 409: _err("No TypeSafe key is set and no answer is recorded for this exact description."),
               422: _err("The description is blank or too long.")},
    tags=["classify"], summary="Classify by consensus of the hybrid and the tree walk",
    response_description="A NAICS code or a hold, the step that produced it, and each method's answer.",
    description=(
        "Runs `hybrid` and `full` on the description. When both pick the same code, that code is the answer "
        "with no further call (`step: agreement`). When either holds as too vague, on a malformed answer, or "
        "on a service error, it holds. Otherwise Jev picks once over every industry either method considered "
        "(their answers plus their search results or finalists, a closed set built in code), with the same "
        "probability, fit, and vagueness gates as `search` (`step: judge`). **Costs TypeSafe credits**: about "
        "6 Jev calls, 12 on a hybrid fallback, plus 1 when judged."
    ),
)
def consensus_endpoint(request: Request, body: ConsensusIn, decider: Decider = Depends(get_consensus_decider),
                       searcher: Searcher = Depends(get_searcher)) -> dict[str, Any]:
    from jev_naics.consensus import classify_consensus

    return _consensus_response(request, "consensus", body.description, classify_consensus, decider, searcher)


@v1_only.post(
    "/triage", dependencies=_PROTECTED, response_model=ConsensusOut,
    responses={**_AUTH_ERRORS, 409: _err("No TypeSafe key is set and no answer is recorded for this exact description."),
               422: _err("The description is blank or too long.")},
    tags=["classify"], summary="Triage for a broker: accept, judge, or hand back",
    response_description="A NAICS code or a hold for the broker, the step that produced it, and each method's answer.",
    description=(
        "Runs `hybrid` and `full` **in parallel**. Accepted with no further call when both pick the same code "
        "(`step: agreement`) or when only the tree walk answers and the hybrid held unsure (`step: tree_only`). "
        "A hybrid that failed (malformed answer, service error) is never read as \"no answer\": it goes back to "
        "the broker. Anything else (a split, a lone hybrid answer, both unsure) is judged once over every industry "
        "either method considered, with `search`'s gates (`step: judge`); a judge hold, or a too-vague "
        "description, goes back to the broker (`step: broker`). **Costs TypeSafe credits**: about 6 Jev calls, "
        "12 on a hybrid fallback, plus 1 when judged."
    ),
)
def triage_endpoint(request: Request, body: ConsensusIn, decider: Decider = Depends(get_consensus_decider),
                    searcher: Searcher = Depends(get_searcher)) -> dict[str, Any]:
    from jev_naics.consensus import classify_triage

    return _consensus_response(request, "triage", body.description, classify_triage, decider, searcher)


@v1_only.post(
    "/tree-first", dependencies=_PROTECTED, response_model=ConsensusOut,
    responses={**_AUTH_ERRORS, 409: _err("No TypeSafe key is set and no answer is recorded for this exact description."),
               422: _err("The description is blank or too long.")},
    tags=["classify"], summary="Tree walk first, checked by the hybrid",
    response_description="The tree walk's code or a hold for the broker, the step, and each method's answer.",
    description=(
        "Runs `hybrid` and `full` **in parallel**; the tree walk's answer is the default. It stands when the "
        "hybrid picks the same code (`step: agreement`) or holds unsure: low probability, misfit, or no match "
        "(`step: tree_only`). It goes back to the broker (`step: broker`) when the hybrid picks a different code "
        "(`hold_reason: methods_disagree`), finds the description too vague, or fails, and whenever the tree "
        "walk holds. No judge call. **Costs TypeSafe credits**: about 6 Jev calls, 12 on a hybrid fallback."
    ),
)
def tree_first_endpoint(request: Request, body: ConsensusIn, decider: Decider = Depends(get_consensus_decider),
                        searcher: Searcher = Depends(get_searcher)) -> dict[str, Any]:
    from jev_naics.consensus import classify_tree_first

    return _consensus_response(request, "tree_first", body.description, classify_tree_first, decider, searcher)


@v1_only.post(
    "/assist", dependencies=_PROTECTED, response_model=AssistOut,
    responses={**_AUTH_ERRORS, 409: _err("No TypeSafe key is set and no answer is recorded for this exact description."),
               422: _err("The description is blank or too long.")},
    tags=["classify"], summary="Assist as the broker types",
    response_description="How specific a code the text supports, the one thing to add, and a pick-list if needed.",
    description=(
        "Call on a typing pause. One Jev call asks the sector and the completeness checks together; when the text "
        "is not yet operations, not specific, or too vague, it stops there (`NEEDS_INFO`, **1 call**, never a "
        "6-digit code). Otherwise the tree walk continues to a code (`CODED`), a pick-list between commonly "
        "confused codes (`AMBIGUOUS`), or an industry group or sector (`PARTIAL`): **about 5 calls**. The "
        "tooltip and pick-list copy are fixed; Jev only answers checks and closed Choices. **Costs TypeSafe "
        "credits.**"
    ),
)
def assist_endpoint(request: Request, body: AssistIn, decider: Decider = Depends(get_assist_decider)) -> dict[str, Any]:
    from jev_naics.assist import assess

    text = body.description
    request.scope[_LOG_FIELDS] = {
        "event": "assist", "description_len": len(text),
        "description_sha256": hashlib.sha256(text.encode("utf-8")).hexdigest()[:12]}
    try:
        r = assess(text, decider=decider, strict=True)
    except ReplayMissing as exc:
        raise HTTPException(status_code=409, detail=f"no recorded answer and no TypeSafe key: {exc}") from exc
    request.scope[_LOG_FIELDS].update(assist_status=r.status, code=r.naics, digits=r.level, tooltip=r.tooltip_id,
                                      picklist=r.picklist and r.picklist["id"], calls=r.calls, model=r.model,
                                      config_version=r.config_version)
    c = r.checks
    checks = None if c is None else {
        "is_operations_text": c.operations, "states_activity": c.activity, "states_customers": c.customers,
        "single_primary_activity": c.single, "underspecified": c.underspecified,
        "delivery_mode": {"choice": c.delivery, "p": c.delivery_p} if c.delivery else None}
    return {"status": r.status, "naics": r.naics, "naics_title": r.naics_title, "naics_partial": r.naics_partial,
            "partial_title": r.partial_title, "level": r.level, "candidates": r.candidates, "checks": checks,
            "sector": r.sector, "tooltip_id": r.tooltip_id, "tooltip": r.tooltip, "picklist": r.picklist,
            "reason": r.reason, "model": r.model, "calls": r.calls, "seconds": r.seconds,
            "config_version": r.config_version}


@v1_only.post(
    "/refine/assist", dependencies=_PROTECTED, response_model=RefineOut,
    responses={**_AUTH_ERRORS, 409: _err("No TypeSafe key is set and no answer is recorded for this exact description."),
               422: _err("The description is blank or too long.")},
    tags=["classify"], summary="Assist, refine experiment (E9)",
    response_description="The assist result from the wide pick plus a verify step, with what each saw.",
    description=(
        "An experiment beside `/assist`, which it does not change. One Jev call carries the completeness checks and "
        "a Choice over all 1,012 industries (in blocks of at most 240); a second verifies the best candidates with "
        "their full Census text (`variant`). With `lexicon` (the default) a one- or two-word text is checked when it holds a recognised term or passes the completeness checks; without it, fewer than three words returns `TOO_SHORT` with no call. "
        "**Costs TypeSafe credits**: about 40-45k input tokens per call."
    ),
)
def refine_endpoint(request: Request, body: RefineIn, decider: Decider = Depends(get_refine_decider)) -> dict[str, Any]:
    from jev_naics.lexicon import default_lexicon
    from jev_naics.questions import REFINE_MIN_WORDS
    from jev_naics.refine import refine

    text = body.description
    request.scope[_LOG_FIELDS] = {
        "event": "refine", "description_len": len(text), "variant": body.variant,
        "description_sha256": hashlib.sha256(text.encode("utf-8")).hexdigest()[:12]}
    try:
        rr = refine(text, decider=decider, variant=body.variant, strict=True,
                    lexicon=default_lexicon() if body.lexicon else None,
                    min_words=REFINE_MIN_WORDS if body.lexicon else 3)
    except ReplayMissing as exc:
        raise HTTPException(status_code=409, detail=f"no recorded answer and no TypeSafe key: {exc}") from exc
    r = rr.result
    request.scope[_LOG_FIELDS].update(assist_status=r.status, code=r.naics, digits=r.level, tooltip=r.tooltip_id,
                                      picklist=r.picklist and r.picklist["id"], calls=r.calls, model=r.model,
                                      config_version=r.config_version)
    c = r.checks
    checks = None if c is None else {
        "is_operations_text": c.operations, "states_activity": c.activity, "states_customers": c.customers,
        "single_primary_activity": c.single, "underspecified": c.underspecified,
        "delivery_mode": {"choice": c.delivery, "p": c.delivery_p} if c.delivery else None}
    return {"status": r.status, "naics": r.naics, "naics_title": r.naics_title, "naics_partial": r.naics_partial,
            "partial_title": r.partial_title, "level": r.level, "candidates": r.candidates, "checks": checks,
            "sector": r.sector, "tooltip_id": r.tooltip_id, "tooltip": r.tooltip, "picklist": r.picklist,
            "reason": r.reason, "model": r.model, "calls": r.calls, "seconds": r.seconds,
            "config_version": r.config_version, "variant": rr.variant, "wide": rr.wide, "verify": rr.verify,
            "agree": rr.agree, "usage": rr.usage, "reading": rr.reading}


@v1_only.post(
    "/finalize", dependencies=_PROTECTED, response_model=FinalizeOut,
    responses={**_AUTH_ERRORS, 409: _err("No TypeSafe key is set and no answer is recorded for this exact description."),
               422: _err("The description is blank or too long, or the pick is not a NAICS 2022 industry.")},
    tags=["classify"], summary="Finalize on submit",
    response_description="The final code or hold, its source, and what tree-first said.",
    description=(
        "Runs tree-first (the tree walk, checked by the hybrid). With a broker's pick-list answer, that answer is "
        "the code (`source: user_pick`), and tree-first still runs and is returned for audit. Submission is "
        "never blocked: a hold means a person reviews it. **Costs TypeSafe credits**: about 6 Jev calls."
    ),
)
def finalize_endpoint(request: Request, body: FinalizeIn, decider: Decider = Depends(get_consensus_decider),
                      searcher: Searcher = Depends(get_searcher)) -> dict[str, Any]:
    from jev_naics.consensus import classify_tree_first
    from jev_naics.questions import ASSIST_CONFIG_VERSION

    tree = load_tree()
    pick = body.user_pick
    if pick and (pick.code not in tree or not tree.is_leaf(pick.code)):
        raise HTTPException(status_code=422, detail=f"{pick.code} is not a NAICS 2022 industry")
    ensemble = _consensus_response(request, "finalize", body.description, classify_tree_first, decider, searcher)
    request.scope[_LOG_FIELDS].update(source="user_pick" if pick else "jev_ensemble",
                                      picklist=pick.picklist_id if pick else None)
    if pick:
        return {"gate": "auto_act", "code": pick.code, "title": tree.title(pick.code), "source": "user_pick",
                "hold_reason": None, "reason": f"The broker answered the {pick.picklist_id} pick-list: {pick.code}.",
                "ensemble": ensemble, "config_version": ASSIST_CONFIG_VERSION}
    acted = ensemble["gate"] == "auto_act"
    return {"gate": ensemble["gate"], "code": ensemble["code"], "title": ensemble["title"],
            "source": "jev_ensemble" if acted else None, "hold_reason": ensemble["hold_reason"],
            "reason": ensemble["reason"], "ensemble": ensemble, "config_version": ASSIST_CONFIG_VERSION}


# ---- naics ----------------------------------------------------------------------------

CODE = PathParam(description="A NAICS 2022 code (2 to 6 digits, or a range sector such as 31-33).",
                 examples=["621210"])


@router.get(
    "/naics/search", dependencies=_PROTECTED, response_model=SearchOut,
    responses={**_AUTH_ERRORS, 422: _err("q is blank or too long, or limit is outside 1-50."),
               503: _err("The search database is not configured or not reachable, or service authentication "
                         "is not configured.")},
    tags=["naics"], summary="Full-text search of the official NAICS text",
    response_description="Industries ranked by how well their official text matches, best first.",
    description=(
        "Type a description of operations, or any words. Postgres full-text search ranks every 2022 industry by "
        "its official title, Census index entries, illustrative examples, the activities other industries' "
        "cross-references send to it, and its description, with rare words counting more. **No model call, no "
        "cost.** Use **Classify a description** with mode `search` to have Jev pick among these.\n\n"
        f"- **Spelling:** a word the Census text lacks is corrected to the nearest Census word (at most "
        f"{SPELL_MAX_EDITS} edits), counted at {SPELL_WEIGHT:g} of a typed word's weight, with the typed word "
        "kept (`corrections`).\n"
        "- **Modern terms:** words such as SaaS or telehealth add their Census wording (`expanded`) at "
        f"{TERM_WEIGHT:g} of a typed word's weight, from a reviewed list; never a code."
    ),
)
def full_text_search(request: Request,
                     q: str = Query(max_length=4000, description="The words or description to search."),
                     limit: int = Query(default=SEARCH_K, ge=1, le=db.MAX_LIMIT, description="How many results, 1-50."),
                     searcher: Searcher = Depends(get_searcher),
                     spelling: Spelling = Depends(get_spelling)) -> dict[str, Any]:
    if not q.strip():
        raise HTTPException(status_code=422, detail="q must not be blank")
    try:
        hits = searcher(q, limit)
    except (db.psycopg.Error, OSError, RuntimeError, ValueError) as exc:
        request.scope[_LOG_FIELDS] = {"error": type(exc).__name__}
        raise HTTPException(status_code=503,
                            detail=f"the search database is not available: {type(exc).__name__}") from exc
    return {"hits": [{"code": h.code, "title": h.title, "rank": round(h.rank, 4), "snippet": h.snippet}
                     for h in hits], **_search_notes(q, spelling)}


@v1_only.get(
    "/naics/reference", response_model=ReferenceOut, tags=["naics"],
    summary="Export the NAICS 2022 reference data",
    response_description="Every industry's code and title, the 2017 -> 2022 concordance, and their version.",
    description=("The public Census reference data a caller needs without the classifier: the 1,012 six-digit "
                 "industries with their official titles (for example, a form's list of codes) and the 2017 -> "
                 "2022 concordance (to read a written 2017 code). The same JSON as "
                 "`python -m jev_naics --export-reference`. **No model call, no database.**"),
)
def get_reference() -> dict[str, Any]:
    return reference()


@router.get(
    "/naics/{code}", dependencies=_PROTECTED, response_model=NodeOut, responses={**_AUTH_ERRORS, 404: _err("No NAICS 2022 node has this code.")},
    tags=["naics"], summary="Look up a NAICS code",
    response_description="The node's official title, description, path, and children.",
    description="Any level of the official 2022 tree. No model call.",
)
def get_naics(code: str = CODE) -> dict[str, Any]:
    tree = load_tree()
    if code not in tree or code == ROOT:
        raise HTTPException(status_code=404, detail=f"no NAICS 2022 code {code}")
    return {"code": code, "title": tree.title(code), "level": level_name(code), "description": tree.description(code),
            "index_terms": tree.index_terms(code, limit=12), "path": _steps(tree.path(code)[:-1]),
            "children": _steps(tree.children(code))}


@router.get(
    "/naics", dependencies=_PROTECTED, response_model=list[SearchHitOut], responses={**_AUTH_ERRORS, 422: _err("q is shorter than 2 characters.")},
    tags=["naics"], summary="Find a word in titles or index terms",
    response_description="Up to 20 industries, title matches first.",
    description="A plain text search of official titles and Census index terms. No model call.",
)
def search_naics(q: str = Query(min_length=2, max_length=80, description="Words to find, e.g. tattoo.")) -> list[dict]:
    tree, needle = load_tree(), q.lower()
    hits = []
    for code in tree.leaves():
        title = tree.title(code)
        if needle in title.lower():
            hits.append((0, code, title))
            continue
        term = next((t for t in tree.index_terms(code) if needle in t.lower()), None)
        if term:
            hits.append((1, code, term))
    return [{"code": c, "title": tree.title(c), "matched": m} for _r, c, m in sorted(hits)[:20]]


# ---- service --------------------------------------------------------------------------

@router.get(
    "/evals", dependencies=_PROTECTED, responses=_AUTH_ERRORS, response_model=list[EvalOut], tags=["service"],
    summary="List the eval descriptions", response_description="The full-NAICS eval set with gold codes.",
    description="The descriptions used to measure full-NAICS classification, with their gold answers.",
)
def list_evals() -> list[dict]:
    return [{k: c[k] for k in ("id", "ops_description", "expect_code", "expect_hold")}
            for c in json.loads(FULL_EVALS.read_text(encoding="utf-8"))]


def _database_problems() -> list[str]:
    """db.verify's problems, or why the database could not be checked. Fails closed: any error is a
    problem. Only an exception's type is reported: its message can carry the connection string."""
    try:
        url = db.database_url()
    except RuntimeError:
        return ["NAICS_DATABASE_URL is not set"]
    try:
        with db.connect(url) as conn:
            return db.verify(conn)
    except db.UndefinedTable:
        return ["not migrated (run: python -m jev_naics.db_cli build)"]
    except Exception as exc:  # noqa: BLE001 - unreachable, refused, timed out: not ready
        return [f"database unreachable: {type(exc).__name__}"]


@router.get(
    "/health", response_model=HealthOut, tags=["service"], summary="Check the tree and the database",
    response_description="Ready: the tree is loaded (and, in the deploy shape, the database is current).",
    responses={503: {"model": HealthOut, "description": "NAICS_MIGRATE_ON_START is false and the database is not "
                     "ready: unreachable, not migrated, empty, or an older data version. Run "
                     "`python -m jev_naics.db_cli build`."}},
    description=("Loads the NAICS 2022 tree. With NAICS_MIGRATE_ON_START=false (the production shape, where a "
                 "deploy step builds the database) it also verifies the database and answers **503** with "
                 "`ok: false` until it is ready, so a load balancer or Compose health check never routes to an "
                 "API whose search would fail. No model call."),
)
def health(response: Response) -> dict:
    tree = load_tree()
    out = {"ok": True, "industries": len(tree.leaves()), "sectors": len(tree.children(ROOT)),
           "database": "not_checked", "problems": []}
    if not migrate_on_start():
        problems = _database_problems()
        out.update(ok=not problems, database="not_ready" if problems else "ready", problems=problems)
        if problems:
            response.status_code = 503
    return out


# The router is served twice: at /v1 (current) and, one release longer, at the old unversioned
# paths (deprecated). Explicit operation_ids would collide across the two includes (FastAPI copies
# route.operation_id verbatim into each include, so a generate_unique_id_function only takes effect
# when operation_id is unset) so each route's id instead comes from this table, keyed by its
# function name, via generate_unique_id_function on both includes.
_OPERATION_IDS = {
    "classify_endpoint": "classify",
    "consensus_endpoint": "consensus",
    "triage_endpoint": "triage",
    "tree_first_endpoint": "treeFirst",
    "assist_endpoint": "assist",
    "refine_endpoint": "refineAssist",
    "finalize_endpoint": "finalize",
    "full_text_search": "fullTextSearch",
    "get_naics": "getNaics",
    "search_naics": "searchNaics",
    "list_evals": "listEvals",
    "health": "getHealth",
    "get_reference": "getReference",
}


def _unique_id(route: Any) -> str:
    return _OPERATION_IDS[route.name]


def _legacy_unique_id(route: Any) -> str:
    return f"legacy_{_OPERATION_IDS[route.name]}"


app.include_router(v1_only, prefix="/v1", generate_unique_id_function=_unique_id)
app.include_router(router, prefix="/v1", generate_unique_id_function=_unique_id)
app.include_router(router, deprecated=True, include_in_schema=True, generate_unique_id_function=_legacy_unique_id)
app.add_middleware(CORSMiddleware, allow_origins=[os.environ.get("NAICS_WEB_ORIGIN", "http://127.0.0.1:8097")],
                   allow_methods=["GET", "POST"], allow_headers=["authorization", "content-type", "x-request-id"],
                   expose_headers=["x-request-id"])

_REQUEST_ID = re.compile(r"[A-Za-z0-9_-]{1,64}")


def request_id(sent: str | None) -> str:
    """The caller's X-Request-ID when it is 1-64 of [A-Za-z0-9_-] (so intake can correlate), else a
    new uuid4: a header that could forge or break a log line is never echoed."""
    return sent if sent and _REQUEST_ID.fullmatch(sent) else str(uuid.uuid4())


def _route_template(scope: dict) -> str:
    """The matched route's template (/v1/naics/{code}), never the raw path. FastAPI 0.142 keeps an
    included route's own path without its mount prefix, and this app mounts the router at "/v1" and
    at "" only, so the prefix is "/v1" exactly when the request path starts with it."""
    template = getattr(scope.get("route"), "path", None)
    if not template:
        return "<unmatched>"
    if scope.get("path", "").startswith("/v1/") and not template.startswith("/v1/"):
        return "/v1" + template
    return template


@app.middleware("http")  # added last, so outermost: CORS preflights get a request id and a line too
async def request_log(request: Request, call_next):
    """One JSON line per request: "classify" for a classify call (no description text), else
    "request" with the route template (never the raw path or query string). An unhandled error is
    an "error" line — the exception's type and the request id, never a message, since a request's
    own text (a description, a search query) could otherwise reach the log through it — and a 500
    that still carries the request id."""
    rid = request_id(request.headers.get("x-request-id"))
    start = time.perf_counter()
    try:
        response = await call_next(request)
    except Exception as exc:  # noqa: BLE001 - logged, answered 500 with the id; never re-raised
        logs.request_error(exc, request_id=rid)
        response = JSONResponse({"detail": "internal error", "request_id": rid}, status_code=500)
    response.headers["X-Request-ID"] = rid
    fields = dict(request.scope.get(_LOG_FIELDS) or {})
    name = fields.pop("event", "request")
    path = _route_template(request.scope)
    # A passing health check (every few seconds from Compose or a load balancer) is DEBUG, not noise.
    quiet = path in ("/v1/health", "/health") and response.status_code == 200
    logs.event(name, logs.logging.DEBUG if quiet else logs.logging.INFO, request_id=rid, method=request.method, path=path,
               status=response.status_code,
               latency_ms=round((time.perf_counter() - start) * 1000, 1), **fields)
    return response


def serve() -> None:
    # 127.0.0.1 by default; the container sets NAICS_HOST=0.0.0.0 and Compose publishes on 127.0.0.1 only.
    # Uvicorn's own access log is off: it prints the raw query string, which can carry a description.
    # Auth: refuse here too, before binding, so the message is the first thing printed.
    logs.configure()
    check_startup()
    # bind_host() is the same value the auth opt-out checks, so an empty NAICS_HOST cannot bind every
    # interface while the opt-out believes it is on loopback (SECURITY.md).
    # proxy_headers=False: uvicorn otherwise rewrites scope["client"] from X-Forwarded-For sent by a
    # loopback peer (its default forwarded_allow_ips), which could satisfy the opt-out's loopback check.
    uvicorn.run(app, host=bind_host(), port=8095, access_log=False, proxy_headers=False)
