// Headings for the candidate list (JEV-57 demo review). On a hold nothing was chosen, so the list is titled as
// candidates: a percentage under a hold must not read as "it is that sure". Pure, so node can test it.
export function finalistsHeading(r) {
  const fromSearch = r.mode === "search" || r.mode === "hybrid";
  const fellBack = r.mode === "hybrid" && r.fallback;
  const note = fellBack
    ? "Search was unsure, so the tree walk ran and its end points joined the search results (listed last). Jev read each one's official description, examples, index entries, and what it is not, picked one, and checked each one's fit."
    : fromSearch
    ? "The top search results. Jev read each one's official description, examples, index entries, and what it is not, picked one in a single call, and checked each one's fit."
    : "The walk's end points. One final pick chooses among them with each industry's description and index terms; each also gets a fit check.";
  if (r.gate !== "auto_act") {
    return {
      title: "Candidates it considered (none chosen)",
      note: "Held for a person, so no code was assigned. These are the industries Jev weighed"
        + (fromSearch ? " from the top search results" : " at the end of the tree walk")
        + "; the lean column is a diagnostic, not a suggestion.",
      pickLabel: "lean",
      factLabel: "lean",
    };
  }
  return { title: fromSearch ? "What Jev picked among" : "Finalists", note, pickLabel: "final pick", factLabel: "pick" };
}
