"""The collaboration GitHub tools: waiting on CI, cross-agent reviews, the merge queue, syncing with the default
branch and reading a merge's resolution. GitHub is faked; git runs on throwaway repositories. No network."""
import asyncio
import os
from pathlib import Path
import subprocess
import tempfile
import time
import types
import unittest
from unittest.mock import patch

fixture = tempfile.TemporaryDirectory()
fixture_root = Path(fixture.name)
(fixture_root / "project.godot").write_text("config_version=5\n", encoding="utf-8")
os.environ["SKYRUNNER_ROOT"] = str(fixture_root)
os.environ["SKYRUNNER_MACHINE_LOCK"] = str(fixture_root / "godot-machine.lock")
from skyrunner_mcp import server  # noqa: E402

HEAD = "a" * 40
OLD = "b" * 40


def review(agent, verdict, head=HEAD, at="2026-10-11T01:00:00Z", state="COMMENTED"):
    return {"body": server._review_marker(agent, verdict, head) + "\nbody", "submitted_at": at, "state": state,
            "user": {"login": "owner"}}


class FakeGitHub:
    """Enough of the REST API for these tools; `runs` is a list of states handed out one per CI look."""
    owner, name = "o", "r"
    repo = "/repos/o/r"

    def __init__(self, ref="claude/feature", runs=("completed",), reviews=(), comments=(), talk=()):
        self.ref, self.runs, self.reviews, self.comments, self.talk = ref, list(runs), list(reviews), list(comments), list(talk)
        self.posted = []

    async def get(self, path, **params):
        if path == self.repo:
            return {"default_branch": "main"}
        if path.startswith("pulls/") and path.count("/") == 1:
            return {"number": 7, "head": {"sha": HEAD, "ref": self.ref, "repo": {"full_name": "o/r"}}, "draft": False,
                    "title": "Feature", "created_at": "2026-10-11T00:00:00Z"}
        if path == "actions/runs":
            status = self.runs.pop(0) if len(self.runs) > 1 else self.runs[0]
            return {"workflow_runs": [{"id": 1, "status": status, "conclusion": "success" if status == "completed" else None,
                                       "created_at": "2026-10-11T00:30:00Z"}]}
        if path == "actions/runs/1/jobs":
            return {"jobs": [{"name": "tests", "conclusion": "success"}]}
        raise AssertionError(path)

    async def paged(self, path, **kwargs):
        if path == "pulls":
            return [await self.get("pulls/7")]
        if path.endswith("/reviews"):
            return self.reviews
        if path.startswith("pulls/") and path.endswith("/comments"):
            return self.comments
        if path.startswith("issues/"):
            return self.talk
        raise AssertionError(path)

    async def request(self, method, path, json=None):
        self.posted.append((method, path, json))
        return type("Response", (), {"json": lambda _self: {"html_url": "https://example.invalid/review"}})()


class Rules(unittest.TestCase):
    def test_agent_names_and_markers(self):
        for name, agent in [("claude-07c5", "claude"), ("codex/startup-tests", "codex"), ("Codex-thread", "codex"),
                            ("feature/phone-ui", ""), ("claudette/x", "")]:
            self.assertEqual(server._agent_of(name), agent, name)
        marked = server._marked_reviews([review("codex", "approve"), {"body": "plain review", "submitted_at": "x"}])
        self.assertEqual(marked, [{"agent": "codex", "verdict": "approve", "head": HEAD, "at": "2026-10-11T01:00:00Z"}])

    def test_queue_blockers(self):
        clean = {"clean": True, "behind": 3}
        approve = server._marked_reviews([review("codex", "approve")])
        self.assertEqual(server._queue_blockers("claude", False, "success", approve, HEAD, 0, clean), [])
        cases = [
            (dict(draft=True), "draft"),
            (dict(ci="running"), "CI running"),
            (dict(ci="failure"), "CI failed"),
            (dict(merge={"clean": False, "behind": 1}), "conflicts"),
            (dict(merge={"clean": True, "behind": 40}), "40 commits behind"),
            (dict(reviews=[]), "no review from the other agent"),
            (dict(reviews=server._marked_reviews([review("claude", "approve")])), "no review from the other agent"),
            (dict(reviews=server._marked_reviews([review("codex", "approve", head=OLD)])), "not the head"),
            (dict(reviews=server._marked_reviews([review("codex", "approve"), review("codex", "changes", at="2026-10-11T02:00:00Z")])), "changes requested"),
            (dict(waiting=2), "2 unanswered"),
        ]
        for change, expected in cases:
            args = {"draft": False, "ci": "success", "reviews": approve, "waiting": 0, "merge": clean, **change}
            with self.subTest(expected=expected):
                blockers = server._queue_blockers("claude", args["draft"], args["ci"], args["reviews"], HEAD,
                                                  args["waiting"], args["merge"])
                self.assertTrue(any(expected in b for b in blockers), blockers)
        # a later comment-only review does not hide an approval
        later_comment = server._marked_reviews([review("codex", "approve"), review("codex", "comment", at="2026-10-11T03:00:00Z")])
        self.assertEqual(server._queue_blockers("claude", False, "success", later_comment, HEAD, 0, clean), [])


class Tools(unittest.IsolatedAsyncioTestCase):
    def use(self, fake):
        p = patch.object(server, "gh", return_value=fake)
        p.start()
        self.addCleanup(p.stop)
        q = patch.object(server, "_POLL_SECONDS", 0.01)
        q.start()
        self.addCleanup(q.stop)

    async def test_wait_reports_ci_and_the_review_that_follows(self):
        bot = {"id": 5, "user": {"login": "review-bot"}, "path": "a.gd", "line": 3, "body": "P2: off by one",
               "created_at": "2026-10-11T00:40:00Z"}
        fake = FakeGitHub(runs=["in_progress", "in_progress", "completed"], comments=[bot])
        self.use(fake)
        out = await server.github_pr_wait(7, timeout_seconds=30, review_grace_seconds=5)
        self.assertIn("CI success", out)
        self.assertIn("P2: off by one", out)

    async def test_wait_gives_up_at_the_timeout(self):
        self.use(FakeGitHub(runs=["in_progress"]))
        ticks = iter([0.0, 0.0] + [1000.0] * 20)  # a clock of the server module's own: asyncio keeps the real one
        clock = types.SimpleNamespace(monotonic=lambda: next(ticks), time=time.time)
        with patch.object(server, "time", clock):
            out = await server.github_pr_wait(7, timeout_seconds=30, review_grace_seconds=0)
        self.assertIn("Still waiting", out)

    async def test_review_is_pinned_to_the_head_and_never_the_authors_own(self):
        fake = FakeGitHub(ref="claude/feature")
        self.use(fake)
        with self.assertRaises(server.ToolError):
            await server.github_pr_review(7, "claude-1", "approve", "looks fine", HEAD)
        with self.assertRaises(server.ToolError):
            await server.github_pr_review(7, "codex-1", "approve", "looks fine", OLD)
        with self.assertRaises(server.ToolError):
            await server.github_pr_review(7, "someone", "approve", "looks fine", HEAD)
        self.assertEqual(fake.posted, [])
        await server.github_pr_review(7, "codex-thread-9", "changes", "fix the guard", HEAD,
                                      [{"path": "a.gd", "line": 3, "body": "here"}])
        method, path, body = fake.posted[0]
        self.assertEqual((method, path, body["event"], body["commit_id"]), ("POST", "pulls/7/reviews", "COMMENT", HEAD))
        self.assertEqual(server._marked_reviews([{"body": body["body"], "submitted_at": "t"}])[0]["verdict"], "changes")
        self.assertEqual(body["comments"][0]["side"], "RIGHT")

    async def test_merge_queue_orders_ready_first(self):
        fake = FakeGitHub(ref="claude/feature", reviews=[review("codex", "approve")])
        self.use(fake)
        with patch.object(server, "_fetch_origin"), patch.object(server, "_merge_state", return_value={"clean": True, "behind": 0}):
            out = await server.github_merge_queue()
        self.assertIn("1 of 1 ready", out)
        self.assertIn("READY", out)
        fake.reviews = []
        with patch.object(server, "_fetch_origin"), patch.object(server, "_merge_state", return_value={"clean": False, "behind": 2}):
            out = await server.github_merge_queue()
        self.assertIn("0 of 1 ready", out)
        self.assertIn("no review from the other agent", out)


class Diff(unittest.IsolatedAsyncioTestCase):
    async def test_diff_lists_files_then_reads_matching_patches(self):
        files = [{"filename": "scripts/ui/phone_menu.gd", "status": "modified", "additions": 3, "deletions": 1, "patch": "@@ -1 +1 @@\n-a\n+b"},
                 {"filename": "assets/big.png", "status": "added", "additions": 0, "deletions": 0}]

        class Fake(FakeGitHub):
            async def get(self, path, **params):
                if path == "pulls/7":
                    return {"head": {"sha": HEAD}, "additions": 3, "deletions": 1}
                return await super().get(path, **params)

            async def paged(self, path, **kwargs):
                return files if path == "pulls/7/files" else await super().paged(path, **kwargs)

        with patch.object(server, "gh", return_value=Fake()):
            listing = await server.github_pr_diff(7)
            self.assertIn("2 files", listing)
            self.assertIn("assets/big.png (added, +0/-0, no patch)", listing)
            one = await server.github_pr_diff(7, path="phone_menu")
            self.assertIn("+b", one)
            self.assertNotIn("big.png", one)
            with self.assertRaises(server.ToolError):
                await server.github_pr_diff(7, path="nothing-here")


class Packet(unittest.IsolatedAsyncioTestCase):
    """github_review_packet: one bounded read of a PR at its current head, from the server, so a turn with no network can review."""

    def fake(self, head=HEAD, moves_to=None):
        files = [{"filename": "scripts/sim/people.gd", "status": "modified", "additions": 10, "deletions": 2, "patch": "@@ -1 +1 @@\n-a\n+b"},
                 {"filename": "tests/test_people.gd", "status": "added", "additions": 5, "deletions": 0, "patch": "+t"},
                 {"filename": "tools/collab/test_collab_tools.py", "status": "modified", "additions": 3, "deletions": 0, "patch": "+x"}]
        comments = [{"id": 1, "path": "a.gd", "line": 3, "user": {"login": "bot"}, "body": "unanswered finding"},
                    {"id": 2, "path": "b.gd", "line": 4, "user": {"login": "bot"}, "body": "answered finding"},
                    {"id": 3, "in_reply_to_id": 2, "user": {"login": "me"}, "body": "done"}]
        reviews = [review("codex", "approve", OLD), review("codex", "changes", head)]
        seen = {"gets": 0}

        class Fake(FakeGitHub):
            async def get(self, path, **params):
                if path == "pulls/7":
                    seen["gets"] += 1
                    sha = moves_to if (moves_to and seen["gets"] > 1) else head
                    return {"number": 7, "head": {"sha": sha, "ref": "claude/feature", "repo": {"full_name": "o/r"}}, "base": {"ref": "main"},
                            "draft": False, "title": "Fix people", "user": {"login": "owner"}, "created_at": "2026-10-11T00:00:00Z"}
                return await super().get(path, **params)

            async def paged(self, path, **kwargs):
                if path == "pulls/7/files":
                    return files
                return await super().paged(path, **kwargs)
        return Fake(reviews=reviews, comments=comments)

    async def test_the_packet_has_the_full_head_ci_marked_reviews_threads_files_and_tests(self):
        with patch.object(server, "gh", return_value=self.fake()):
            text = await server.github_review_packet(7)
        self.assertIn("head " + HEAD, text, "the full 40-character head")
        self.assertIn("CI on this head: success", text)
        self.assertIn("Agent review by codex: approve at " + OLD[:7] + " (STALE: older head)", text)
        self.assertIn("Agent review by codex: changes at " + HEAD[:7] + " (CURRENT)", text)
        self.assertIn("Unanswered review threads: 1", text)
        self.assertIn("a.gd:3", text)
        self.assertNotIn("b.gd:4", text, "an answered thread is not listed")
        self.assertIn("+10/-2 scripts/sim/people.gd   tests by name: tests/test_people.gd", text)
        self.assertIn("The PR changes 2 test file(s)", text, "tests outside tests/ count too")
        self.assertLess(len(text), 3000, "bounded")

    async def test_the_diff_is_capped_and_a_head_that_moves_is_flagged(self):
        with patch.object(server, "gh", return_value=self.fake(moves_to="d" * 40)):
            text = await server.github_review_packet(7, diff_lines=4)
        self.assertIn("Diff (first 4 of", text)
        self.assertIn("WARNING: the head moved while this was read", text)

    async def test_the_tool_is_registered_and_read_only(self):
        tools = {t.name: t for t in await server.mcp.list_tools()}
        self.assertIn("github_review_packet", tools)
        self.assertTrue(tools["github_review_packet"].annotations.read_only_hint)


class Sync(unittest.IsolatedAsyncioTestCase):
    """github_pr_sync_main and skyrunner_merge_resolution on a real origin, clone and conflicting branch."""

    def git(self, cwd, *args):
        r = subprocess.run(["git", "-c", "core.fsmonitor=false", "-c", "user.name=T", "-c", "user.email=t@example.invalid",
                            "-c", "init.defaultBranch=main", "-c", "core.autocrlf=false", *args],
                           cwd=cwd, capture_output=True, text=True)
        self.assertEqual(r.returncode, 0, r.stderr)
        return r.stdout.strip()

    def commit(self, cwd, name, text, message):
        (cwd / name).write_text(text, encoding="utf-8")
        self.git(cwd, "add", name)
        self.git(cwd, "commit", "-m", message)

    async def asyncSetUp(self):
        self.temp = tempfile.TemporaryDirectory()
        base = Path(self.temp.name)
        self.git(base, "init", "--bare", "origin.git")
        self.clone = base / "clone"
        self.git(base, "clone", str(base / "origin.git"), "clone")
        self.git(self.clone, "config", "user.name", "T")  # the tool's own merge commit needs an identity in the repo
        self.git(self.clone, "config", "user.email", "t@example.invalid")
        self.git(self.clone, "config", "core.autocrlf", "false")
        self.git(self.clone, "checkout", "-b", "main")
        self.commit(self.clone, "phone.gd", "a\nb\nc\n", "base")
        self.git(self.clone, "push", "-u", "origin", "main")
        self.git(self.clone, "checkout", "-b", "claude/phone")
        self.commit(self.clone, "phone.gd", "a\nB from the branch\nc\n", "branch edit")
        self.other = base / "other"
        self.git(base, "clone", str(base / "origin.git"), "other")
        self.git(self.other, "checkout", "main")
        root = patch.object(server, "ROOT", self.clone)
        root.start()
        self.addCleanup(root.stop)
        nogh = patch.object(server, "gh", side_effect=server.ToolError("offline"))
        nogh.start()
        self.addCleanup(nogh.stop)

    async def asyncTearDown(self):
        self.temp.cleanup()

    async def test_clean_merge_then_conflict_then_resolution(self):
        self.assertIn("already has everything", await server.github_pr_sync_main())
        self.commit(self.other, "notes.md", "new\n", "unrelated on main")
        self.git(self.other, "push", "origin", "main")
        out = await server.github_pr_sync_main()
        self.assertIn("no conflicts", out)
        self.assertIn("nothing was resolved by hand", await server.skyrunner_merge_resolution("HEAD"))
        with self.assertRaises(server.ToolError):
            await server.skyrunner_merge_resolution("HEAD~1")  # an ordinary commit
        self.commit(self.other, "phone.gd", "a\nB from main\nc\n", "conflicting edit on main")
        self.git(self.other, "push", "origin", "main")
        out = await server.github_pr_sync_main()
        self.assertIn("phone.gd", out)
        self.assertIn("conflict", out)
        with self.assertRaises(server.ToolError):
            await server.github_pr_sync_main()  # a merge is in progress
        (self.clone / "phone.gd").write_text("a\nB from the branch and main\nc\n", encoding="utf-8")
        self.git(self.clone, "add", "phone.gd")
        self.git(self.clone, "commit", "--no-edit")
        resolution = await server.skyrunner_merge_resolution("HEAD")
        self.assertIn("B from the branch and main", resolution)
        (self.clone / "phone.gd").write_text("dirty\n", encoding="utf-8")
        with self.assertRaises(server.ToolError):
            await server.github_pr_sync_main()  # uncommitted changes

    async def test_refuses_on_the_default_branch(self):
        self.git(self.clone, "checkout", "main")
        with self.assertRaises(server.ToolError):
            await server.github_pr_sync_main()


if __name__ == "__main__":
    unittest.main()
