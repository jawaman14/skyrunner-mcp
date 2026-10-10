# Skyrunner MCP

A standalone MCP server for the [Skyrunner game](https://github.com/jawaman14/skyrunner).
Claude and Codex use the same tools and share durable notes and atomic file claims across linked game
worktrees. The server has its own repository, package and Windows/Linux CI; the game is an explicit
runtime target, not a bundled dependency.

## Run

Install Python 3.11+ and uv. Clone this repository, set `SKYRUNNER_ROOT` to an absolute path to a game
checkout containing `project.godot`, and run:

```text
uv run --project /absolute/path/to/skyrunner-mcp skyrunner-mcp
```

The server speaks MCP over stdio. Configure the same command in Claude's `.mcp.json` or Codex's MCP
settings. Set `SKYRUNNER_GODOT` to a Godot 4.7.2 executable on Windows/macOS; on Linux the game's pinned
`tools/get_godot.sh` is used if no executable is configured. Do not run a raw stdio server in a terminal
and expect it to act as an HTTP service.

You can also install/run a pinned commit without a local server clone:

```text
uvx --from git+https://github.com/jawaman14/skyrunner-mcp.git@<commit-sha> skyrunner-mcp
```

Keep the commit pinned in client configurations and review/test an update before changing it.
Never store tokens in the repository. Set `GH_TOKEN`, `GITHUB_TOKEN` or `SKYRUNNER_GITHUB_TOKEN`, or use
`gh auth login`, for authenticated GitHub access. Public reads work anonymously; writes refuse before
dispatch without authentication. GitHub tools operate on the **game checkout's origin**, not this repo.

## Tools

- Collaboration: `skyrunner_collaboration_start`, `read`, `claim`, `update`, `note` (full names retain the
  `skyrunner_collaboration_` prefix). Start with `start`, verify target/revision and read the shared inbox.
- Game: filtered tests, import, smoke, screenshots, balance, scripts, command/switch introspection and
  project-status checks. Long jobs return IDs; poll their owning client instead of restarting them.
- Jobs: status/logs/cancellation. Kernel locks serialize Godot work per checkout across MCP processes;
  each worktree has a separate cache. Independent worktrees still compete for host memory.
- GitHub: PR/issue inspection, CI/logs, authorized PR/issue updates and conservative merge/cleanup tools.
  Tool availability is not permission to publish, merge or send messages.

All 30 existing tool names are preserved. The bundled `mcp_introspect.gd` adapter reads the target game's
compiled command/role/switch definitions, so no server implementation needs to live in the game repository.
The test worker and collaboration guide ship inside the package, including wheels.

## Working together

Read [COLLABORATION.md](src/skyrunner_mcp/COLLABORATION.md). Claims coordinate explicit paths, use unique
agent-session owners, renew before expiry and release at handoff. Claims are advisory: they do not
intercept shell/editor writes. Notes persist under the game's Git common directory, do not travel to
unrelated clones and do not wake the other agent. Both agents must read their inbox during work.

## Development and tests

```text
uv sync
uv run python tests/test_guards.py src/skyrunner_mcp/server.py
uv run python tests/test_runtime.py
uv run python tests/test_collaboration.py
uv build
```

Tests create temporary fixture projects; they require neither Godot nor a Skyrunner clone and make no
real GitHub writes. Windows/Linux CI runs the same checks. Actual game integration remains a separate
validation in the game's repository. After server updates, reconnect clients; running clients do not
hot-load tools. See [SOURCE.md](SOURCE.md) for extraction provenance.

## Licences

MIT, copyright 2026 jawaman14; the original notice is retained in [LICENSE](LICENSE). Dependencies retain
their own licences. No game assets, radio recordings, credentials or runtime databases are shipped here.
