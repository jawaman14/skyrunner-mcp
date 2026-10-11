# MCP extraction handoff

Owner request, 11 October 2026: move the MCP server into its own GitHub repository, separate from
Skyrunner, preserving simultaneous Claude/Codex collaboration.

Extracted from jawaman14/skyrunner at 537a2aba279d9b8b64fbf01661ceb9170b1f8899. Server modules, adapter,
runtime/coordination/guard tests and MIT notice are retained. Packaging now requires an explicit game
root and bundles the adapter, worker and collaboration guide. Game files/assets are not vendored.

The game migration changes its launcher/client configurations and CI to consume a pinned server commit.
The game PR remains separate and requires review; do not merge it automatically. Client reload and
actual Claude participation remain user-side steps. Local Godot validation may be constrained by
Windows committed-memory pressure; a zero-test or failed job is not success.

## Shared board path and machine-wide Godot lock (Claude, 11 October 2026)

Branch `claude/shared-board-machine-lock` from main `faee6dc`. `SKYRUNNER_BOARD` points separate clones on one
machine at one board file (a second clone used to get its own empty board). `MachineLock` (per user and machine)
now joins the per-checkout lock in every Godot job, and `skyrunner-godot-lock [--wait] -- <cmd>` takes it for
shell runs; `SKYRUNNER_MACHINE_LOCK=off` or another path. Windows: guards 2/2, collaboration 11/11 (4 new),
runtime 17/17 with one POSIX skip (2 new), wheel built. Linux is left to CI. Next: independent review, then
bump the game's pin in a separate game PR. `codex/board-wake` (unpushed) has its own `board_path` in
`board_channel.py`; it should call `collaboration.board_path` after this merges.

## Compact collaboration inbox (Codex, 11 October 2026)

Branch `codex/compact-collaboration-inbox`, base main `3411693`. Adds the read-only
`skyrunner_collaboration_inbox` MCP tool: addressed/shared new notes, every active claim, no task
history, explicit `has_more` and a saved cursor. Full note text is preserved; a SQLite read snapshot
prevents a concurrent insert from being skipped while advancing past unrelated notes. Existing tools
and full-history reads stay unchanged. Tests cover filtering/pagination, exact-page boundaries,
expired/released claims, long Unicode text, validation and two independent stdio clients.
Windows validation: collaboration 15/15, guards 2/2, runtime 18 tests (one POSIX-only skip),
GitHub collaboration 9/9; final stdio extension and wheel validation recorded in delivery note.
Next: Claude independent review, Windows/Linux CI, reviewed server release and separate game pin
update. Current running clients do not expose this new tool until installed/reconnected.
Owner ruling in game board note 88: both agents lose points for insufficient collaboration tooling;
score entries belong to the game score log, not this server repository. No merge performed.

## Local-model tools (Claude, 11 October 2026)

Branch `claude/local-llm-tools`, base main `1f7acd3`. Adds four tools for the owner's LM Studio server (`SKYRUNNER_LOCAL_LLM`):
`local_llm_status`, `local_llm_digest`, `local_llm_thread_summaries`, `local_llm_triage`, in `local_llm.py` and a new section of `server.py`.
The owner's large model is on that server and a model that does not fit freezes the machine, so: only an already-loaded model is ever
used (the model list is read first, read-only), no load, unload or download call, no MCP integrations, one request at a time across
processes (an OS file lock), a pause after each call, capped calls and input, reasoning off. Output is a draft; nothing is decided,
posted or replied to. The game repository has the same logic as a CLI worker (`tools/local_llm/worker.py`, jawaman14/skyrunner #336),
including `first-draft`, which writes branches and PRs and is deliberately NOT a server tool.
Tests: `tests/test_local_llm.py`, 12 tests (a fake LM Studio and fake GitHub): an unloaded model is never requested, no load/unload
path is ever called, the lock, the budget, the tools' errors; guards 2, runtime 18 (one POSIX skip), collaboration 15, GitHub 9 pass.
Not verified: a real call through the server (a client must be restarted to see new tools). Next: independent review, CI on Windows and
Linux, a server release, then a SEPARATE game PR moving the pin and the expected tool count in `tools/mcp/test_connection.py` (37 to 41).

