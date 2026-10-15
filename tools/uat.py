"""AI-run UAT for jev-naics (JEV-55): real Chrome, headed, against the standalone stack.

Start the stack first: docker compose up -d --build (Postgres, the API on :8095, the web
page on :8097, each its own container). Then: .venv/Scripts/python tools/uat.py. Every step
asserts; a failure exits non-zero. Descriptions are the eval texts, so a stack without a
TypeSafe key replays recorded answers. Screenshots go to tools/out/uat/ (git-ignored).

Based on jev_peas packages/jev-naic-ops's tools/uat_naics.py, adapted for the split
frontend/backend containers: the page is served by its own nginx container on :8097 while
the API (and its Swagger docs) stays on :8095, so this also checks that the page's calls to
the API across that origin boundary do not trip CORS.
"""

import json
from pathlib import Path

from playwright.sync_api import expect, sync_playwright

BASE = "http://127.0.0.1:8097"
API = "http://127.0.0.1:8095"
OUT = Path(__file__).resolve().parents[1] / "tools" / "out" / "uat"
OUT.mkdir(parents=True, exist_ok=True)
DENTAL = "General and cosmetic dentistry practice with four dentists, cleanings, fillings, crowns, and implants."
VET = "Small-animal veterinary clinic providing vaccinations, spay and neuter surgery, and pet dental care."
report: dict[str, object] = {}

with sync_playwright() as pw:
    browser = pw.chromium.launch(channel="chrome", headless=False)
    page = browser.new_context(viewport={"width": 1400, "height": 1000}).new_page()
    errors: list[str] = []
    page.on("console", lambda m: errors.append(m.text) if m.type == "error" else None)
    page.on("pageerror", lambda e: errors.append(str(e)))
    page.goto(BASE)

    # Typed is the default, and so is search + tree fallback (JEV-53): search is sure here, so it is one
    # call over the search results and the tree walk never runs.
    expect(page.locator("#example")).to_be_hidden()
    expect(page.locator("#modeSeg button[data-mode=hybrid]")).to_have_attribute("aria-checked", "true")
    page.locator("#description").type(VET)
    page.click("#go")
    expect(page.locator("#outcome")).to_have_text("541940", timeout=60_000)
    expect(page.locator("#finalists-title")).to_have_text("What Jev picked among")
    expect(page.locator(".finalist.picked")).to_contain_text("Veterinary Services")
    expect(page.locator("#walk-card")).to_be_hidden()
    expect(page.locator("#gold")).to_be_hidden()
    # The page (:8097) called the API on a different origin (:8095): confirm CORS let it through.
    assert not any("cors" in e.lower() for e in errors), errors
    report["vet_search_pick"] = page.locator("#verdict-reason").inner_text()
    page.screenshot(path=str(OUT / "n01-search-pick-vet.png"), full_page=True)

    # Search alone: free, ranked, with the matching official words marked.
    page.click("#searchGo")
    expect(page.locator("#results .result").first).to_contain_text("541940")
    expect(page.locator("#results .result").first.locator(".snippet b").first).to_be_visible()
    report["vet_search_top3"] = page.locator("#results .result code").all_inner_texts()[:3]
    page.screenshot(path=str(OUT / "n02-search-results.png"), full_page=True)

    # JEV-52: typos are corrected, and the page says how the text was read.
    page.locator("#description").fill("vetrinary clinc for dogs and cats, vacinations and surgery")
    page.click("#searchGo")
    expect(page.locator("#results-notes")).to_contain_text('"vetrinary" read as "veterinary"')
    page.click("#go")
    expect(page.locator("#outcome")).to_have_text("541940", timeout=60_000)
    expect(page.locator("#verdict-notes")).to_contain_text("veterinary")
    report["typo_notes"] = page.locator("#verdict-notes").inner_text()
    page.locator("#description").fill(VET)

    # The tree walk on the same text holds (its known miss), and shows no answer path.
    page.click("#modeSeg button[data-mode=full]")
    page.click("#go")
    expect(page.locator("#outcome")).to_have_text("Hold", timeout=60_000)
    expect(page.locator("#crumbs")).to_have_class("crumbs diagnostic")
    expect(page.locator("#official")).to_be_hidden()
    expect(page.locator("#results-card")).to_be_hidden()

    # An example: the pet groomer on the tree walk, where the final pick picks a runner-up's industry.
    page.click("#sourceSeg button[data-source=example]")
    page.select_option("#example", "pet-groomer")
    expect(page.locator("#gold")).to_contain_text("812910")
    page.click("#go")
    expect(page.locator("#outcome")).to_have_text("812910", timeout=60_000)
    expect(page.locator("#crumbs li")).to_have_count(5)
    overflow = page.evaluate("""() => [...document.querySelectorAll('.opt')].some(
        o => o.scrollWidth > o.clientWidth + 1 || o.getBoundingClientRect().right > o.parentElement.getBoundingClientRect().right + 1)""")
    assert overflow is False, "option rows overflow their node box"

    # A vague description holds on search + pick, and says why.
    page.click("#modeSeg button[data-mode=search]")
    page.select_option("#example", "underspecified-ops")
    page.click("#go")
    expect(page.locator("#outcome")).to_have_text("Hold", timeout=60_000)
    report["vague"] = page.locator("#verdict-title").inner_text()
    # On a hold the candidate list is titled as not chosen, so its percentages don't read as a pick.
    expect(page.locator("#finalists-title")).to_have_text("Candidates it considered (none chosen)")
    expect(page.locator(".finalist.picked")).to_have_count(0)

    # Swagger deep link to classify, on the API's own origin (:8095), not the page's (:8097).
    page.goto(f"{API}/docs#/classify/classify")
    expect(page.locator(".opblock.is-open")).to_contain_text("Classify a description")

    # Phone width: typed description, search + pick, then search; nothing scrolls sideways.
    page.goto(BASE)
    page.set_viewport_size({"width": 390, "height": 844})
    page.locator("#description").type(DENTAL)
    page.click("#go")
    expect(page.locator("#outcome")).to_have_text("621210", timeout=60_000)
    rows_ok = page.evaluate("""() => [...document.querySelectorAll('.finalist .nums')].every(
        n => n.getBoundingClientRect().height < 60)""")
    page.click("#searchGo")
    expect(page.locator("#results .result").first).to_contain_text("621210")
    report["mobile_overflow"] = page.evaluate(
        "() => document.documentElement.scrollWidth > document.documentElement.clientWidth")
    page.screenshot(path=str(OUT / "n03-mobile.png"), full_page=True)
    assert report["mobile_overflow"] is False, "horizontal scroll on mobile"
    assert rows_ok, "finalist numbers wrap into a column on mobile"
    report["console_errors"] = errors
    assert not errors, errors
    browser.close()

print(json.dumps(report, indent=2))
