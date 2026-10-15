"""Record the demo video and GIF (docs/media/demo.gif) against a running stack in replay mode (JEV-59).

Every answer on screen is a recorded real Jev answer: run the stack without TYPESAFE_API_KEY (or with
JEV_DECIDER=replay) so the page replays backend/transcripts/ and nothing is generated for the video.

    pip install playwright==1.56.0 && playwright install chromium     # not a project dependency
    docker compose up -d --build                                       # .env: NAICS_API_KEYS only
    python tools/record_demo.py [--base http://127.0.0.1:8097] [--out tools/out/demo]
    python tools/record_demo.py --videos clip-1-classify,clip-2-live-assist,clip-3-lexicon --size 1080x1080

Writes <out>/<video>.mp4 for each video asked for (the full demo also writes <out>/demo.gif, 800 wide, for the
README); the clips are one scene each, with their own short title card, for social posts;
needs ffmpeg on PATH. tools/out/ is git-ignored. Run it against `docker compose up` (the page reaches the
API through its own nginx, which adds the service key) with no TYPESAFE_API_KEY in .env, so answers replay.
"""
import argparse
import shutil
import subprocess
from pathlib import Path
from playwright.sync_api import sync_playwright

VET = "Small-animal veterinary clinic providing vaccinations, spay and neuter surgery, and pet dental care."
# Every text below is a recorded eval case (backend/evals/full_naics.json, assist_dev.json,
# refine_shortmodern.json), so replay answers it; anything else would be refused, never invented.
VAGUE_ASSIST = "Professional services for businesses."
HVAC = "We install and service commercial heating and air conditioning systems for office buildings."
CONFUSED = "We advise companies on cybersecurity, run penetration tests, and monitor their networks."
LEXICON_VARIATIONS = ["B2B SaaS", "HVAC", "CPA", "food truck", "rideshare", "Coworking"]
W, H = 1280, 720

CAPTION_CSS = """
#demo-cap{position:fixed;left:50%;top:6px;transform:translateX(-50%);z-index:99999;max-width:1040px;
 background:rgba(20,20,22,.92);color:#fff;font:600 21px/1.35 Inter,system-ui,sans-serif;padding:14px 22px;
 border-radius:12px;box-shadow:0 8px 30px rgba(0,0,0,.25);text-align:center;transition:opacity .3s}
#demo-cap small{display:block;font-weight:400;font-size:15px;opacity:.8;margin-top:4px}
#demo-card{position:fixed;inset:0;z-index:100000;background:#141416;color:#fff;display:flex;flex-direction:column;
 align-items:center;justify-content:center;font-family:Inter,system-ui,sans-serif;text-align:center;padding:40px}
#demo-card h1{font-size:46px;margin:0 0 18px;font-weight:700;letter-spacing:-.5px}
#demo-card p{font-size:24px;margin:6px 0;opacity:.9;max-width:980px;line-height:1.4}
#demo-card .mono{font-family:ui-monospace,Menlo,monospace;font-size:20px;opacity:.75;margin-top:26px}
"""

def caption(pg, text, sub=""):
    pg.evaluate("""([t,s]) => {let c=document.getElementById('demo-cap'); if(!c){c=document.createElement('div');c.id='demo-cap';document.body.appendChild(c);}
      c.innerHTML = t + (s ? '<small>'+s+'</small>' : ''); c.style.opacity=1;}""", [text, sub])

def card(pg, html):
    pg.evaluate("""h => {let c=document.getElementById('demo-card'); if(!c){c=document.createElement('div');c.id='demo-card';document.body.appendChild(c);} c.innerHTML=h; c.style.display='flex';}""", html)

def uncard(pg):
    pg.evaluate("() => {const c=document.getElementById('demo-card'); if(c) c.style.display='none';}")

def styled(pg):
    pg.add_style_tag(content=CAPTION_CSS)

def smooth_scroll(pg, y, ms=1200):
    pg.evaluate("([y]) => window.scrollTo({top:y,behavior:'smooth'})", [y]); pg.wait_for_timeout(ms)

def classify_scene(pg) -> None:
    """Classify: code searches, Jev picks from what code found, code decides."""
    caption(pg, "Classify: describe the business in plain words")
    pg.click("#tabClassify textarea"); pg.keyboard.type(VET, delay=16); pg.wait_for_timeout(400)
    caption(pg, "Search: Postgres full-text over the official Census text", "Code only. No model call. 1,012 industries narrowed to 10.")
    pg.get_by_role("button", name="Search", exact=True).click(); pg.wait_for_timeout(1200)
    smooth_scroll(pg, 330, 1200); pg.wait_for_timeout(1400); smooth_scroll(pg, 0, 700)
    caption(pg, "Jev picks one of the 10 that code found; code decides whether to act",
            "Probabilities, never text. NAICS files veterinarians under Professional Services, not Health Care.")
    pg.get_by_role("button", name="Classify", exact=True).click(); pg.wait_for_timeout(3600)
    smooth_scroll(pg, 560, 1400); pg.wait_for_timeout(1800); smooth_scroll(pg, 0, 700)


def assist(pg, text: str, wait: int, cap: str = "", sub: str = "") -> None:
    if cap:
        caption(pg, cap, sub)
    pg.fill("#assistText", ""); pg.click("#assistText"); pg.keyboard.type(text, delay=22)
    pg.wait_for_timeout(wait)


def assist_scene(pg) -> None:
    """Live assist on the tree walk: checks as the broker types (one call per typing pause)."""
    pg.click("#tabAssistBtn"); pg.wait_for_timeout(500)
    caption(pg, "Live assist: the description is checked as it is typed", "Each pause runs yes/no checks and the tree walk. Submit is never blocked.")
    assist(pg, VAGUE_ASSIST, 3800, "Too general: code shows one fixed tip on what to add", "The model answers yes/no checks; it never writes the tip.")
    assist(pg, HVAC, 3400, "Specific enough: the tree walk reaches a code")
    assist(pg, CONFUSED, 4200, "Between two codes people often confuse: a pick-list, not a guess")


def lexicon_scene(pg) -> None:
    """Refine with the lexicon: short and modern wording."""
    pg.click("#tabAssistBtn"); pg.wait_for_timeout(300)
    pg.select_option("#assistEngine", "refine"); pg.wait_for_timeout(400)
    caption(pg, "Short, modern wording: code reads it against a lexicon before Jev sees it",
            "A term maps to Census wording, never to a code. The lexicon is data (60 entries, still a draft), not code.")
    for text in LEXICON_VARIATIONS:
        assist(pg, text, 3100)


END_CARD = ("<h1>jev_naics</h1><p>PEAS spec, code-owned actuators, measured thresholds,"
            " and evals that count a hold as a pass only when one is expected.</p>"
            "<p class='mono'>github.com/jmurray10/jev_naics</p>"
            "<p style='font-size:16px;opacity:.6;margin-top:28px'>Recorded in replay mode: every answer shown is a real"
            " TypeSafe Jev answer, recorded on jev-1.13.0.</p>")
INTRO = ("<h1>jev_naics</h1><p>A business description in. One of 1,012 NAICS 2022 industries out,"
         " or a hold for a person.</p><p><b>No LLM in the runtime path.</b></p>")
# name: (title card, scenes). The full demo is every scene; each clip is one scene with its own short cards.
VIDEOS = {
    "jev_naics_demo": (INTRO, [classify_scene, assist_scene, lexicon_scene]),
    "clip-1-classify": ("<h1>Classify</h1><p>Code searches the Census text. Jev picks one of what code found."
                        " Code decides whether to act.</p><p><b>No LLM in the runtime path.</b></p>", [classify_scene]),
    "clip-2-live-assist": ("<h1>Live assist</h1><p>The description is checked as a broker types:"
                           " a tip, a code, or a pick-list.</p><p><b>No LLM in the runtime path.</b></p>", [assist_scene]),
    "clip-3-lexicon": ("<h1>SaaS, HVAC, CPA</h1><p>Short and modern wording, read against a lexicon"
                       " before the model sees it.</p><p><b>No LLM in the runtime path.</b></p>", [lexicon_scene]),
}

ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
ap.add_argument("--base", default="http://127.0.0.1:8097")
ap.add_argument("--out", default="tools/out/demo")
ap.add_argument("--videos", default="jev_naics_demo", help=f"comma-separated, from: {', '.join(VIDEOS)} (or 'all')")
ap.add_argument("--size", default="1280x720", help="WIDTHxHEIGHT, e.g. 1080x1080 for a square social clip")
args = ap.parse_args()
W, H = (int(x) for x in args.size.split("x"))
out = Path(args.out); out.mkdir(parents=True, exist_ok=True)
names = list(VIDEOS) if args.videos == "all" else args.videos.split(",")
ffmpeg = shutil.which("ffmpeg") or "ffmpeg"
with sync_playwright() as p:
    b = p.chromium.launch()
    for name in names:
        intro, scenes = VIDEOS[name]
        raw = out / "raw" / name; raw.mkdir(parents=True, exist_ok=True)
        ctx = b.new_context(viewport={"width": W, "height": H}, record_video_dir=str(raw),
                            record_video_size={"width": W, "height": H}, device_scale_factor=1,
                            bypass_csp=True)  # the captions are injected; the CSP (rightly) refuses inline style
        pg = ctx.new_page()
        pg.goto(args.base + "/"); styled(pg)
        card(pg, intro); pg.wait_for_timeout(3500 if name == "jev_naics_demo" else 2800); uncard(pg)
        for scene in scenes:
            scene(pg)
        if name == "jev_naics_demo":
            caption(pg, "A hold costs a person a minute. A wrong code misprices a policy and nobody notices.")
            pg.wait_for_timeout(3000)
        card(pg, END_CARD); pg.wait_for_timeout(4000 if name == "jev_naics_demo" else 3200)
        ctx.close()
        webm = max(raw.glob("*.webm"), key=lambda f: f.stat().st_mtime)
        subprocess.run([ffmpeg, "-v", "error", "-y", "-i", str(webm), "-c:v", "libx264", "-preset", "slow", "-crf", "20",
                        "-pix_fmt", "yuv420p", "-r", "30", "-movflags", "+faststart", str(out / f"{name}.mp4")], check=True)
        if name == "jev_naics_demo":
            subprocess.run([ffmpeg, "-v", "error", "-y", "-i", str(webm), "-vf",
                            "fps=7,scale=800:-1:flags=lanczos,split[s0][s1];[s0]palettegen=max_colors=96:stats_mode=diff[p];"
                            "[s1][p]paletteuse=dither=bayer:bayer_scale=4:diff_mode=rectangle", str(out / "demo.gif")], check=True)
        print(out / f"{name}.mp4")
    b.close()
