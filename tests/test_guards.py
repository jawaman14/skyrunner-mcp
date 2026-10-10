import ast
import asyncio
import re
import sys
from pathlib import Path
import unittest

source = Path(sys.argv.pop(1))
tree = ast.parse(source.read_text(encoding="utf-8"))
names = {"_summarize_tests", "_error_lines", "_clip", "github_pr_merge"}
nodes = [n for n in tree.body if isinstance(n, (ast.FunctionDef, ast.AsyncFunctionDef)) and n.name in names]
for node in nodes:
    node.decorator_list = []
    node.returns = None
    for arg in node.args.args:
        arg.annotation = None
env = {"re": re, "MAX_TEXT": 12000, "ToolError": RuntimeError}
for node in tree.body:
    if isinstance(node, ast.Assign) and any(isinstance(t, ast.Name) and t.id in {"_ERROR_RE", "_SHUTDOWN_NOISE"} for t in node.targets):
        exec(compile(ast.Module(body=[node], type_ignores=[]), str(source), "exec"), env)
exec(compile(ast.Module(body=nodes, type_ignores=[]), str(source), "exec"), env)

class FakeGitHub:
    def __init__(self, mergeable=True, jobs=True):
        self.mergeable, self.jobs, self.writes = mergeable, jobs, 0
    async def get(self, path, **kwargs):
        if path == "pulls/293":
            return {"state": "open", "draft": False, "head": {"sha": "a"*40}, "base": {"ref": "main"}, "mergeable": self.mergeable, "title": "Test"}
        if path == "actions/runs":
            return {"workflow_runs": [{"id": 1, "status": "completed", "conclusion": "success"}]}
        if path == "actions/runs/1/jobs":
            return {"jobs": [{"name": "tests", "conclusion": "success"}] if self.jobs else []}
        raise AssertionError(path)
    async def paged(self, *args, **kwargs):
        return []
    async def request(self, *args, **kwargs):
        self.writes += 1
        return type("Response", (), {"json": lambda self: {"sha": "b"*40}})()

class Guards(unittest.TestCase):
    def test_test_results(self):
        for text, code, expected in [
            ("0 passed, 0 failed in 0.1 s", 0, False),
            ("1 passed, 0 failed in 0.1 s", 0, True),
            ("0 passed, 1 failed in 0.1 s", 1, False),
            ("SCRIPT ERROR: failure\n1 passed, 0 failed in 0.1 s", 0, False),
        ]:
            with self.subTest(text=text):
                self.assertEqual(env["_summarize_tests"](text, code)["ok"], expected)
    def test_merge_evidence(self):
        for mergeable, jobs, expected in [(None, True, 0), (True, False, 0), (True, True, 1)]:
            with self.subTest(mergeable=mergeable, jobs=jobs):
                client = FakeGitHub(mergeable, jobs)
                env["gh"] = lambda: client
                try:
                    asyncio.run(env["github_pr_merge"](293, "a"*40))
                except RuntimeError:
                    pass
                self.assertEqual(client.writes, expected)

unittest.main()
