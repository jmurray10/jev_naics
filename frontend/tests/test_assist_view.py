"""The live-assist tab's pure logic (js/assist-view.js): when to call, what the meter shows, and that a
NEEDS_INFO result never shows a 6-digit code. Run through node, like labels.js."""

import json
import shutil
import subprocess
from pathlib import Path

import pytest

WEB = Path(__file__).resolve().parents[1]
pytestmark = pytest.mark.skipif(shutil.which("node") is None, reason="node is not installed")


def _call(name: str, *args):
    module = (WEB / "js" / "assist-view.js").resolve().as_uri()
    code = (f"import * as v from {json.dumps(module)};\n"
            f"console.log(JSON.stringify(v.{name}(...{json.dumps(list(args))})));")
    out = subprocess.run(["node", "--input-type=module", "-e", code], capture_output=True, text=True, encoding="utf-8")
    assert out.returncode == 0, out.stderr
    return json.loads(out.stdout)


def test_it_calls_only_on_three_words_and_changed_text() -> None:
    assert _call("shouldCall", "car wash", "") is False
    assert _call("shouldCall", "self service car wash", "") is True
    assert _call("shouldCall", "Self  service car wash ", "self service car wash") is False


def test_the_meter_fills_to_the_level_the_text_supports() -> None:
    coded = _call("meter", {"status": "CODED", "level": 6, "naics": "811192", "naics_title": "Car Washes",
                            "naics_partial": "811192", "partial_title": "Car Washes"})
    assert (coded["filled"], coded["label"]) == (3, "811192 Car Washes")
    group = _call("meter", {"status": "PARTIAL", "level": 4, "naics": None, "naics_partial": "5416",
                            "partial_title": "Management, Scientific, and Technical Consulting Services"})
    assert group["filled"] == 2 and group["label"].startswith("5416 ")
    sector = _call("meter", {"status": "NEEDS_INFO", "level": 2, "naics": None, "naics_partial": "51",
                             "partial_title": "Information"})
    assert (sector["filled"], sector["label"]) == (1, "51 Information")


def test_needs_info_never_shows_a_six_digit_code_even_if_one_is_sent() -> None:
    m = _call("meter", {"status": "NEEDS_INFO", "level": 6, "naics": "561311", "naics_partial": "561311",
                        "partial_title": "Employment Placement Agencies"})
    assert m["filled"] <= 1 and "561311" not in m["label"]


def test_an_error_hides_the_meter_quietly() -> None:
    assert _call("meter", {"status": "ERROR"})["hidden"] is True


def test_the_final_summary_names_the_source() -> None:
    assert _call("finalSummary", {"gate": "auto_act", "code": "513210", "title": "Software Publishers",
                                  "source": "user_pick", "ensemble": {"step": "broker"}}) == \
        "513210 Software Publishers · from the broker's pick-list answer"
    held = _call("finalSummary", {"gate": "hold", "code": None, "source": None, "hold_reason": "methods_disagree",
                                  "ensemble": {"step": "broker"}})
    assert held.startswith("Held for review")


def test_a_result_that_shows_the_same_thing_does_not_redraw() -> None:
    a = {"status": "CODED", "level": 6, "naics": "513210", "naics_title": "Software Publishers", "tooltip_id": None,
         "picklist": None, "candidates": [{"code": "513210", "p": 0.98}], "checks": {"states_activity": 0.97}}
    b = {**a, "candidates": [{"code": "513210", "p": 0.97}], "checks": {"states_activity": 0.95}, "seconds": 0.9}
    assert _call("viewKey", a) == _call("viewKey", b)
    assert _call("viewKey", a) != _call("viewKey", {**a, "tooltip_id": "states_customers"})


def test_an_error_keeps_the_last_result_on_screen() -> None:
    shown = {"status": "PARTIAL", "level": 4, "naics_partial": "5415"}
    assert _call("nextShown", shown, {"status": "ERROR"}) == shown
    assert _call("nextShown", None, {"status": "ERROR"}) == {"status": "ERROR"}
    assert _call("nextShown", shown, {"status": "CODED", "naics": "541512"})["status"] == "CODED"


def _run(code: str):
    module = (WEB / "js" / "assist-view.js").resolve().as_uri()
    out = subprocess.run(["node", "--input-type=module", "-e", f"import * as v from {json.dumps(module)};\n{code}"],
                         capture_output=True, text=True, encoding="utf-8")
    assert out.returncode == 0, out.stderr
    return json.loads(out.stdout)


def test_deleting_below_three_words_resets_the_meter() -> None:
    assert _call("tooShort", "") is True
    assert _call("tooShort", "  car  wash ") is True
    assert _call("tooShort", "self service car wash") is False


def test_deleting_back_to_earlier_text_reuses_its_result_without_a_call() -> None:
    got = _run("""
const c = new v.ResultCache(2);
c.set("We wash cars by hand", {status: "CODED"});
c.set("We wash cars", {status: "PARTIAL"});
const back = c.get("  we wash CARS by hand ");
c.set("Third text here", {status: "NEEDS_INFO"});
console.log(JSON.stringify([back, c.get("We wash cars"), c.get("We wash cars by hand"), c.size]));""")
    # The oldest unused entry goes first; reading an entry keeps it.
    assert got == [{"status": "CODED"}, None, {"status": "CODED"}, 2]


def test_errors_are_never_cached() -> None:
    got = _run("""
const c = new v.ResultCache(5);
c.set("some failed text", {status: "ERROR"});
console.log(JSON.stringify(c.get("some failed text")));""")
    assert got is None


def test_the_description_shown_is_the_level_on_the_meter() -> None:
    assert _call("describedCode", {"status": "CODED", "naics": "238220", "naics_partial": "238220", "level": 6}) == "238220"
    assert _call("describedCode", {"status": "PARTIAL", "naics_partial": "5415", "level": 4}) == "5415"
    assert _call("describedCode", {"status": "NEEDS_INFO", "naics_partial": "51", "level": 2}) == "51"
    assert _call("describedCode", {"status": "NEEDS_INFO", "naics_partial": "561311", "level": 6}) is None
    assert _call("describedCode", {"status": "ERROR"}) is None


def test_refine_submit_codes_only_a_coded_result_or_the_brokers_pick() -> None:
    coded = _call("refineFinal", {"status": "CODED", "naics": "811192", "naics_title": "Car Washes", "reason": "r"}, None)
    assert (coded["gate"], coded["code"], coded["source"]) == ("auto_act", "811192", "refine")
    for status in ("NEEDS_INFO", "PARTIAL", "AMBIGUOUS", "TOO_SHORT", "ERROR"):
        held = _call("refineFinal", {"status": status, "naics": "811192", "reason": "why"}, None)
        assert held["gate"] == "hold" and held["code"] is None, status
    picked = _call("refineFinal", {"status": "AMBIGUOUS", "reason": "x"}, {"picklist_id": "p", "code": "541511"})
    assert (picked["gate"], picked["code"], picked["source"]) == ("auto_act", "541511", "user_pick")
    assert _call("refineFinal", None, None)["gate"] == "hold"


def test_the_refine_summary_names_its_source_and_a_hold_says_why() -> None:
    coded = _call("refineFinal", {"status": "CODED", "naics": "811192", "naics_title": "Car Washes", "reason": "r"}, None)
    assert _call("finalSummary", coded) == "811192 Car Washes · from the refine check (wide pick, then verify)"
    held = _call("refineFinal", {"status": "NEEDS_INFO", "reason": "The text does not yet say enough."}, None)
    assert _call("finalSummary", held).startswith("Held for review: The text does not yet say enough.")
    assert _call("finalSummary", held).endswith("The submission still goes through.")


def test_the_too_short_copy_matches_the_api_and_has_its_own_status() -> None:
    tip = _call("meter", {"status": "TOO_SHORT"})
    assert (tip["hidden"], tip["filled"]) == (False, 0)
    assert _call("tooShort", "licensing fees") is True
    module = (WEB / "js" / "assist-view.js").resolve().as_uri()
    code = (f"import * as v from {json.dumps(module)};\n"
            "console.log(JSON.stringify([v.TOO_SHORT_TIP, v.STATUS_TEXT.TOO_SHORT]));")
    out = subprocess.run(["node", "--input-type=module", "-e", code], capture_output=True, text=True, encoding="utf-8")
    copy, status = json.loads(out.stdout)
    backend = (WEB.parent / "backend" / "src" / "jev_naics" / "assist.py").read_text(encoding="utf-8")
    assert '"too_short": "' + copy.split(" Nothing is checked")[0] in backend.replace('"\n                 "', "")
    assert status == "Too short to check"


def test_each_method_posts_to_its_own_endpoint_and_refine_asks_for_the_cascade() -> None:
    module = (WEB / "js" / "assist-view.js").resolve().as_uri()
    code = (f"import {{ ENGINES as E }} from {json.dumps(module)};\n"
            "console.log(JSON.stringify([E.assist.path, E.assist.body('t'), E.refine.path, E.refine.body('t')]));")
    out = subprocess.run(["node", "--input-type=module", "-e", code], capture_output=True, text=True, encoding="utf-8")
    assert json.loads(out.stdout) == ["/assist", {"description": "t"}, "/refine/assist",
                                      {"description": "t", "variant": "cascade"}]


def test_refine_has_no_word_count_gate_but_still_ignores_a_single_character() -> None:
    assert _call("shouldCall", "SaaS", "", 1) is True and _call("shouldCall", "SaaS", "") is False  # today's assist: 3 words
    assert _call("shouldCall", "a", "", 1) is False and _call("tooShort", "a", 1) is True
    assert _call("tooShort", "plumber", 1) is False and _call("tooShort", "plumber") is True
    assert _call("shouldCall", "plumber", "plumber", 1) is False  # unchanged text is not asked again


def test_the_page_says_which_terms_it_read_and_redraws_when_they_change() -> None:
    saas = {"status": "CODED", "naics": "513210", "naics_title": "Software Publishers", "reading": [
        {"term": "saas", "census": "software publishers software subscriptions"}]}
    assert _call("readingLine", saas) == 'Read "saas" as: software publishers software subscriptions.'
    assert _call("readingLine", {"reading": [{"term": "fintech", "census": ""}]}) == 'Recognised "fintech".'
    assert _call("readingLine", {"status": "CODED"}) == "" and _call("readingLine", None) == ""
    plain = {k: v for k, v in saas.items() if k != "reading"}
    assert _call("viewKey", saas) != _call("viewKey", plain)


def test_each_method_carries_its_own_gate_and_hint() -> None:
    module = (WEB / "js" / "assist-view.js").resolve().as_uri()
    code = (f"import {{ ENGINES as E }} from {json.dumps(module)};\n"
            "console.log(JSON.stringify([E.assist.minWords, E.refine.minWords, E.assist.hint, E.refine.hint]));")
    out = subprocess.run(["node", "--input-type=module", "-e", code], capture_output=True, text=True, encoding="utf-8")
    a, r, ha, hr = json.loads(out.stdout)
    assert (a, r) == (3, 1) and "three words" in ha and "SaaS" in hr
