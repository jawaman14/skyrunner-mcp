"""Ledger arithmetic and anti-double-counting tests, no board or GitHub writes."""
import contextlib
import io
import os
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch
from skyrunner_mcp.score_tally import main, tally

HEADER = "| # | Event | Team | Who | Evidence | Score |\n|---|---|---|---|---|---|\n"


class ScoreTally(unittest.TestCase):
    def test_pending_is_excluded_and_negative_scores_work(self):
        text = HEADER + "| 1 | seed | +2 | both | note 1 | - |\n| 2 | lapse | -2 | codex | note 2 | -2 |\n| 3 | help | +1 | claude | note 3 | -1 |\n"
        self.assertEqual(tally(text), {"team_score": -1, "counted_events": 2, "pending_entries": 1})

    def test_running_score_cannot_be_inflated(self):
        with self.assertRaisesRegex(ValueError, "computed score 1"):
            tally(HEADER + "| 1 | delivered | +1 | codex | PR 3 | 2 |\n")

    def test_duplicate_rows_are_rejected(self):
        with self.assertRaisesRegex(ValueError, "duplicate row"):
            tally(HEADER + "| 1 | a | +1 | codex | PR 3 | 1 |\n| 1 | b | +1 | codex | PR 4 | 2 |\n")

    def test_stable_event_id_prevents_different_rows_farming_same_award(self):
        text = HEADER.replace("| Event |", "| Event | Event ID |")
        # Separator cells do not need to match header length.
        text += "| 1 | a | inbox-3 | +2 | codex | PR 3 | 2 |\n| 2 | b | inbox-3 | +1 | codex | PR 3 | 3 |\n"
        with self.assertRaisesRegex(ValueError, "counted twice"):
            tally(text)

    def test_pending_proposal_then_accepted_event_counts_once(self):
        text = HEADER.replace("| Event |", "| Event | Event ID |")
        text += "| 1 | proposal | inbox-3 | +2 | codex | PR 3 | - |\n| 2 | accepted | inbox-3 | +2 | codex | review 1 | 2 |\n"
        self.assertEqual(tally(text), {"team_score": 2, "counted_events": 1, "pending_entries": 0})

    def test_missing_evidence_and_malformed_deltas_fail_closed(self):
        for row in ("| 1 | a | +3 | codex | PR 3 | 3 |", "| 1 | a | +1 | codex | | 1 |", "| 1 | a | +1 | rival | PR 3 | 1 |", "| 1 | broken |"):
            with self.subTest(row=row), self.assertRaises(ValueError):
                tally(HEADER + row)

    def test_other_tables_and_escaped_pipes_are_not_awards(self):
        text = "| Points | Rule |\n|---|---|\n| +2 | tools |\n\n" + HEADER
        text += "| 1 | a\\|b | +1 | both | note 1 | 1 |\n\nMore text"
        self.assertEqual(tally(text)["counted_events"], 1)

    def test_cli_is_read_only_and_missing_file_is_not_zero_score(self):
        with tempfile.TemporaryDirectory() as folder:
            path = Path(folder) / "score.md"
            content = HEADER + "| 1 | a | +1 | both | note 1 | 1 |\n"
            path.write_text(content, encoding="utf-8")
            with contextlib.redirect_stdout(io.StringIO()) as out:
                self.assertEqual(main([str(path)]), 0)
            self.assertIn('"team_score": 1', out.getvalue())
            self.assertEqual(path.read_text(encoding="utf-8"), content)
            with contextlib.redirect_stderr(io.StringIO()):
                self.assertEqual(main([str(path.with_name("missing.md"))]), 1)
                with patch.dict(os.environ, {"SKYRUNNER_ROOT": "relative"}):
                    self.assertEqual(main([]), 1)


if __name__ == "__main__":
    unittest.main()
