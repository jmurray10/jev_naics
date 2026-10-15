import { initAssist } from "./assist.js";
import { finalistsHeading } from "./labels.js";

// NAICS with Jev: search the official text, classify a description, and show how. Text goes in via
// textContent only; search snippets are split on Postgres's <b> marks, never parsed as HTML.

const $ = (id) => document.getElementById(id);
const pct = (x) => (x === null || x === undefined ? "–" : Math.round(x * 100) + "%");
const HOLD = {
  too_vague: "The description is too vague or mixes businesses.",
  low_probability: "Jev was not sure enough of one industry.",
  misfit: "Jev's best industry does not describe this business.",
  invalid_answer: "An answer was malformed, so nothing was acted on.",
  service_error: "The call failed.",
  no_match: "No industry's official text matches these words.",
};

function el(tag, cls, text) {
  const node = document.createElement(tag);
  if (cls) node.className = cls;
  if (text !== undefined && text !== null) node.textContent = String(text);
  return node;
}

async function api(method, path, body) {
  const res = await fetch(window.JEV_NAICS_API + "/v1" + path, {
    method,
    headers: body ? { "Content-Type": "application/json" } : {},
    body: body ? JSON.stringify(body) : undefined,
  });
  const payload = await res.json().catch(() => ({}));
  if (!res.ok) {
    const d = payload.detail;
    throw new Error(Array.isArray(d) ? d.map((x) => x.msg).join("; ") : d || res.statusText);
  }
  return payload;
}

let toastTimer;
function toast(message) {
  const t = $("toast");
  t.textContent = message;
  t.hidden = false;
  clearTimeout(toastTimer);
  toastTimer = setTimeout(() => { t.hidden = true; }, 6000);
}

let mode = "hybrid";
let examples = [];

function renderVerdict(r) {
  $("verdict").hidden = false;
  const acted = r.gate === "auto_act";
  const outcome = $("outcome");
  outcome.className = "outcome " + (acted ? "act" : "hold");
  outcome.textContent = acted ? r.code : "Hold";
  $("verdict-title").textContent = acted ? r.title : (HOLD[r.hold_reason] || r.hold_reason || "Hold");
  $("verdict-reason").textContent = r.reason;
  // On a hold there is no answer: no path is shown, and Jev's best path is labelled as not the answer.
  const crumbs = $("crumbs");
  const steps = acted ? r.path : r.model_path;
  crumbs.classList.toggle("diagnostic", !acted && steps.length > 0);
  crumbs.replaceChildren(...steps.map((s) => {
    const li = el("li");
    li.append(el("b", "", s.code), document.createTextNode(s.title), el("small", "", s.level));
    return li;
  }));
  const facts = [
    [finalistsHeading(r).factLabel, pct(r.probability)],
    r.path_score !== null && ["path score", pct(r.path_score)],
    r.fit_noul !== null && ["fits", pct(r.fit_noul)],
    ["vague", pct(r.underspecified_noul)],
    ["calls", r.calls],
    r.seconds && ["time", r.seconds.toFixed(1) + "s"],
    ["model", r.model || "–"],
  ].filter(Boolean);
  $("facts").replaceChildren(...facts.map(([k, v]) => {
    const span = el("span", "", k + " ");
    span.append(el("b", "", v));
    return span;
  }));
  const code = acted ? r.code : null;
  $("official").hidden = !code;
  if (code) {
    api("GET", "/naics/" + encodeURIComponent(code)).then((node) => {
      $("official").querySelector("summary").textContent = `Official Census description of ${node.code} ${node.title}`;
      $("official-text").textContent = node.description;
      $("official-terms").textContent = node.index_terms.length ? "Index terms: " + node.index_terms.join(" · ") : "";
    }).catch(() => {});
  }
}

function renderWalk(r) {
  $("walk-card").hidden = !r.trace.length;
  $("walk").replaceChildren(...r.trace.map((level) => {
    const kept = new Set(level.beam.map((b) => b.path[b.path.length - 1]));
    const box = el("div", "level");
    const head = el("p", "level-head",
      `Level ${level.level} · ${level.asked.length} node${level.asked.length === 1 ? "" : "s"} asked · kept: ` +
      level.beam.map((b) => `${b.path[b.path.length - 1]} (${pct(b.score)})`).join(", "));
    const nodes = el("div", "nodes");
    for (const asked of level.asked) {
      const node = el("div", "node");
      const title = el("p", "node-title");
      title.append(el("code", "", asked.node === "root" ? "all" : asked.node), document.createTextNode(asked.title));
      node.append(title);
      if (!asked.valid) node.append(el("p", "muted small", "Malformed answer: this path was dropped."));
      for (const opt of asked.top) {
        const row = el("div", "opt");
        const onBeam = level.beam.some((b) => b.path.includes(opt.code));
        if (onBeam || kept.has(opt.code)) row.classList.add("kept");
        const name = el("span", "name", opt.title);
        name.title = opt.title;
        const bar = el("div", "bar");
        const fill = el("span");
        fill.style.width = Math.max(1, Math.round(opt.p * 100)) + "%";
        bar.append(fill);
        const mid = el("div");
        mid.append(name, bar);
        row.append(el("code", "", opt.code), mid, el("span", "p", pct(opt.p)));
        node.append(row);
      }
      nodes.append(node);
    }
    box.append(head, nodes);
    return box;
  }));
}

function renderFinalists(r) {
  $("finalists-card").hidden = !r.finalists.length;
  const fromSearch = r.mode === "search" || r.mode === "hybrid";
  const heading = finalistsHeading(r);
  $("finalists-title").textContent = heading.title;
  $("finalists-note").textContent = heading.note;
  const picked = r.gate === "auto_act" ? r.code : null;
  $("finalists").replaceChildren(...r.finalists.map((f) => {
    const row = el("div", "finalist" + (f.code === picked ? " picked" : ""));
    const num = (label, v) => {
      const cell = el("span", "num", v);
      cell.append(el("small", "", label));
      return cell;
    };
    const nums = el("div", "nums");
    nums.append(num(heading.pickLabel, pct(f.p)),
      fromSearch ? num("search", f.search_rank ? "#" + f.search_rank : f.source === "tree" ? "tree" : "–") : num("path", pct(f.path_score)),
      num("fits", pct(f.fit)));
    row.append(el("code", "", f.code), el("span", "", f.title), nums);
    return row;
  }));
}

// A ts_headline snippet marks matches with <b>…</b>; build it as text nodes and <b> elements.
function snippet(text) {
  const out = el("p", "snippet");
  const parts = String(text || "").split(/(<b>|<\/b>)/);
  let bold = false;
  for (const part of parts) {
    if (part === "<b>") { bold = true; continue; }
    if (part === "</b>") { bold = false; continue; }
    if (part) out.append(bold ? el("b", "", part) : document.createTextNode(part));
  }
  return out;
}

// How search read the text (JEV-52): spelling corrections and modern terms, as plain text.
function renderNotes(target, notes) {
  const parts = [];
  for (const c of (notes && notes.corrections) || []) parts.push(`"${c.typed}" read as "${c.corrected}"`);
  for (const e of (notes && notes.expanded) || []) parts.push(`"${e.term}" read as "${e.census}"`);
  target.hidden = parts.length === 0;
  target.textContent = parts.length ? "Searched with: " + parts.join(" · ") : "";
}

async function runSearch() {
  const description = $("description").value.trim();
  if (!description) {
    toast("Type a description first.");
    return;
  }
  const button = $("searchGo");
  button.disabled = true;
  try {
    // No limit: the API returns the measured candidate count (SEARCH_K in questions.py).
    const body = await api("GET", "/naics/search?q=" + encodeURIComponent(description));
    const hits = body.hits;
    $("results-card").hidden = false;
    renderNotes($("results-notes"), body);
    $("results").replaceChildren(...(hits.length ? hits.map((h) => {
      const item = el("li", "result");
      item.append(el("code", "", h.code), el("span", "result-title", h.title), snippet(h.snippet));
      return item;
    }) : [el("li", "muted small", "No industry's official text matches these words.")]));
    $("results-card").scrollIntoView({ behavior: "smooth", block: "start" });
  } catch (error) {
    toast(error.message);
  } finally {
    button.disabled = false;
  }
}

async function classify() {
  const description = $("description").value.trim();
  if (!description) {
    toast("Type a description first.");
    return;
  }
  const button = $("go");
  button.disabled = true;
  button.textContent = mode === "full" ? "Walking the tree…" : mode === "search" || mode === "hybrid" ? "Searching, then asking Jev…" : "Asking Jev…";
  try {
    const r = await api("POST", "/classify", { description, mode });
    $("results-card").hidden = true;
    renderVerdict(r);
    renderNotes($("verdict-notes"), r.search_notes);
    renderWalk(r);
    renderFinalists(r);
  } catch (error) {
    toast(error.message);
  } finally {
    button.disabled = false;
    button.textContent = "Classify";
  }
}

$("modeSeg").addEventListener("click", (event) => {
  const button = event.target.closest("button");
  if (!button) return;
  mode = button.dataset.mode;
  for (const b of $("modeSeg").querySelectorAll("button")) b.setAttribute("aria-checked", String(b === button));
});

// Typed (default) classifies only what you write; an example is opt-in and shows its gold answer.
let loadedExample = null;
function setSource(source) {
  for (const b of $("sourceSeg").querySelectorAll("button")) {
    b.setAttribute("aria-checked", String(b.dataset.source === source));
  }
  $("example").hidden = source !== "example";
  if (source === "typed") {
    $("example").value = "";
    if (loadedExample) $("description").value = "";
    loadedExample = null;
    $("gold").hidden = true;
    $("description").focus();
  }
}
$("sourceSeg").addEventListener("click", (event) => {
  const button = event.target.closest("button");
  if (button) setSource(button.dataset.source);
});
$("go").addEventListener("click", classify);
$("description").addEventListener("input", () => {
  // Editing an example makes it your own description; its gold answer no longer applies.
  if (loadedExample && $("description").value !== loadedExample.ops_description) {
    loadedExample = null;
    $("example").value = "";
    $("gold").hidden = true;
    for (const b of $("sourceSeg").querySelectorAll("button")) {
      b.setAttribute("aria-checked", String(b.dataset.source === "typed"));
    }
    $("example").hidden = true;
  }
});
$("description").addEventListener("keydown", (event) => {
  if (event.key === "Enter" && (event.ctrlKey || event.metaKey)) classify();
});
$("searchGo").addEventListener("click", runSearch);
$("example").addEventListener("change", () => {
  const ex = examples.find((e) => e.id === $("example").value);
  if (!ex) return;
  loadedExample = ex;
  $("description").value = ex.ops_description;
  const gold = $("gold");
  gold.hidden = false;
  gold.textContent = ex.expect_hold ? "Gold answer: hold (too vague)." : `Gold answer: ${ex.expect_code}.`;
});

// Two tabs: Classify (every method so far) and Live assist. Switching keeps each tab's state.
for (const tab of document.querySelectorAll('[role="tab"]')) {
  tab.addEventListener("click", () => {
    for (const t of document.querySelectorAll('[role="tab"]')) {
      const on = t === tab;
      t.setAttribute("aria-selected", String(on));
      $(t.getAttribute("aria-controls")).hidden = !on;
    }
    if (tab.dataset.tab === "assist") $("assistText").focus();
  });
}
initAssist();

// The Swagger docs live on the API origin, not this static page's own origin.
document.querySelector(".api-link").href = window.JEV_NAICS_API + "/docs";

api("GET", "/evals").then((list) => {
  examples = list;
  for (const ex of list) {
    const opt = el("option", "", ex.id.replace(/-/g, " "));
    opt.value = ex.id;
    $("example").append(opt);
  }
}).catch((error) => toast(error.message));
