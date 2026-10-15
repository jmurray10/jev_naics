"""Build or check the NAICS search database (NAICS_DATABASE_URL).

    python -m jev_naics.db_cli build     migrate, then load (skipped when the data version is unchanged)
    python -m jev_naics.db_cli verify    counts, data version, and a known query; exit 1 on a problem
"""

from __future__ import annotations

import sys

from jev_naics import db, logs
from jev_naics.search_data import load_search_data


def main(argv: list[str]) -> int:
    if argv not in (["build"], ["verify"]):
        print(__doc__)
        return 2
    try:
        with db.connect() as conn:
            if argv == ["build"]:
                data = load_search_data()
                applied, loaded = db.migrate_and_load(conn, data)
                print("migrations applied:", applied or "none new")
                print("data loaded" if loaded else "data already current")
                logs.event("data_load", data_version=data.version, industries=len(data.industries),
                           reloaded=loaded, migrations_applied=applied)
            problems = db.verify(conn)
    except Exception as exc:  # noqa: BLE001 - one error line and exit 1, not a raw traceback
        logs.error(exc)  # no client text at this CLI: the redacted message is safe to log here
        return 1
    print("\n".join(problems) or "ready: 1012 industries, current data version, known query ok")
    return 1 if problems else 0


if __name__ == "__main__":
    logs.configure()
    raise SystemExit(main(sys.argv[1:]))
