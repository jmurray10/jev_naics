# Copied from jev_peas 9705080650cb52ba56a337e12eb7bee24f2b3777 packages/jev-common/src/jev_common/agent_config.py; keep in sync by hand (CHANGELOG.md).
"""Load a peas-style agent.yaml that puts Jev in the action-selection slot.

Same keys as peas `00-config-runtime/agents/<name>/agent.yaml` (use_case, architecture,
performance, environment, sensors, actuators), plus a `judgment` block naming the one
component Jev replaces. Hold and ask are escalation actuators owned by code: they are
declared here so the PEAS spec is complete, and they must never be a Choice option.
"""

from __future__ import annotations

from collections.abc import Iterable, Mapping
from dataclasses import dataclass
from pathlib import Path
from typing import Any

import yaml

ARCHITECTURES = frozenset(
    {"simple-reflex", "model-based-reflex", "goal-based", "utility", "learning"}
)
ACTUATOR_TYPES = frozenset({"deterministic", "escalation"})
DECIDERS = frozenset({"jev", "replay", "claude"})
# peas lists five components an LLM can power; Jev only ever takes this one.
JUDGMENT_COMPONENTS = frozenset({"action-selection"})
USE_CASE_KEYS = ("domain", "decision", "why_it_matters", "boundary")


class AgentConfigError(ValueError):
    """agent.yaml does not describe a complete, closed PEAS agent."""


@dataclass(frozen=True)
class AgentConfig:
    path: Path
    raw: Mapping[str, Any]

    @property
    def name(self) -> str:
        return str(self.raw["name"])

    @property
    def architecture(self) -> str:
        return str(self.raw["architecture"])

    @property
    def judgment(self) -> Mapping[str, Any]:
        return self.raw["judgment"]

    @property
    def actuators(self) -> tuple[Mapping[str, Any], ...]:
        return tuple(self.raw["actuators"])

    @property
    def model_actuators(self) -> tuple[str, ...]:
        """Actuators Jev may choose. peas types some of these as escalation
        (refer_to_siu); only `owner: code` takes an actuator out of the model's hands."""
        return tuple(a["name"] for a in self.actuators if a.get("owner") != "code")

    @property
    def code_owned_actuators(self) -> tuple[str, ...]:
        return tuple(a["name"] for a in self.actuators if a.get("owner") == "code")

    @property
    def eval_path(self) -> Path:
        return self.path.parent / self.raw["performance"]["eval"]

    def assert_choice_matches_actuators(self, options: Iterable[str]) -> None:
        """The Choice Jev answers is exactly the model-chosen actuators, nothing more."""
        offered = list(options)
        leaked = sorted(set(offered) & set(self.code_owned_actuators))
        if leaked:
            raise AgentConfigError(f"code-owned actuators offered to the model: {leaked}")
        expected = set(self.model_actuators)
        if set(offered) != expected or len(offered) != len(set(offered)):
            extra = sorted(set(offered) - expected)
            missing = sorted(expected - set(offered))
            raise AgentConfigError(
                f"Choice options differ from actuators: extra={extra} missing={missing}"
            )


def _require(mapping: Mapping[str, Any], key: str, where: str) -> Any:
    if not isinstance(mapping, Mapping) or key not in mapping:
        raise AgentConfigError(f"{where}: missing `{key}`")
    return mapping[key]


def load_agent(path: Path) -> AgentConfig:
    doc = yaml.safe_load(Path(path).read_text(encoding="utf-8"))
    agent = _require(doc, "agent", str(path))
    for key in ("name", "use_case", "architecture", "performance", "environment",
                "sensors", "actuators", "judgment"):
        _require(agent, key, "agent")
    for key in USE_CASE_KEYS:
        _require(agent["use_case"], key, "agent.use_case")
    if agent["architecture"] not in ARCHITECTURES:
        raise AgentConfigError(
            f"agent.architecture {agent['architecture']!r} is not one of {sorted(ARCHITECTURES)}"
        )
    _require(agent["performance"], "metrics", "agent.performance")
    eval_rel = _require(agent["performance"], "eval", "agent.performance")
    if not (Path(path).parent / eval_rel).is_file():
        raise AgentConfigError(f"agent.performance.eval file not found: {eval_rel}")
    _require(agent["environment"], "type", "agent.environment")
    if not agent["sensors"]:
        raise AgentConfigError("agent.sensors: at least one sensor is required")

    names: list[str] = []
    for actuator in agent["actuators"]:
        name = _require(actuator, "name", "agent.actuators[]")
        kind = _require(actuator, "type", f"actuator {name}")
        if kind not in ACTUATOR_TYPES:
            raise AgentConfigError(f"actuator {name}: type {kind!r} not in {sorted(ACTUATOR_TYPES)}")
        owner = actuator.get("owner")
        if owner is not None and owner != "code":
            raise AgentConfigError(f"actuator {name}: owner must be `code` when set, got {owner!r}")
        names.append(name)
    dupes = sorted({n for n in names if names.count(n) > 1})
    if dupes:
        raise AgentConfigError(f"duplicate actuator names: {dupes}")

    judgment = agent["judgment"]
    component = _require(judgment, "component", "agent.judgment")
    if component not in JUDGMENT_COMPONENTS:
        raise AgentConfigError(
            f"agent.judgment.component {component!r}: Jev only replaces {sorted(JUDGMENT_COMPONENTS)}"
        )
    decider = _require(judgment, "decider", "agent.judgment")
    if decider not in DECIDERS:
        raise AgentConfigError(f"agent.judgment.decider {decider!r} not in {sorted(DECIDERS)}")
    _require(judgment, "questions", "agent.judgment")
    return AgentConfig(path=Path(path), raw=agent)
