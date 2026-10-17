# /// script
# requires-python = ">=3.11"
# dependencies = ["mcp>=2.3,<3", "httpx>=0.27"]
# ///
"""Real job lifecycle and stdio checks. Run with uv run --script tools/mcp/test_runtime.py."""
import asyncio
import importlib.util
import os
from pathlib import Path
import sys
import tempfile
import subprocess
import json
import unittest
from unittest.mock import patch
from mcp import ClientSession, StdioServerParameters
from mcp.client.stdio import stdio_client

# Tests are self-contained: never require or operate on a real game checkout.
fixture = tempfile.TemporaryDirectory()
fixture_root = Path(fixture.name)
(fixture_root / "project.godot").write_text("config_version=5\n", encoding="utf-8")
(fixture_root / "tests").mkdir()
(fixture_root / "tests/test_docs.gd").write_text("func test_example():\n    pass\n", encoding="utf-8")
os.environ["SKYRUNNER_ROOT"] = str(fixture_root)
os.environ["SKYRUNNER_MACHINE_LOCK"] = str(fixture_root / "godot-machine.lock")  # never the real machine's lock
from skyrunner_mcp import server, run_tests as worker


class Runtime(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        (server.ROOT / ".build").mkdir(exist_ok=True)
        self.temp = tempfile.TemporaryDirectory(dir=server.ROOT / ".build")
        self.work = patch.object(server, "WORK", Path(self.temp.name))
        self.work.start()
        server.JOBS.clear()
        server._queue_turn = None  # the queue's lock belongs to one event loop: each test has its own
        self.poll = patch.object(server, "QUEUE_POLL_S", 0.05)
        self.poll.start()

    async def asyncTearDown(self):
        self.poll.stop()
        for job in list(server.JOBS.values()):
            if job.queued:
                job.cancelled = True
                job.done.set()
        await asyncio.sleep(0.2)  # let the queue's runners see the cancel
        for job in server.JOBS.values():
            if job.running:
                server._terminate_job(job, force=True)
                await job.proc.wait()
        self.work.stop()
        self.temp.cleanup()

    async def test_compact_inbox_tool_preserves_complete_notes_and_cursor(self):
        from skyrunner_mcp.collaboration import Board
        board = Board(Path(self.temp.name) / "board.sqlite3")
        note = board.note("claude-session", "codex", "review at exact SHA")
        with patch.object(server, "_board", return_value=board):
            result = json.loads(await server.skyrunner_collaboration_inbox("codex", limit=1))
            self.assertEqual(result["notes"][0]["id"], note)
            self.assertNotIn("tasks", result)
            self.assertFalse(result["has_more"])
            self.assertEqual(result["next_cursor"], note)
            with self.assertRaises(server.ToolError):
                await server.skyrunner_collaboration_inbox("invalid")

    async def test_completed_job(self):
        output = "TEST FILE: test_fixture.gd — 1 passed, 0 failed\n1 passed, 0 failed in 0.1 s\n"
        command = "import sys; sys.stdout.buffer.write(%r)" % output.encode("utf-8")
        job = await server._start_job("fixture", [sys.executable, "-c", command],
                                      server._summarize_tests, timeout_s=10)
        await server._wait(job, 10)
        self.assertEqual(job.proc.returncode, 0)
        self.assertEqual(server._summarize_tests(job.text(), 0)["files with tests run"], 1)

    async def test_external_cache_lock_refuses_spawn(self):
        lock = server.ProjectLock(server.WORK / "godot-cache.lock")
        try:
            with self.assertRaises(server.ToolError), patch.object(server.asyncio, "create_subprocess_exec") as spawn:
                await server._start_job("blocked", [sys.executable, "-c", "pass"], server._summarize_plain)
            spawn.assert_not_called()
            self.assertEqual(server.JOBS, {})
        finally:
            lock.close()

    async def test_machine_lock_held_elsewhere_refuses_spawn(self):
        lock = server.MachineLock(server.MACHINE_LOCK, {"kind": "tests", "root": "other-worktree", "pid": 1})
        try:
            with self.assertRaises(server.ToolError) as caught, \
                    patch.object(server.asyncio, "create_subprocess_exec") as spawn:
                await server._start_job("blocked", [sys.executable, "-c", "pass"], server._summarize_plain)
            spawn.assert_not_called()
            self.assertIn("other-worktree", str(caught.exception))
            self.assertEqual(server.JOBS, {})
            server.ProjectLock(server.WORK / "godot-cache.lock").close()  # the checkout's own lock was given back
        finally:
            lock.close()

    async def test_finished_job_releases_machine_lock(self):
        job = await server._start_job("fixture", [sys.executable, "-c", "pass"], server._summarize_plain, timeout_s=10)
        await server._wait(job, 10)
        for _ in range(50):
            try:
                server.MachineLock(server.MACHINE_LOCK, {}).close()
                break
            except RuntimeError:
                await asyncio.sleep(0.05)
        else:
            self.fail("the machine lock is still held after the job ended")

    async def test_failed_spawn_releases_cache_lock(self):
        # Exercise create_subprocess_exec failure on both platforms. GNU timeout itself starts
        # successfully and returns 127 for a missing child; that is a completed job, not a spawn error.
        with patch.object(server.shutil, "which", return_value=None), self.assertRaises(FileNotFoundError):
            await server._start_job("missing", [str(Path(self.temp.name) / "missing-program")], server._summarize_plain)
        job = await server._start_job("next", [sys.executable, "-c", "pass"], server._summarize_plain)
        await server._wait(job, 10)
        self.assertEqual(job.proc.returncode, 0)

    @unittest.skipIf(os.name == "nt", "POSIX process-group exit race")
    async def test_posix_cancel_exit_race_is_harmless(self):
        job = await server._start_job("race", [sys.executable, "-c", "import time; time.sleep(60)"],
                                      server._summarize_plain, timeout_s=60)
        with patch.object(server.os, "killpg", side_effect=ProcessLookupError):
            server._terminate_job(job)
        await server.skyrunner_job_cancel(job.id)

    async def test_introspection_obeys_job_guard(self):
        job = await server._start_job("fixture", [sys.executable, "-c", "import time; time.sleep(60)"],
                                      server._summarize_plain, timeout_s=60)
        server._introspect_cache.clear()
        with patch.object(server, "_git", return_value="revision"), patch.object(server, "godot", return_value=sys.executable):
            with self.assertRaises(server.ToolError):
                await server._introspect()
        await server.skyrunner_job_cancel(job.id)

    async def test_cancel_and_single_job_limit(self):
        job = await server._start_job("fixture", [sys.executable, "-c", "import time; time.sleep(60)"],
                                      server._summarize_plain, timeout_s=60)
        with self.assertRaises(server.ToolError):
            await server._start_job("second", [sys.executable, "-c", "pass"], server._summarize_plain)
        await server.skyrunner_job_cancel(job.id)
        self.assertFalse(job.running)

    # ------------------------------------------------------------------ the queue
    SLEEP = [sys.executable, "-c", "import time; time.sleep(%s)"]

    def _sleep_cmd(self, seconds):
        return [self.SLEEP[0], self.SLEEP[1], self.SLEEP[2] % seconds]

    async def test_queued_job_waits_for_the_running_job_then_runs(self):
        first = await server._start_job("first", self._sleep_cmd(1.0), server._summarize_plain, timeout_s=20)
        second = await server._start_job("second", [sys.executable, "-c", "print('ran')"], server._summarize_plain,
                                         timeout_s=20, queue=True)
        self.assertTrue(second.queued)
        self.assertIsNone(second.proc)
        self.assertEqual(server._queue_position(second), 1)
        self.assertIn("queued, number 1", server._report(second))
        self.assertIn("queued, number 1", await server.skyrunner_job_status())
        await server._wait(second, 20)
        self.assertEqual(second.exit_code, 0)
        self.assertIn("ran", second.text())
        self.assertLessEqual(first.ended, second.started, "the queued job started only after the first one ended")

    async def test_queued_jobs_run_in_the_order_they_were_queued(self):
        await server._start_job("first", self._sleep_cmd(0.8), server._summarize_plain, timeout_s=20)
        a = await server._start_job("a", self._sleep_cmd(0.1), server._summarize_plain, timeout_s=20, queue=True)
        b = await server._start_job("b", self._sleep_cmd(0.1), server._summarize_plain, timeout_s=20, queue=True)
        c = await server._start_job("c", self._sleep_cmd(0.1), server._summarize_plain, timeout_s=20, queue=True)
        self.assertEqual([server._queue_position(j) for j in (a, b, c)], [1, 2, 3])
        for j in (a, b, c):
            await server._wait(j, 20)
        self.assertTrue(a.started <= b.started <= c.started, "first in, first out")
        self.assertTrue(all(j.exit_code == 0 for j in (a, b, c)))

    async def test_a_free_lock_starts_a_queued_job_at_once(self):
        job = await server._start_job("now", [sys.executable, "-c", "pass"], server._summarize_plain, timeout_s=20, queue=True)
        await server._wait(job, 20)
        self.assertEqual(job.exit_code, 0)

    async def test_cancelling_a_queued_job_means_it_never_starts(self):
        await server._start_job("first", self._sleep_cmd(1.0), server._summarize_plain, timeout_s=20)
        second = await server._start_job("second", [sys.executable, "-c", "pass"], server._summarize_plain,
                                         timeout_s=20, queue=True)
        with patch.object(server.asyncio, "create_subprocess_exec", wraps=server.asyncio.create_subprocess_exec) as spawn:
            self.assertIn("cancelled", await server.skyrunner_job_cancel(second.id))
            await asyncio.sleep(0.3)
        self.assertFalse(second.queued)
        self.assertIsNone(second.proc)
        self.assertIn("cancelled while queued", server._report(second))
        spawn.assert_not_called()

    async def test_a_queued_job_waits_for_the_machine_lock_held_elsewhere(self):
        other = server.MachineLock(server.MACHINE_LOCK, {"kind": "tests", "root": "other-worktree", "pid": 1})
        job = await server._start_job("waits", [sys.executable, "-c", "pass"], server._summarize_plain, timeout_s=20, queue=True)
        await asyncio.sleep(0.4)
        self.assertTrue(job.queued, "still waiting while the other agent's job holds the lock")
        self.assertIn("other-worktree", job.waiting_for)
        other.close()
        await server._wait(job, 20)
        self.assertEqual(job.exit_code, 0)

    async def test_a_queued_job_that_cannot_start_ends_instead_of_waiting_forever(self):
        with patch.object(server.shutil, "which", return_value=None):
            job = await server._start_job("missing", [str(Path(self.temp.name) / "no-such-program")],
                                          server._summarize_plain, timeout_s=20, queue=True)
            await server._wait(job, 10)
        self.assertFalse(job.queued)
        self.assertIsNone(job.proc)
        self.assertIn("could not start", server._report(job))

    async def test_a_finished_queued_job_tells_the_board_recipient(self):
        from skyrunner_mcp.collaboration import Board
        board = Board(Path(self.temp.name) / "queue-board.sqlite3")
        with patch.object(server, "_board", return_value=board):
            first = await server._start_job("first", self._sleep_cmd(0.5), server._summarize_plain, timeout_s=20)
            job = await server._start_job("done", [sys.executable, "-c", "pass"], server._summarize_plain,
                                          timeout_s=20, queue=True, notify="claude")
            await server._wait(job, 20)
            await asyncio.sleep(0.2)
            notes = board.read(recipient="claude")["notes"]
        self.assertTrue(any("queue done" in n["text"] and job.id in n["text"] for n in notes), notes)
        self.assertFalse(first.queued)

    async def test_a_queued_tool_call_returns_at_once_whatever_wait_seconds_says(self):
        await server._start_job("first", self._sleep_cmd(2.0), server._summarize_plain, timeout_s=20)
        began = asyncio.get_running_loop().time()
        with patch.object(server, "godot", return_value=sys.executable):
            text = await server.skyrunner_import(wait_seconds=30, queue=True)
        self.assertLess(asyncio.get_running_loop().time() - began, 5.0, "it did not wait for the lock")
        self.assertIn("queued, number 1", text)

    async def test_cancelling_while_the_process_is_being_created_still_stops_it(self):
        real = server.asyncio.create_subprocess_exec

        async def slow_spawn(*args, **kwargs):
            await asyncio.sleep(0.4)
            return await real(*args, **kwargs)
        with patch.object(server.asyncio, "create_subprocess_exec", slow_spawn):
            job = await server._start_job("race", self._sleep_cmd(30), server._summarize_plain, timeout_s=60, queue=True)
            await asyncio.sleep(0.15)  # the runner is inside the spawn
            self.assertTrue(job.queued)
            await server.skyrunner_job_cancel(job.id)
            await asyncio.sleep(1.2)
        self.assertIsNotNone(job.proc, "the spawn finished")
        self.assertFalse(job.running, "and the process was stopped, not left running")

    async def test_without_queue_a_busy_lock_still_refuses(self):
        await server._start_job("first", self._sleep_cmd(1.0), server._summarize_plain, timeout_s=20)
        with self.assertRaises(server.ToolError):
            await server._start_job("second", [sys.executable, "-c", "pass"], server._summarize_plain)

    async def test_deadline(self):
        job = await server._start_job("fixture", [sys.executable, "-c", "import time; time.sleep(60)"],
                                      server._summarize_plain, timeout_s=1)
        await server._wait(job, 15)
        self.assertFalse(job.running)
        self.assertIn("124", server._report(job))
        self.assertIn("FAILED", server._report(job))

    async def test_cancel_stops_descendant(self):
        marker = Path(self.temp.name) / "child-survived"
        child = "import time; from pathlib import Path; time.sleep(3); Path(%r).touch()" % str(marker)
        parent = "import subprocess,sys,time; subprocess.Popen([sys.executable,'-c',%r]); print('child-started',flush=True); time.sleep(60)" % child
        job = await server._start_job("tree", [sys.executable, "-u", "-c", parent],
                                      server._summarize_plain, timeout_s=60)
        for _ in range(100):
            if "child-started" in job.text():
                break
            await asyncio.sleep(0.05)
        self.assertIn("child-started", job.text())
        await server.skyrunner_job_cancel(job.id)
        await asyncio.sleep(4)
        self.assertFalse(marker.exists(), "cancellation must stop descendants too")

    async def test_stdio_initialization(self):
        params = StdioServerParameters(command=sys.executable, args=["-m", "skyrunner_mcp"], env=dict(os.environ))
        async with stdio_client(params) as (reader, writer):
            async with ClientSession(reader, writer) as session:
                await session.initialize()
                listing = await session.list_tools()
                self.assertIn("skyrunner_run_tests", [t.name for t in listing.tools])
                result = await session.call_tool("skyrunner_list_tests", {"filter": "docs"})
                self.assertFalse(result.is_error)
                self.assertIn("test_docs.gd", result.content[0].text)

    async def test_cli_refuses_missing_or_invalid_game_root(self):
        for value in (None, str(Path(self.temp.name) / "not-a-game")):
            env = dict(os.environ)
            env.pop("SKYRUNNER_ROOT", None)
            if value is not None:
                env["SKYRUNNER_ROOT"] = value
            proc = await asyncio.create_subprocess_exec(sys.executable, "-m", "skyrunner_mcp", env=env,
                                                        stdout=asyncio.subprocess.PIPE, stderr=asyncio.subprocess.PIPE)
            out, err = await proc.communicate()
            self.assertNotEqual(proc.returncode, 0)
            self.assertIn("SKYRUNNER_ROOT", err.decode())

    async def test_introspection_uses_packaged_adapter(self):
        from types import SimpleNamespace
        server._introspect_cache.clear()
        job = SimpleNamespace(running=False, proc=SimpleNamespace(returncode=0), extra={},
                              text=lambda: 'INTROSPECT_JSON {"commands": {}, "switches": {}}')
        with patch.object(server, "_git", return_value="fixture"), patch.object(server, "godot", return_value="godot"):
            with patch.object(server, "_start_job", return_value=job) as start, patch.object(server, "_wait"):
                await server._introspect()
        command = start.call_args.args[1]
        adapter = Path(command[-1])
        self.assertEqual(adapter, Path(server.__file__).with_name("mcp_introspect.gd"))
        self.assertTrue(adapter.is_file())
        self.assertNotEqual(adapter.parent, server.ROOT / "tools")
        server._introspect_cache.clear()

    async def test_unauthenticated_github_reads_but_refuses_writes(self):
        with patch.object(server, "_repo", return_value=("owner", "repo")), patch.object(server, "_token", return_value=""):
            client = server.GitHub()
        await client.client.aclose()
        seen = []
        def handler(request):
            seen.append(request.method)
            self.assertNotIn("authorization", request.headers)
            return server.httpx.Response(200, json={"state": "open"})
        client.client = server.httpx.AsyncClient(base_url="https://api.github.com", transport=server.httpx.MockTransport(handler))
        try:
            self.assertEqual((await client.get("issues/97"))["state"], "open")
            for method in ("POST", "PATCH", "PUT", "DELETE"):
                with self.assertRaises(server.ToolError):
                    await client.request(method, "issues/97")
            self.assertEqual(seen, ["GET"])
        finally:
            await client.client.aclose()

    async def test_two_stdio_clients_share_claims_and_notes(self):
        checkout = Path(self.temp.name) / "checkout"
        checkout.mkdir()
        (checkout / "project.godot").write_text("config_version=5\n", encoding="utf-8")
        def git(*args):
            result = subprocess.run(["git", "-c", "core.fsmonitor=false", *args], cwd=checkout, capture_output=True)
            self.assertEqual(result.returncode, 0, result.stderr)
        git("init")
        git("add", "project.godot")
        git("-c", "user.name=MCP Test", "-c", "user.email=mcp-test@example.invalid", "commit", "-m", "fixture")
        params = StdioServerParameters(command=sys.executable, args=["-m", "skyrunner_mcp"],
                                       env={**os.environ, "SKYRUNNER_ROOT": str(checkout)})
        async with stdio_client(params) as (r1, w1), stdio_client(params) as (r2, w2):
            async with ClientSession(r1, w1) as codex, ClientSession(r2, w2) as claude:
                await codex.initialize()
                await claude.initialize()
                claim = await codex.call_tool("skyrunner_collaboration_claim", {"owner": "codex-test", "title": "HUD", "paths": ["scripts/ui/hud.gd"]})
                self.assertFalse(claim.is_error)
                task = json.loads(claim.content[0].text)["task_id"]
                conflict = await claude.call_tool("skyrunner_collaboration_claim", {"owner": "claude-test", "title": "HUD", "paths": ["scripts/ui"]})
                self.assertTrue(conflict.is_error)
                await codex.call_tool("skyrunner_collaboration_note", {"author": "codex-test", "recipient": "claude", "text": "Ready for review", "task_id": task})
                inbox = await claude.call_tool("skyrunner_collaboration_read", {"recipient": "claude"})
                self.assertIn("Ready for review", inbox.content[0].text)
                self.assertIn("codex-test", inbox.content[0].text)
                compact = await claude.call_tool("skyrunner_collaboration_inbox", {"recipient": "claude", "limit": 1})
                self.assertFalse(compact.is_error)
                page = json.loads(compact.content[0].text)
                self.assertNotIn("tasks", page)
                self.assertEqual(page["notes"][0]["text"], "Ready for review")
                self.assertEqual(page["active_claims"][0]["id"], task)
                again = await claude.call_tool("skyrunner_collaboration_inbox", {"recipient": "claude", "after": page["next_cursor"]})
                self.assertEqual(json.loads(again.content[0].text)["notes"], [])
                wrong_owner = await claude.call_tool("skyrunner_collaboration_update", {"task_id": task, "owner": "claude-test", "status": "done"})
                self.assertTrue(wrong_owner.is_error)
                released = await codex.call_tool("skyrunner_collaboration_update", {"task_id": task, "owner": "codex-test", "status": "review", "detail": "abc123; tests passed"})
                self.assertFalse(released.is_error)
                next_claim = await claude.call_tool("skyrunner_collaboration_claim", {"owner": "claude-test", "title": "Review HUD", "paths": ["scripts/ui/hud.gd"]})
                self.assertFalse(next_claim.is_error)


class TestWorker(unittest.TestCase):
    def test_failed_import_prevents_tests(self):
        for code, output in [(1, "import failed"), (0, "SCRIPT ERROR: broken"), (0, "Parse Error: broken")]:
            with self.subTest(code=code, output=output), patch.object(sys, "argv", ["worker", "godot", "--reimport"]):
                with patch.object(worker.subprocess, "run", return_value=worker.subprocess.CompletedProcess([], code, output)):
                    with patch.object(worker.subprocess, "call") as tests:
                        self.assertNotEqual(worker.main(), 0)
                        tests.assert_not_called()

    def test_successful_import_runs_literal_filter(self):
        with patch.object(sys, "argv", ["worker", "godot with spaces", "--reimport", "--filter", "docs"]):
            with patch.object(worker.subprocess, "run", return_value=worker.subprocess.CompletedProcess([], 0, "import OK")):
                with patch.object(worker.subprocess, "call", return_value=0) as tests:
                    self.assertEqual(worker.main(), 0)
                    tests.assert_called_once_with(["godot with spaces", "--headless", "--script", "res://tests/run_tests.gd", "--", "docs"])


if __name__ == "__main__":
    unittest.main()
