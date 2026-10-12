"""Read-only diagnostic index. A log summary is never a job-success verdict."""
from __future__ import annotations

import argparse
import json
from itertools import islice
from pathlib import Path
import re
import sys

ANSI = re.compile(r"\x1b\[[0-?]*[ -/]*[@-~]")
DIAGNOSTIC = re.compile(
    r"SCRIPT ERROR:|(?:^|\s)(?:ERROR|WARNING|FAIL(?:ED)?)(?:\s|:)|"
    r"Parse Error:|(?:Assertion|Runtime|Syntax|Memory)Error:|"
    r"Traceback \(most recent call last\):|##\[error\]|fatal exception|out of memory|0xC0000374",
    re.IGNORECASE,
)
SUMMARY = re.compile(r"\b(\d+) passed, (\d+) failed in ([\d.]+) s\b")
LOCATION = re.compile(r"^\s*(?:at:|File \"|res://|Caused by:)")


def digest(text: str, limit: int = 30) -> dict:
    """Retain complete diagnostic text and immediate locations; bound group count only."""
    if limit < 1:
        raise ValueError("limit must be positive")
    lines = [ANSI.sub("", line) for line in text.splitlines()]
    groups: dict[tuple, dict] = {}
    summaries = []
    diagnostic_lines = 0
    for index, line in enumerate(lines):
        match = SUMMARY.search(line)
        if match:
            summaries.append({"line": index + 1, "passed": int(match[1]),
                              "failed": int(match[2]), "seconds": float(match[3])})
            continue
        if not DIAGNOSTIC.search(line):
            continue
        diagnostic_lines += 1
        context = []
        # Godot prints its source location immediately after an error. Python
        # traceback frames are also indexed as locations, with their code lines.
        for following in islice(lines, index + 1, None):
            if DIAGNOSTIC.search(following):
                break
            if LOCATION.search(following) or (context and following.startswith("    ")):
                context.append(following)
            else:
                break
        key = (line, tuple(context))
        if key not in groups:
            groups[key] = {"message": line, "context": context, "count": 0,
                           "first_line": index + 1, "last_line": index + 1}
        groups[key]["count"] += 1
        groups[key]["last_line"] = index + 1
    flags = []
    if not summaries:
        flags.append("no_test_summary")
    if any(item["passed"] + item["failed"] == 0 for item in summaries):
        flags.append("zero_tests_reported")
    if any(item["failed"] for item in summaries):
        flags.append("failed_tests_reported")
    if diagnostic_lines:
        flags.append("diagnostics_present")
    # Unexpected output stays discoverable: the full log is the source of truth.
    selected = list(groups.values())[:limit]
    omitted = len(groups) - len(selected)
    if omitted:
        flags.append("diagnostic_groups_omitted")
    return {"verdict": "not_assessed", "source_lines": len(lines),
            "diagnostic_lines": diagnostic_lines, "unique_diagnostics": len(groups),
            "diagnostics": selected, "omitted_groups": omitted,
            "test_summaries": summaries, "flags": flags}


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("log", type=Path, help="explicit path to a completed or partial log")
    parser.add_argument("--limit", type=int, default=30, help="maximum distinct diagnostic groups")
    args = parser.parse_args(argv)
    try:
        source = args.log.resolve(strict=True)
        # Preserve undecodable bytes as visible escapes instead of silently losing them.
        text = source.read_bytes().decode("utf-8-sig", errors="backslashreplace")
        result = digest(text, args.limit)
        result["source"] = str(source)
    except (OSError, ValueError) as exc:
        print(f"log digest: {exc}", file=sys.stderr)
        return 1
    print(json.dumps(result, ensure_ascii=True))
    return 0  # successful indexing, never the underlying job's exit status


if __name__ == "__main__":
    sys.exit(main())
