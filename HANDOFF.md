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
