# MCP extraction handoff

## Diagnostic helper for Claude (Codex, 11 October 2026)

Branch `codex/diagnostic-digest` from `main@3411693`, isolated server worktree
`skyrunner/.build/skyrunner-mcp-digest`. Adds read-only `skyrunner-log-digest`, preserving
all existing MCP tools. Indexes repeated Godot/CI diagnostic text, immediate source context,
counts and first/last log line numbers; includes all test summaries and explicit missing/
zero/failed-test/omitted-group flags. Never issues a job-success verdict. Full log remains
source of truth; unknown formats may not be recognized and timestamp prefixes are not
normalized. No game files, shared claims or live client setup changed.

Validation: diagnostic8, guards2, collaboration11, runtime16 pass+1 POSIX skip, GitHub9:
46 passed,1 skipped on Windows. One initial diagnostic test failure exposed matching
`0 failed` in a summary as a diagnostic; fixed before delivery. Wheel/sdist built offline.
An initial no-isolation build selected an interpreter without setuptools; switched to
the cached isolated build rather than changing shared dependencies. Linux CI, independent
review and actual Claude use remain pending.
Owner says trying this tool is neutral and +1 requires Claude actually functions better
with it. No score award recorded. Board note101 requests real-log use and feedback.

Next: build/publish draft server PR, Claude reviews and tests a real failure log, then
record evidence-backed recipient acceptance at a normal task checkpoint. Do not claim
token/efficiency gains from synthetic compression or add a duplicate award.

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
