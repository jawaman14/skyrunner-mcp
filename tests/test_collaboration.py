"""Cross-process coordination regressions; no Godot, network or GitHub writes."""
import json
import os
from pathlib import Path
import subprocess
import sys
import tempfile
import time
import unittest
from unittest.mock import patch
from skyrunner_mcp.collaboration import Board, MachineLock, ProjectLock, board_path, machine_lock_path

SRC = str(Path(__file__).resolve().parents[1] / "src")


class Coordination(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.root = Path(self.temp.name)
        self.board = Board(self.root / "board.sqlite3")

    def tearDown(self):
        self.temp.cleanup()

    def claim(self, paths, owner="codex-session-1"):
        return self.board.claim(owner, "bounded task", paths, "checkout", "abc123")

    def test_overlapping_directory_claim_is_refused_across_clients(self):
        self.claim(["scripts/ui"])
        other = Board(self.board.path)
        with self.assertRaises(ValueError):
            other.claim("claude-session-2", "other", ["scripts/UI/hud.gd"], "other-worktree", "def456")
        other.claim("claude-session-2", "other", ["scripts/sim/jobs.gd"], "other-worktree", "def456")

    def test_owner_and_expiry_are_enforced(self):
        task = self.claim(["scripts/ui/hud.gd"])
        with self.assertRaises(ValueError):
            self.board.update(task, "claude-session-2", "done", "")
        self.board.update(task, "codex-session-1", "active", "checkpoint")
        with patch("skyrunner_mcp.collaboration.time.time", return_value=10**12):
            self.assertEqual(self.board.read()["tasks"][0]["status"], "expired")
            with self.assertRaises(ValueError):
                self.board.update(task, "codex-session-1", "active", "")
            self.claim(["scripts/ui/hud.gd"], "claude-session-2")

    def test_finished_tasks_release_claim_but_cannot_reactivate(self):
        for status in ("blocked", "review", "done", "released"):
            task = self.claim(["scripts/ui/hud.gd"])
            self.board.update(task, "codex-session-1", status, "revision/tests/next step")
            with self.assertRaises(ValueError):
                self.board.update(task, "codex-session-1", "active", "")

    def test_notes_survive_reopen_and_have_filtered_cursors(self):
        self.board.note("codex-session-1", "claude", "Ready for core review")
        self.board.note("claude-session-2", "codex", "UI interface available")
        self.board.note("claude-session-2", "all", "Shared checkpoint")
        page = Board(self.board.path).read(recipient="claude", limit=1)
        self.assertEqual(page["notes"][0]["text"], "Ready for core review")
        page = self.board.read(page["next_cursor"], recipient="claude", limit=1)
        self.assertEqual(page["notes"][0]["text"], "Shared checkpoint")
        self.assertEqual(self.board.read(page["next_cursor"], recipient="claude")["notes"], [])

    def test_invalid_paths_and_note_tasks(self):
        for path in ("../secret", "/absolute", "C:/secret", "scripts/../sim", "scripts//ui", ""):
            with self.subTest(path=path), self.assertRaises(ValueError):
                self.claim([path])
        with self.assertRaises(ValueError):
            self.board.note("codex", "claude", "hello", "unknown")

    def test_concurrent_processes_cannot_both_claim_same_file(self):
        code = "from skyrunner_mcp.collaboration import Board; import sys; b=Board(sys.argv[1]);\ntry: print(b.claim(sys.argv[2], 'race', ['scripts/ui/hud.gd'], 'checkout', 'rev'))\nexcept ValueError: sys.exit(2)"
        env = {**os.environ, "PYTHONPATH": str(Path(__file__).resolve().parents[1] / "src")}
        procs = [subprocess.Popen([sys.executable, "-c", code, str(self.board.path), who], env=env,
                                 stdout=subprocess.PIPE, stderr=subprocess.PIPE) for who in ("codex-1", "claude-2")]
        for proc in procs:
            stdout, stderr = proc.communicate(timeout=15)
            self.assertIn(proc.returncode, (0, 2), stderr.decode())
        self.assertEqual(sorted(p.returncode for p in procs), [0, 2])

    def test_kernel_lock_refuses_another_process_and_releases(self):
        path = self.root / "godot-cache.lock"
        lock = ProjectLock(path)
        code = "from skyrunner_mcp.collaboration import ProjectLock; import sys;\ntry: p=ProjectLock(sys.argv[1]); p.close()\nexcept RuntimeError: sys.exit(2)"
        env = {**os.environ, "PYTHONPATH": str(Path(__file__).resolve().parents[1] / "src")}
        try:
            result = subprocess.run([sys.executable, "-c", code, str(path)], env=env, capture_output=True, timeout=15)
            self.assertEqual(result.returncode, 2, result.stderr)
        finally:
            lock.close()
        result = subprocess.run([sys.executable, "-c", code, str(path)], env=env, capture_output=True, timeout=15)
        self.assertEqual(result.returncode, 0, result.stderr)

    def test_board_path_follows_override(self):
        common = self.root / "repo.git"
        with patch.dict(os.environ, {"SKYRUNNER_BOARD": ""}):
            self.assertEqual(board_path(common), common.resolve() / "skyrunner-mcp" / "collaboration.sqlite3")
        shared = self.root / "shared" / "board.sqlite3"
        with patch.dict(os.environ, {"SKYRUNNER_BOARD": str(shared)}):
            self.assertEqual(board_path(common), shared)
        with patch.dict(os.environ, {"SKYRUNNER_BOARD": "relative/board.sqlite3"}), self.assertRaises(ValueError):
            board_path(common)

    def test_machine_lock_path_settings(self):
        with patch.dict(os.environ, {"SKYRUNNER_MACHINE_LOCK": "OFF"}):
            self.assertIsNone(machine_lock_path())
        with patch.dict(os.environ, {"SKYRUNNER_MACHINE_LOCK": str(self.root / "m.lock")}):
            self.assertEqual(machine_lock_path(), self.root / "m.lock")
        with patch.dict(os.environ, {"SKYRUNNER_MACHINE_LOCK": "m.lock"}), self.assertRaises(ValueError):
            machine_lock_path()
        with patch.dict(os.environ, {"SKYRUNNER_MACHINE_LOCK": ""}):
            self.assertEqual(machine_lock_path().parts[-2:], ("skyrunner-mcp", "godot-machine.lock"))

    def test_machine_lock_names_its_holder_and_releases(self):
        path = self.root / "godot-machine.lock"
        code = ("from skyrunner_mcp.collaboration import MachineLock; import sys\n"
                "try: MachineLock(sys.argv[1], {}).close()\n"
                "except RuntimeError as e: print(e); sys.exit(2)")
        env = {**os.environ, "PYTHONPATH": SRC}
        lock = MachineLock(path, {"kind": "tests", "root": "C:/worktree-a", "pid": 1})
        try:
            result = subprocess.run([sys.executable, "-c", code, str(path)], env=env, capture_output=True, text=True, timeout=15)
            self.assertEqual(result.returncode, 2, result.stderr)
            self.assertIn("tests in C:/worktree-a", result.stdout)
        finally:
            lock.close()
        result = subprocess.run([sys.executable, "-c", code, str(path)], env=env, capture_output=True, text=True, timeout=15)
        self.assertEqual(result.returncode, 0, result.stderr)

    def test_shell_wrapper_refuses_or_waits_while_held(self):
        path = self.root / "godot-machine.lock"
        env = {**os.environ, "PYTHONPATH": SRC, "SKYRUNNER_MACHINE_LOCK": str(path)}
        run = [sys.executable, "-m", "skyrunner_mcp.locked"]
        command = ["--", sys.executable, "-c", "print('ran')"]
        lock = MachineLock(path, {"kind": "tests", "root": "elsewhere", "pid": 1})
        try:
            result = subprocess.run(run + command, env=env, capture_output=True, text=True, timeout=30)
            self.assertEqual(result.returncode, 75, result.stderr)
            self.assertNotIn("ran", result.stdout)
            waiting = subprocess.Popen(run + ["--wait"] + command, env=env, stdout=subprocess.PIPE,
                                       stderr=subprocess.PIPE, text=True)
            time.sleep(2)
            self.assertIsNone(waiting.poll(), "--wait must not run the command while the lock is held")
        finally:
            lock.close()
        stdout, stderr = waiting.communicate(timeout=30)
        self.assertEqual(waiting.returncode, 0, stderr)
        self.assertIn("Waiting", stderr)
        self.assertIn("ran", stdout)


if __name__ == "__main__":
    unittest.main()
