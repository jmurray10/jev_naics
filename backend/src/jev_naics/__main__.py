"""CLI: classify an ops description (full walk, search, or hybrid), or run an eval."""

from __future__ import annotations

import argparse
import json
from typing import Sequence

from jev_naics._jev import decider_for

from jev_naics.env import load_typesafe_api_key


def main(argv: Sequence[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        description="NAICS from ops description via TypeSafe Jev."
    )
    parser.add_argument("--text", help="Operations description to classify (requires --full)")
    parser.add_argument("--full", action="store_true", help="With --text: classify over all of NAICS 2022 (JEV-48)")
    parser.add_argument("--full-eval", dest="full_eval", action="store_true",
                        help="Full-NAICS eval on evals/full_naics.json (JEV-48)")
    parser.add_argument("--search-eval", dest="search_eval", action="store_true",
                        help="Search-then-pick eval (JEV-50); with NAICS_DATABASE_URL it searches the database "
                             "and saves the results for offline replay")
    parser.add_argument("--heldout", action="store_true",
                        help="With --search-eval: also run the blind messy held-out set (JEV-52). Run it once, "
                             "after the search is final; its search results are saved to their own file")
    parser.add_argument("--hybrid-eval", dest="hybrid_eval", action="store_true",
                        help="Search first, tree walk as a fallback (JEV-53), on the dev and seen sets; with "
                             "--heldout also the JEV-53 blind set (run once, when the hybrid is final)")
    parser.add_argument("--serve", action="store_true", help="Run the API on :8095 (JEV-48)")
    parser.add_argument("--openapi", action="store_true",
                        help="Print the API's OpenAPI schema as JSON (JEV-55); redirect to backend/openapi.json")
    parser.add_argument("--export-reference", dest="export_reference", action="store_true",
                        help="Print the NAICS 2022 reference data (every industry's code and title, the 2017 -> "
                             "2022 concordance, and their version) as JSON, the same as GET /v1/naics/reference "
                             "(JEV-56). No model call, no database")
    args = parser.parse_args(argv)

    if args.export_reference:
        import sys

        from jev_naics.tree import reference

        # Bytes, so a Windows redirect writes LF, not CRLF.
        sys.stdout.buffer.write((json.dumps(reference(), indent=1, sort_keys=True) + "\n").encode("utf-8"))
        sys.stdout.flush()
        return 0
    if not (args.text or args.full_eval or args.search_eval or args.hybrid_eval or args.serve or args.openapi):
        parser.print_help()
        return 2
    if args.text and not args.full:
        parser.error("--text requires --full")

    # With a key the default decider is live Jev; without one it replays recorded answers.
    load_typesafe_api_key()

    if args.search_eval:
        import os

        from jev_naics import db
        from jev_naics.api.app import get_searcher
        from jev_naics.full_eval import HELDOUT
        from jev_naics.search_data import load_search_data
        from jev_naics.search_eval import (
            HELDOUT_CANDIDATES,
            MESSY_DEV,
            MESSY_HELDOUT,
            SEARCH_HELDOUT,
            eval_descriptions,
            print_search_report,
            save_candidates,
            saved_searcher,
            search_report,
        )
        from jev_naics.search_pick import SEARCH_TRANSCRIPT

        if os.environ.get("NAICS_DATABASE_URL", "").strip():
            with db.connect() as conn:
                db.apply_migrations(conn)
                db.load_search(conn, load_search_data())
            save_candidates(get_searcher())
            if args.heldout:
                save_candidates(get_searcher(), eval_descriptions((MESSY_HELDOUT,)), HELDOUT_CANDIDATES)
        searcher, decider = saved_searcher(), decider_for(SEARCH_TRANSCRIPT)
        reports = [("dev", search_report(decider, searcher)),
                                   ("JEV-48 held-out (informed K and ranking)", search_report(decider, searcher, HELDOUT)),
                                   ("fresh held-out", search_report(decider, searcher, SEARCH_HELDOUT)),
                   ("messy dev", search_report(decider, searcher, MESSY_DEV))]
        if args.heldout:
            reports.append(("messy blind held-out",
                            search_report(decider, saved_searcher(HELDOUT_CANDIDATES), MESSY_HELDOUT)))
        return print_search_report(*reports)

    if args.hybrid_eval:
        import os

        from jev_naics import db
        from jev_naics.api.app import get_searcher
        from jev_naics.full_eval import FULL_EVALS, HELDOUT
        from jev_naics.hybrid import HYBRID_TRANSCRIPT
        from jev_naics.search_eval import (
            HELDOUT_CANDIDATES,
            HYBRID_CANDIDATES,
            HYBRID_HELDOUT,
            MESSY_DEV,
            MESSY_HELDOUT,
            SEARCH_HELDOUT,
            eval_descriptions,
            hybrid_report,
            print_search_report,
            save_candidates,
            saved_searcher,
        )

        if args.heldout and os.environ.get("NAICS_DATABASE_URL", "").strip():
            with db.connect() as conn:
                db.apply_migrations(conn)
            save_candidates(get_searcher(), eval_descriptions((HYBRID_HELDOUT,)), HYBRID_CANDIDATES)
        searcher, decider = saved_searcher(), decider_for(HYBRID_TRANSCRIPT)
        reports = [("dev", hybrid_report(decider, searcher, FULL_EVALS)),
                   ("messy dev", hybrid_report(decider, searcher, MESSY_DEV)),
                   ("JEV-48 held-out (seen)", hybrid_report(decider, searcher, HELDOUT)),
                   ("fresh held-out (seen)", hybrid_report(decider, searcher, SEARCH_HELDOUT)),
                   ("messy blind held-out (seen: it motivated JEV-53)",
                    hybrid_report(decider, saved_searcher(HELDOUT_CANDIDATES), MESSY_HELDOUT))]
        if args.heldout:
            reports.append(("JEV-53 blind held-out",
                            hybrid_report(decider, saved_searcher(HYBRID_CANDIDATES), HYBRID_HELDOUT)))
        return print_search_report(*reports)

    if args.openapi:
        from jev_naics.api.app import app

        print(json.dumps(app.openapi(), indent=1, sort_keys=True))
        return 0
    if args.serve:
        from jev_naics.api.app import serve

        serve()
        return 0
    if args.full_eval or (args.text and args.full):
        from jev_naics.full import FULL_TRANSCRIPT, classify_full
        from jev_naics.full_eval import HELDOUT, full_report, print_full_report

        if args.full_eval:
            decider = decider_for(FULL_TRANSCRIPT)
            return print_full_report(("dev", full_report(decider)), ("held-out", full_report(decider, HELDOUT)))
        result = classify_full(args.text, decider=decider_for(FULL_TRANSCRIPT))
        print(json.dumps({k: v for k, v in result.__dict__.items() if k != "trace"}, indent=2))
        return 0

    parser.error("nothing to do")
    return 2


if __name__ == "__main__":
    raise SystemExit(main())
