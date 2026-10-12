"""Diagnostic compression must retain evidence and never imply test acceptance."""
import contextlib
import io
import json
from pathlib import Path
import tempfile
import unittest

from skyrunner_mcp.log_digest import digest, main


class LogDigest(unittest.TestCase):
    def test_repeated_parse_errors_keep_location_and_ranges(self):
        block = 'SCRIPT ERROR: Parse Error: unknown name\n    at: GDScript::reload (res://scripts/foo.gd:12)\n'
        result = digest(block * 50 + '0 passed, 0 failed in 0.1 s\n')
        self.assertEqual(result['unique_diagnostics'], 1)
        group = result['diagnostics'][0]
        self.assertEqual((group['count'], group['first_line'], group['last_line']), (50, 1, 99))
        self.assertIn('foo.gd:12', group['context'][0])
        self.assertIn('zero_tests_reported', result['flags'])
        self.assertEqual(result['verdict'], 'not_assessed')

    def test_same_message_different_source_is_not_collapsed(self):
        result = digest('    ERROR: broke\n    at: one.gd:1\n    ERROR: broke\n    at: two.gd:2\n')
        self.assertEqual(result['unique_diagnostics'], 2)
        self.assertEqual(len(result['diagnostics'][0]['context']), 1)

    def test_passing_summary_does_not_hide_earlier_failure(self):
        result = digest('1 passed, 1 failed in 1.0 s\nFAIL expected 2, got 3\n9 passed, 0 failed in 1.0 s\n')
        self.assertEqual(len(result['test_summaries']), 2)
        self.assertIn('failed_tests_reported', result['flags'])
        self.assertEqual(result['verdict'], 'not_assessed')

    def test_partial_and_unknown_logs_are_not_success(self):
        for text in ('', 'working...\n', 'fatal exception 0xC0000374\n'):
            result = digest(text)
            self.assertIn('no_test_summary', result['flags'])
            self.assertEqual(result['verdict'], 'not_assessed')

    def test_ansi_ci_and_unicode_evidence(self):
        result = digest('\x1b[31m##[error]FAIL café\x1b[0m\nWARNING: memory pressure\n')
        self.assertEqual(result['unique_diagnostics'], 2)
        self.assertEqual(result['diagnostics'][0]['message'], '##[error]FAIL café')

    def test_bounded_groups_are_explicit_not_silent_truncation(self):
        result = digest('\n'.join(f'ERROR: message {i}' for i in range(6)), limit=2)
        self.assertEqual(result['unique_diagnostics'], 6)
        self.assertEqual(result['omitted_groups'], 4)
        self.assertIn('diagnostic_groups_omitted', result['flags'])
        with self.assertRaises(ValueError):
            digest('', 0)

    def test_traceback_keeps_frames(self):
        result = digest('Traceback (most recent call last):\n  File "test.py", line 2\n    fail()\nAssertionError: mismatch\n')
        self.assertIn('test.py', result['diagnostics'][0]['context'][0])
        self.assertEqual(result['diagnostics'][-1]['message'], 'AssertionError: mismatch')

    def test_cli_read_only_missing_and_invalid_encoding(self):
        with tempfile.TemporaryDirectory() as directory:
            source = Path(directory) / 'failure.log'
            original = b'ERROR: bad byte \xff\n'
            source.write_bytes(original)
            output = io.StringIO()
            with contextlib.redirect_stdout(output):
                self.assertEqual(main([str(source)]), 0)
            result = json.loads(output.getvalue())
            self.assertIn('\\xff', result['diagnostics'][0]['message'])
            self.assertEqual(source.read_bytes(), original)
            with contextlib.redirect_stderr(io.StringIO()):
                self.assertEqual(main([str(source.with_name('missing'))]), 1)


if __name__ == '__main__':
    unittest.main()
