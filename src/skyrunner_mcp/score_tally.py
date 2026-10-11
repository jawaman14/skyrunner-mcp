"""Read-only tally of the human-reviewed score log; never infer or grant awards."""
import argparse
import json
import os
from pathlib import Path
import re
import sys


def tally(text):
    total = counted = 0
    pending_events = set()
    row_ids, accepted_events = set(), set()
    columns = None
    found = False
    for line in text.splitlines():
        if not line.lstrip().startswith("|"):
            if columns is not None:
                break
            continue
        cells = [x.strip().replace("\\|", "|") for x in re.split(r"(?<!\\)\|", line.strip())[1:-1]]
        if {"#", "Team", "Who", "Score"}.issubset(cells):
            if found:
                raise ValueError("More than one score log found.")
            columns = cells
            found = True
            continue
        if columns is None or all(re.fullmatch(r":?-+:?", x) for x in cells):
            continue
        if len(cells) != len(columns):
            raise ValueError("Score row has the wrong number of columns.")
        row = dict(zip(columns, cells))
        identity = row["#"]
        if not identity or identity in row_ids:
            raise ValueError(f"Missing or duplicate row id: {identity!r}")
        row_ids.add(identity)
        if row["Who"] not in ("claude", "codex", "both"):
            raise ValueError(f"Row {identity}: invalid actor.")
        if row["Team"] not in ("+1", "+2", "-1", "-2", "0"):
            raise ValueError(f"Row {identity}: invalid point delta.")
        if not row.get("Evidence", "").strip():
            raise ValueError(f"Row {identity}: evidence is required.")
        event = row.get("Event ID", identity)
        if not event:
            raise ValueError(f"Row {identity}: event id is required.")
        if row["Score"] == "-":
            pending_events.add(event)
            continue
        if event in accepted_events:
            raise ValueError(f"Event {event!r} is counted twice; use a separate linked correction event.")
        if not re.fullmatch(r"[+-]?\d+", row["Score"]):
            raise ValueError(f"Row {identity}: invalid running score.")
        total += int(row["Team"])
        if int(row["Score"]) != total:
            raise ValueError(f"Row {identity}: recorded score {row['Score']}, computed score {total}.")
        accepted_events.add(event)
        counted += 1
    if not found:
        raise ValueError("No score log with #, Team, Who and Score columns found.")
    return {"team_score": total, "counted_events": counted,
            "pending_entries": len(pending_events - accepted_events)}


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("path", nargs="?", help="Score markdown; defaults to SKYRUNNER_ROOT/docs/COLLABORATION_SCORE.md")
    args = parser.parse_args(argv)
    try:
        if args.path:
            path = Path(args.path)
        else:
            root = os.environ.get("SKYRUNNER_ROOT", "")
            if not root or not Path(root).is_absolute():
                raise ValueError("Supply a path or an absolute SKYRUNNER_ROOT.")
            path = Path(root) / "docs" / "COLLABORATION_SCORE.md"
        result = tally(path.read_text(encoding="utf-8"))
        print(json.dumps({**result, "source": str(path.resolve())}, ensure_ascii=False))
    except (OSError, ValueError) as error:
        print(f"Score not tallied: {error}", file=sys.stderr)
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
