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

## Lightweight tally (Codex, 11 October 2026)

Branch `codex/score-tally`, base main `3411693`. Read-only `skyrunner-score` CLI uses the game's
reviewed markdown score log as the only ledger: no new database, mutations, automatic awards or
individual rankings. It checks point deltas, actors, evidence presence, row IDs and running totals,
excludes pending entries, and rejects double-counted stable Event IDs. Legacy logs without Event ID
use row numbers; semantic deduplication and verification of evidence remain reviewer/owner duties.
New CI step runs eight ledger regressions on Windows/Linux. Locally: tally 8/8, guards 2/2,
collaboration 11/11, runtime 16 passed +1 POSIX skip, GitHub 9/9; wheel/sdist built.
Snapshot of game #315@6dc95782 computes team +1, six counted events, five pending seeds; this is a
draft-ledger snapshot, not a merged/current live total. No score award for ledger administration.
Update confirmed rows only at normal task delivery/review checkpoints; the integrator batches them.
Owner requested a Claude/Codex planning round (game board 98/99); existing lead has not replied.
A separate read-only Claude CLI attempt hit the account session limit (reset 14:20 Sydney); no
discussion was fabricated and no repeated blocked call made. Round remains queued, with task
priorities/eligible points proposed on the board. Next: independent review and Linux CI, integrate
CLI, then complete the actual planning exchange when Claude is available. No merge performed.
