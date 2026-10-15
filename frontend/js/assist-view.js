// Live assist (the second tab): pure helpers, so node can test them. No DOM here.

const norm = (text) => String(text || "").trim().replace(/\s+/g, " ").toLowerCase();

// Call on a pause only when there are at least three words and the text changed since the last call.
// minWords is the method's gate: today's assist needs three words; refine checks any text of at least two
// characters (a single term like "SaaS" or "plumber" is a valid description; the completeness checks decide).
export function shouldCall(text, lastSent, minWords = 3) {
  const t = norm(text);
  return t.length >= 2 && t.split(" ").filter(Boolean).length >= minWords && t !== norm(lastSent);
}

export { norm };

// The meter: sector, industry group, NAICS code. A 6-digit code shows only when the status is CODED.
export function meter(r) {
  if (!r || r.status === "ERROR") return { hidden: true, filled: 0, label: "" };
  if (r.status === "CODED" && r.naics) {
    return { hidden: false, filled: 3, label: `${r.naics} ${r.naics_title || ""}`.trim() };
  }
  const partial = r.naics_partial || "";
  if (!partial) return { hidden: false, filled: 0, label: "Not enough to place it yet" };
  // An industry group is shown only when the text is good enough to walk; NEEDS_INFO shows the sector at most.
  if (r.level === 4 && r.status !== "NEEDS_INFO") {
    return { hidden: false, filled: 2, label: `${partial} ${r.partial_title || ""}`.trim() };
  }
  if (r.level === 2) return { hidden: false, filled: 1, label: `${partial} ${r.partial_title || ""}`.trim() };
  // Anything deeper than allowed is cut to its sector, without a title that would name the deeper code.
  const sector = partial.includes("-") ? partial : partial.slice(0, 2);
  return { hidden: false, filled: 1, label: sector };
}

export const STATUS_TEXT = {
  CODED: "Enough for a NAICS code",
  PARTIAL: "Placed, not yet a code",
  AMBIGUOUS: "Between two codes: answer one question",
  NEEDS_INFO: "Needs more detail",
  TOO_SHORT: "Too short to check",
};

// Fewer than three words is not an error and not vague text: nothing is asked, and the page says why. The
// same copy the API returns as the too_short tooltip (assist.py TOOLTIPS).
export const TOO_SHORT_TIP = "Add a few more words: say what the business does and for whom. Nothing is checked until the " +
  "description has at least three words.";

// The two engines the toggle chooses between: today's assist (tree walk) and the refine cascade (E10).
export const ENGINES = {
  assist: { path: "/assist", minWords: 3, body: (text) => ({ description: text }),
            candidates: "Industries the tree walk ended on",
            hint: "Type at least three words; the check runs when you pause." },
  refine: { path: "/refine/assist", minWords: 1, body: (text) => ({ description: text, variant: "cascade" }),
            candidates: "Industries the verify step judged",
            hint: "Type what the business does, or a term like SaaS; the check runs when you pause." },
};

// Refine mode's Submit: the refine result is the answer, with no second method. A code only when the result is
// CODED (never from a status that failed the completeness checks) or the broker answered the pick-list.
export function refineFinal(r, pick) {
  if (pick) return { gate: "auto_act", code: pick.code, title: null, source: "user_pick", reason: "The broker's answer." };
  if (r && r.status === "CODED" && r.naics) {
    return { gate: "auto_act", code: r.naics, title: r.naics_title, source: "refine", reason: r.reason };
  }
  return { gate: "hold", code: null, title: null, source: null, reason: (r && r.reason) || "No code.",
           status: r ? r.status : null };
}

export function finalSummary(f) {
  if (f.gate === "auto_act") {
    const from = f.source === "user_pick" ? "from the broker's pick-list answer"
      : f.source === "refine" ? "from the refine check (wide pick, then verify)" : "from the tree walk, checked by the hybrid";
    return `${f.code} ${f.title || ""} · ${from}`.replace("  ", " ");
  }
  if (f.reason && f.status !== undefined) return `Held for review: ${f.reason} The submission still goes through.`;
  const why = f.hold_reason === "methods_disagree" ? "the tree walk and the hybrid picked different codes"
    : f.hold_reason === "too_vague" ? "the description is too vague or mixed"
    : f.hold_reason ? f.hold_reason.replace(/_/g, " ") : "no code";
  return `Held for review: ${why}. The submission still goes through.`;
}

// What the broker sees, minus the numbers that move on every call: same key, no redraw of the layout.
export function viewKey(r) {
  if (!r) return "";
  const m = meter(r);
  return JSON.stringify([r.status, m.filled, m.label, r.tooltip_id || null, r.picklist ? r.picklist.id : null,
    (r.candidates || []).map((c) => c.code), (r.reading || []).map((x) => x.term)]);
}

// An error never replaces what is on screen; it hides the meter only when nothing was shown yet.
export function nextShown(shown, r) {
  return r && r.status === "ERROR" && shown ? shown : r;
}

// Fewer than three words: nothing to check; the meter goes back to empty.
export function tooShort(text, minWords = 3) {
  const t = norm(text);
  return t.length < 2 || t.split(" ").filter(Boolean).length < minWords;
}

// Recent results by normalized text, so deleting back to an earlier text shows its result with no call.
// Least recently used goes first; an error is never kept.
export class ResultCache {
  constructor(limit = 50) {
    this.limit = limit;
    this.map = new Map();
  }

  get(text) {
    const key = norm(text);
    if (!this.map.has(key)) return null;
    const value = this.map.get(key);
    this.map.delete(key);
    this.map.set(key, value);
    return value;
  }

  set(text, result) {
    if (!result || result.status === "ERROR") return;
    const key = norm(text);
    this.map.delete(key);
    this.map.set(key, result);
    while (this.map.size > this.limit) this.map.delete(this.map.keys().next().value);
  }

  get size() {
    return this.map.size;
  }
}

// The lexicon terms read in the text, as one line: what the user wrote and the Census wording it means.
export function readingLine(r) {
  const read = (r && r.reading) || [];
  if (!read.length) return "";
  return read.map((x) => (x.census ? `Read "${x.term}" as: ${x.census}.` : `Recognised "${x.term}".`)).join(" ");
}

// The code whose official description is shown: the level on the meter, never a 6-digit code unless CODED.
export function describedCode(r) {
  if (!r || r.status === "ERROR") return null;
  if (r.status === "CODED") return r.naics || null;
  if (r.level === 4 && r.status !== "NEEDS_INFO") return r.naics_partial || null;
  if (r.level === 2) return r.naics_partial || null;
  return null;
}
