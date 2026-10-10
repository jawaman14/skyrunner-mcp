# Notes for Claude

Follow AGENTS.md. This standalone repository is the source of truth for server changes; Skyrunner's
tools/mcp directory becomes a small client launcher and integration check. Do not maintain two copies
of the server. Read the bundled collaboration guide before working in the game.

Run `uv run python tests/test_runtime.py`, `uv run python tests/test_collaboration.py`, and
`uv run python tests/test_guards.py src/skyrunner_mcp/server.py`; then `uv build`.
Set SKYRUNNER_ROOT explicitly when testing against an actual game checkout. The test suites supply
their own fixtures. Update the game client's pinned commit only after independent server CI passes.
