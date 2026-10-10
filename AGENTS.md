# Server development

This repository owns the MCP implementation. The game lives separately at jawaman14/skyrunner.
Read README.md, HANDOFF.md and src/skyrunner_mcp/COLLABORATION.md. Python 3.11+; use uv and preserve all
30 public tool names unless a deliberate compatibility change is requested.

- Require an explicit SKYRUNNER_ROOT; never infer the game from the server repository.
- Resolve git, Godot, logs and the shared board against the target game checkout. Keep packaged adapters,
  workers and instructions resolved against the installed server package.
- Preserve cross-process job locking, failed-import/zero-test gates, conservative GitHub write guards,
  process-tree cancellation and UTF-8 log handling. Do not print or commit credentials.
- Coordinate server edits in separate linked server worktrees; record branch/revision/test/next-step
  checkpoints here. Use the game MCP board for game tasks, not as an ownership claim on this repository.
- Run all three Python suites and build a wheel before publication. CI checks Windows and Linux.
- Changes to the game's client pin and smoke/integration checks belong in a separate game PR. Never
  merge automatically or edit another agent's dirty checkout.
