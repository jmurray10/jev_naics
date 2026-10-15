import {
  ENGINES, ResultCache, norm, STATUS_TEXT, TOO_SHORT_TIP, describedCode, finalSummary, meter, nextShown, refineFinal,
  readingLine, shouldCall, tooShort, viewKey,
} from "./assist-view.js";

// Live assist: on a typing pause, ask /v1/assist how specific a code the text supports, show the meter, the
// one missing thing, and a pick-list between confused codes. Submit calls /v1/finalize; it is never blocked.
// Text goes in via textContent only. An assist error hides the meter quietly; the broker never sees it.

const $ = (id) => document.getElementById(id);
const pct = (x) => (x === null || x === undefined ? "–" : Math.round(x * 100) + "%");
const PAUSE_MS = 700;

function el(tag, cls, text) {
  const node = document.createElement(tag);
  if (cls) node.className = cls;
  if (text !== undefined && text !== null) node.textContent = String(text);
  return node;
}

async function get(path) {
  const res = await fetch(window.JEV_NAICS_API + "/v1" + path);
  if (!res.ok) throw new Error(res.statusText);
  return res.json();
}

async function post(path, body, signal) {
  const res = await fetch(window.JEV_NAICS_API + "/v1" + path, {
    method: "POST", headers: { "Content-Type": "application/json" }, body: JSON.stringify(body), signal,
  });
  const payload = await res.json().catch(() => ({}));
  if (!res.ok) throw new Error(typeof payload.detail === "string" ? payload.detail : res.statusText);
  return payload;
}

let timer = null;
let inFlight = null;
let lastSent = "";
let lastText = ""; // the text `last` was computed for
let last = null; // the last result shown; kept until a newer one arrives
let shownKey = "";
const caches = { assist: new ResultCache(50), refine: new ResultCache(50) }; // one per method; no call when reused
let engine = "assist"; // the method the toggle chose: ENGINES in assist-view.js
const official = new Map(); // code -> its official text (GET /v1/naics/{code}, no model call)
let describedShown = null;
const minWords = () => ENGINES[engine].minWords;
const hint = () => ENGINES[engine].hint;
let pick = null; // the broker's pick-list answer: { picklist_id, code }

// The numbers under the meter: the same spans every time, only their text changes (no layout shift).
function renderFacts(r) {
  const c = r.checks || {};
  const facts = [
    ["operations", pct(c.is_operations_text)], ["specific", pct(c.states_activity)],
    ["customers", pct(c.states_customers)], ["one activity", pct(c.single_primary_activity)],
    ["delivery", c.delivery_mode ? c.delivery_mode.choice.replace(/_/g, " ") : "–"],
    ["calls", r.calls ?? "–"], ["time", r.seconds ? r.seconds.toFixed(2) + "s" : "–"],
  ];
  const box = $("assistFacts");
  if (box.children.length !== facts.length) {
    box.replaceChildren(...facts.map(([k]) => {
      const span = el("span", "", k + " ");
      span.append(el("b"));
      return span;
    }));
  }
  facts.forEach(([, v], i) => { box.children[i].lastChild.textContent = v; });
  const cands = r.candidates || [];
  for (const row of $("assistCands").children) {
    const f = cands.find((c) => c.code === row.dataset.code);
    if (f) row.lastChild.textContent = pct(f.p);
  }
}

function renderPick(r) {
  const box = $("assistPick");
  if (!r.picklist) {
    box.hidden = true;
    box.replaceChildren();
    delete box.dataset.id;
    pick = null;
    return;
  }
  box.hidden = false;
  if (box.dataset.id === r.picklist.id) return; // the same question: keep the broker's selection and focus
  box.dataset.id = r.picklist.id;
  pick = null;
  const note = el("p", "muted small");
  box.replaceChildren(el("p", "pick-q", r.picklist.question), ...r.picklist.options.map((o) => {
    const label = el("label", "pick-opt");
    const input = el("input");
    input.type = "radio";
    input.name = "pick-" + r.picklist.id;
    input.value = o.code;
    input.addEventListener("change", () => {
      pick = { picklist_id: r.picklist.id, code: o.code };
      note.textContent = `Your answer sets ${o.code}.`;
    });
    label.append(input, el("span", "", o.label), el("code", "", o.code));
    return label;
  }), note);
}

// The official Census description of the level on the meter, as on the Classify tab. Fetched only when that
// code changes; the row is always one line tall, so it does not move the page.
function renderOfficial(r) {
  const code = describedCode(r);
  if (code === describedShown) return;
  describedShown = code;
  const summary = $("assistOfficial").querySelector("summary");
  const show = (node) => {
    if (describedShown !== code) return; // a newer code arrived while this one was loading
    summary.textContent = node ? `Official Census description of ${node.code} ${node.title}` : "Official Census description (none yet)";
    $("assistOfficialText").textContent = node ? node.description : "";
    $("assistOfficialTerms").textContent = node && node.index_terms.length ? "Index terms: " + node.index_terms.join(" · ") : "";
  };
  if (!code) return show(null);
  if (official.has(code)) return show(official.get(code));
  get("/naics/" + encodeURIComponent(code)).then((node) => { official.set(code, node); show(node); }).catch(() => {});
}

// Back to the empty meter: the text was deleted or is under three words. Cancels any check in flight.
function reset() {
  if (inFlight) inFlight.abort();
  inFlight = null;
  $("assistChecking").classList.remove("on");
  if (!last && !lastSent) return;
  last = null;
  shownKey = "";
  lastSent = "";
  $("assistMeter").querySelectorAll(".step").forEach((s) => s.classList.remove("on"));
  delete $("assistMeter").dataset.status;
  $("assistStatus").textContent = "";
  $("assistLabel").textContent = hint();
  $("assistLabel").classList.add("muted");
  $("assistTip").classList.add("empty");
  $("assistTip").classList.remove("ok");
  $("assistTip").textContent = "";
  $("assistReading").textContent = "";
  renderPick({});
  delete $("assistPick").dataset.id;
  $("assistCandsCount").textContent = "(none yet)";
  $("assistCands").replaceChildren();
  renderFacts({});
  renderOfficial(null);
  $("assistFinal").hidden = true;
}

// Under three words with something typed: say why nothing is checked, in the tooltip line, not a faint hint.
function showTooShort(text) {
  if (!text.trim() || minWords() <= 1) return; // refine has no word-count gate: only a single character is too short
  const tip = $("assistTip");
  tip.classList.remove("empty", "ok");
  tip.textContent = TOO_SHORT_TIP;
  $("assistLabel").textContent = STATUS_TEXT.TOO_SHORT;
}

function render(incoming) {
  const r = nextShown(last, incoming);
  if (r !== incoming) return; // an error: keep what is on screen
  const key = viewKey(r);
  const changed = key !== shownKey;
  last = r;
  lastText = $("assistText").value;
  shownKey = key;
  const m = meter(r);
  if (m.hidden) {
    // Nothing shown yet and the check failed: stay on the empty meter, quietly, without moving anything.
    $("assistStatus").textContent = "";
    return;
  }
  if (changed) {
    $("assistLabel").classList.remove("muted");
    $("assistMeter").querySelectorAll(".step").forEach((s, i) => s.classList.toggle("on", i < m.filled));
    $("assistMeter").dataset.status = r.status;
    $("assistStatus").textContent = STATUS_TEXT[r.status] || "";
    $("assistLabel").textContent = m.label;
    // The tooltip line keeps its space when empty, so the page below it does not jump.
    // When coded there is nothing missing: the same line confirms the code (spec copy), so no gap is left.
    const tip = $("assistTip");
    const done = r.status === "CODED" && !r.tooltip ? `✓ ${r.naics_title} (${r.naics})` : "";
    tip.classList.toggle("empty", !r.tooltip && !done);
    tip.classList.toggle("ok", !!done);
    tip.textContent = r.tooltip || done;
    $("assistReading").textContent = readingLine(r);
    renderPick(r);
    renderOfficial(r);
    const cands = r.candidates || [];
    $("assistCandsCount").textContent = cands.length ? `(${cands.length})` : "(none yet)";
    $("assistCands").replaceChildren(...cands.map((f) => {
      const row = el("div", "finalist" + (f.code === r.naics ? " picked" : ""));
      row.dataset.code = f.code;
      row.append(el("code", "", f.code), el("span", "", f.title), el("span", "num", pct(f.p)));
      return row;
    }));
  }
  renderFacts(r);
}

async function evaluate() {
  const text = $("assistText").value;
  if (tooShort(text, minWords())) return (reset(), showTooShort(text));
  if (!shouldCall(text, lastSent, minWords())) return;
  if (inFlight) inFlight.abort();
  lastSent = text;
  const cached = caches[engine].get(text);
  if (cached) {
    inFlight = null;
    $("assistChecking").classList.remove("on");
    return render(cached);
  }
  const controller = new AbortController();
  inFlight = controller;
  $("assistChecking").classList.add("on");
  try {
    const r = await post(ENGINES[engine].path, ENGINES[engine].body(text.trim()), controller.signal);
    caches[engine].set(text, r);
    if (inFlight === controller) render(r);
  } catch (error) {
    if (error.name !== "AbortError" && inFlight === controller) render({ status: "ERROR" });
  } finally {
    if (inFlight === controller) {
      inFlight = null;
      $("assistChecking").classList.remove("on");
    }
  }
}

// Refine mode: Submit answers from the refine result itself (no tree-first call). If the text changed since the
// last check, it is checked first. A hold still lets the submission through.
async function submitRefine(text, out) {
  let r = last && norm(lastText) === norm(text) ? last : null;
  if (!r) r = caches.refine.get(text) || (tooShort(text, minWords()) ? { status: "TOO_SHORT", reason: TOO_SHORT_TIP }
    : await post(ENGINES.refine.path, ENGINES.refine.body(text)));
  caches.refine.set(text, r);
  const f = refineFinal(r, pick);
  out.hidden = false;
  out.className = "card final " + (f.gate === "auto_act" ? "act" : "hold");
  out.replaceChildren(el("p", "final-title", finalSummary(f)), el("p", "muted small",
    `Method: refine · calls: ${r.calls ?? 0} · status at submit: ${r.status}`));
}

async function submit() {
  const text = $("assistText").value.trim();
  if (!text) return;
  const button = $("assistSubmit");
  button.disabled = true;
  button.textContent = "Finalizing…";
  const out = $("assistFinal");
  try {
    if (engine === "refine") return await submitRefine(text, out);
    const f = await post("/finalize", pick ? { description: text, user_pick: pick } : { description: text });
    out.hidden = false;
    out.className = "card final " + (f.gate === "auto_act" ? "act" : "hold");
    const e = f.ensemble || {};
    const methods = e.methods || {};
    const side = (m) => (m ? (m.code || "hold" + (m.hold_reason ? ` (${m.hold_reason.replace(/_/g, " ")})` : "")) : "–");
    out.replaceChildren(el("p", "final-title", finalSummary(f)), el("p", "muted small",
      `Tree walk: ${side(methods.tree)} · hybrid: ${side(methods.hybrid)} · step: ${e.step || "–"} · ` +
      `${e.calls || 0} calls · status at submit: ${last ? last.status : "not assessed"}`));
  } catch (error) {
    out.hidden = false;
    out.className = "card final hold";
    out.replaceChildren(el("p", "final-title", "Could not finalize: " + error.message));
  } finally {
    button.disabled = false;
    button.textContent = "Submit";
  }
}

export function initAssist() {
  renderFacts({}); // the numbers row exists from the start, as dashes, so the first result moves nothing
  $("assistText").addEventListener("input", () => {
    clearTimeout(timer);
    $("assistFinal").hidden = true; // a submitted result belongs to the text that was submitted
    // Deleted below three words: reset now, not after the pause.
    if (tooShort($("assistText").value, minWords())) return (reset(), showTooShort($("assistText").value));
    timer = setTimeout(evaluate, PAUSE_MS);
  });
  $("assistEngine").addEventListener("change", () => {
    engine = $("assistEngine").value;
    $("assistCandsTitle").textContent = ENGINES[engine].candidates;
    $("assistLabel").textContent = hint();
    reset(); // the other method's result is not this method's
    const text = $("assistText").value;
    if (tooShort(text, minWords())) return showTooShort(text);
    clearTimeout(timer);
    evaluate();
  });
  $("assistSubmit").addEventListener("click", submit);
}
