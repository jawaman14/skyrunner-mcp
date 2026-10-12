"""The local-model tools: only an already-loaded model is ever used, nothing is loaded or unloaded, one request at a time, and what
comes back is a draft. The LM Studio server and GitHub are faked; no network."""
import asyncio
import json
import os
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch

import httpx

fixture = tempfile.TemporaryDirectory()
fixture_root = Path(fixture.name)
(fixture_root / "project.godot").write_text("config_version=5\n", encoding="utf-8")
os.environ["SKYRUNNER_ROOT"] = str(fixture_root)
os.environ["SKYRUNNER_MACHINE_LOCK"] = str(fixture_root / "godot-machine.lock")
os.environ["SKYRUNNER_LOCAL_LLM"] = "http://lmstudio.test"
os.environ["SKYRUNNER_LOCAL_PAUSE"] = "0"
os.environ["SKYRUNNER_LOCAL_LOCK"] = str(fixture_root / "local-llm.lock")
from skyrunner_mcp import local_llm as llm  # noqa: E402
from skyrunner_mcp import server  # noqa: E402
from mcp.server.mcpserver.exceptions import ToolError  # noqa: E402

MODELS = {"models": [
    {"type": "llm", "key": "big/model", "loaded_instances": [{"id": "big/model", "config": {"context_length": 262144}}]},
    {"type": "llm", "key": "other/model", "loaded_instances": []},
    {"type": "embedding", "key": "embed", "loaded_instances": [{"id": "embed"}]},
]}


class FakeLMStudio:
    """Records every request, so a test can prove nothing was loaded or unloaded and that an unloaded model was never asked for."""

    def __init__(self, models=MODELS, answer="hello", error=None):
        self.models, self.answer, self.error, self.requests = models, answer, error, []

    def transport(self):
        def handler(request: httpx.Request) -> httpx.Response:
            body = json.loads(request.content) if request.content else None
            self.requests.append((request.method, request.url.path, body))
            if request.url.path == "/api/v1/models":
                return httpx.Response(200, json=self.models)
            if self.error:
                return httpx.Response(self.error, text="insufficient memory")
            return httpx.Response(200, json={"output": [{"type": "message", "content": f" {self.answer} "}], "stats": {"reasoning_output_tokens": 0}})
        return httpx.MockTransport(handler)


def run(coro):
    return asyncio.run(coro)


class Module(unittest.TestCase):
    def setUp(self):
        os.environ.pop("SKYRUNNER_LOCAL_MODEL", None)
        os.environ.pop("SKYRUNNER_LOCAL_MAX_CHARS", None)

    def test_only_loaded_language_models_count_and_the_single_loaded_one_is_used(self):
        self.assertEqual(llm.loaded_models(MODELS), ["big/model"])
        fake = FakeLMStudio()
        self.assertEqual(run(llm.LocalLLM(fake.transport(), str(fixture_root / "t1")).chat("hi", system="s")), "hello")
        chats = [r for r in fake.requests if r[1] == "/api/v1/chat"]
        self.assertEqual(len(chats), 1)
        self.assertEqual(chats[0][2]["model"], "big/model")
        self.assertEqual(chats[0][2]["reasoning"], "off")
        self.assertFalse(chats[0][2]["store"])
        self.assertNotIn("integrations", chats[0][2], "no MCP integrations, ever")

    def test_it_never_asks_for_an_unloaded_model_and_never_loads_or_unloads(self):
        fake = FakeLMStudio()
        os.environ["SKYRUNNER_LOCAL_MODEL"] = "other/model"
        with self.assertRaises(llm.LocalLLMError) as cm:
            run(llm.LocalLLM(fake.transport(), str(fixture_root / "t2")).chat("hi"))
        self.assertIn("never loads", str(cm.exception))
        self.assertEqual([r for r in fake.requests if r[0] == "POST"], [], "no chat request for the unloaded model")
        for method, path, _ in fake.requests:
            self.assertNotIn("/load", path)
            self.assertNotIn("/unload", path)
            self.assertNotIn("/download", path)
        os.environ.pop("SKYRUNNER_LOCAL_MODEL")
        none = {"models": [{"type": "llm", "key": "x", "loaded_instances": []}]}
        with self.assertRaises(llm.LocalLLMError):
            llm.pick_model(llm.loaded_models(none))
        with self.assertRaises(llm.LocalLLMError):
            llm.pick_model(["a", "b"])

    def test_the_input_budget_follows_the_context_and_is_capped_and_overridable(self):
        self.assertEqual(llm.input_budget({"models": [{"type": "llm", "loaded_instances": [{"config": {"context_length": 16384}}]}]}), 32768)
        self.assertEqual(llm.input_budget(MODELS), llm.MAX_INPUT_CHARS)
        self.assertEqual(llm.input_budget({"models": []}), llm.DEFAULT_INPUT_CHARS)
        os.environ["SKYRUNNER_LOCAL_MAX_CHARS"] = "5000"
        self.assertEqual(llm.input_budget(MODELS), 5000)

    def test_a_refusal_an_empty_answer_and_a_thinking_run_out_of_tokens_are_errors(self):
        with self.assertRaises(llm.LocalLLMError):
            run(llm.LocalLLM(FakeLMStudio(error=400).transport(), str(fixture_root / "t3")).chat("hi"))
        with self.assertRaises(llm.LocalLLMError):
            run(llm.LocalLLM(FakeLMStudio(answer=" ").transport(), str(fixture_root / "t4")).chat("hi"))

    def test_clip_keeps_the_start_and_the_end_and_the_tier_comes_from_paths(self):
        out = llm.clip("START " + "x" * 5000 + " END", 200)
        self.assertTrue(out.startswith("START") and out.endswith("END") and "cut" in out)
        self.assertEqual([llm.tier_of(p) for p in (["docs/a.md"], ["scripts/ui/hud.gd"], ["scripts/sim/s.gd"], ["tools/collab/n.py"])], [0, 1, 2, 1])

    def test_a_second_request_waits_its_turn_instead_of_running_beside_the_first(self):
        async def go():
            async with llm.Turn(str(fixture_root / "turn"), wait=5):
                with self.assertRaises(llm.LocalLLMError):
                    async with llm.Turn(str(fixture_root / "turn"), wait=0):
                        pass
            async with llm.Turn(str(fixture_root / "turn"), wait=5):
                pass
        run(go())


class FakeGitHub:
    async def paged(self, path, **kw):
        if path == "pulls":
            return [{"number": 9, "title": "Add a thing", "draft": True, "head": {"ref": "claude/thing"}}]
        if path.endswith("/files"):
            return [{"filename": "docs/X.md", "status": "added", "patch": "+hello"}]
        return []


class Tools(unittest.TestCase):
    def setUp(self):
        os.environ.pop("SKYRUNNER_LOCAL_MODEL", None)
        self.fake = FakeLMStudio(answer="one line")
        real = llm.LocalLLM
        self.patches = [patch.object(server._llm, "LocalLLM", lambda: real(self.fake.transport(), str(fixture_root / "tool-turn"))),
                        patch.object(server, "gh", lambda: FakeGitHub())]
        for p in self.patches:
            p.start()

    def tearDown(self):
        for p in self.patches:
            p.stop()

    def test_status_reads_the_loaded_model_and_loads_nothing(self):
        text = run(server.local_llm_status())
        self.assertIn("big/model", text)
        self.assertIn("262144", text)
        self.assertEqual({r[0] for r in self.fake.requests}, {"GET"})

    def test_digest_takes_text_or_a_pr_but_not_both_or_neither(self):
        self.assertEqual(run(server.local_llm_digest(text="a log")), "one line")
        self.assertEqual(run(server.local_llm_digest(pr=9)), "one line")
        for kw in ({}, {"text": "x", "pr": 9}):
            with self.assertRaises(ToolError):
                run(server.local_llm_digest(**kw))

    def test_thread_summaries_cover_only_unanswered_threads_and_post_nothing(self):
        async def waiting(number):
            return [{"id": 5, "path": "a.py", "line": 3, "body": "fix this"}]
        with patch.object(server, "_waiting_comments", waiting):
            text = run(server.local_llm_thread_summaries(9))
        self.assertIn("comment 5", text)
        self.assertIn("NOT replies", text)
        self.assertEqual([r for r in self.fake.requests if r[0] == "POST" and "reply" in r[1]], [])

        async def none(number):
            return []
        with patch.object(server, "_waiting_comments", none):
            self.assertIn("no unanswered", run(server.local_llm_thread_summaries(9)))

    def test_triage_shows_the_tier_from_paths_who_wrote_it_and_the_models_line(self):
        text = run(server.local_llm_triage(5))
        self.assertIn("#9 tier 0, authored by claude (draft): one line", text)

    def test_a_missing_server_address_or_unloaded_model_becomes_a_tool_error(self):
        saved = os.environ.pop("SKYRUNNER_LOCAL_LLM")
        try:
            with self.assertRaises(ToolError):
                run(server.local_llm_status())
        finally:
            os.environ["SKYRUNNER_LOCAL_LLM"] = saved

    def test_the_tools_are_read_only_open_world_and_registered(self):
        names = {t.name for t in run(server.mcp.list_tools())}
        for n in ("local_llm_status", "local_llm_digest", "local_llm_thread_summaries", "local_llm_triage"):
            self.assertIn(n, names)
        for forbidden in ("local_llm_load", "local_llm_unload", "local_llm_download", "local_llm_first_draft"):
            self.assertNotIn(forbidden, names)


if __name__ == "__main__":
    unittest.main()
