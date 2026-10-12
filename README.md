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
- Compact polling: `skyrunner_collaboration_inbox(recipient, after, limit=10)` returns new addressed/shared
  notes and all active claims, without completed-task history. Save `next_cursor`; drain pages while
  `has_more` is true. Notes are never clipped. Use `read` when you need historical tasks.
- Game: filtered tests, import, smoke, screenshots, balance, scripts, command/switch introspection and
  project-status checks. Long jobs return IDs; poll their owning client instead of restarting them.
- Jobs: status/logs/cancellation. Kernel locks serialize Godot work per checkout across MCP processes, and a
  machine-wide lock runs one Godot job at a time across every checkout and worktree, because two can exhaust
  the memory (`SKYRUNNER_MACHINE_LOCK`: another absolute lock path, or `off`). Shell runs take the same lock
  through `skyrunner-godot-lock [--wait] -- bash tools/test.sh <filter>`.
- GitHub: PR/issue inspection, CI/logs, authorized PR/issue updates and conservative merge/cleanup tools.
  Tool availability is not permission to publish, merge or send messages.
- GitHub for two agents: `github_pr_wait` (CI plus the bot review that follows, in one call), `github_pr_review`
  (a review on the PR, pinned to the head, never by the author agent), `github_merge_queue` (every open PR with
  its CI, the other agent's review and a READY / blocked verdict), `github_pr_diff` (the diff, file by file),
  `github_pr_sync_main` and `skyrunner_merge_resolution` (merge the default branch in; read only how conflicts
  were resolved).
  `github_review_packet` (one bounded read of a PR at its current head for a reviewer: the full head sha, CI with the failing jobs,
  agent reviews marked CURRENT or STALE, unanswered threads, files with the tests that sit beside them, an optional capped diff; it works
  where a shell has no network, because the server makes the calls).
  `github_pr_states` (the open PRs as JSON for a program: full head sha, branch, draft, CI with failing jobs, marked agent reviews, unanswered
  review thread ids, and recently merged numbers; the alert feed reads it instead of calling `gh`).

- Local model (LM Studio, optional): `local_llm_status`, `local_llm_digest` (a log or a PR diff), `local_llm_thread_summaries`
  (what each unanswered review comment asks and what to check, never a verdict or a reply) and `local_llm_triage` (open PRs: the
  review tier from the file paths, no model, plus a one-line summary). Set `SKYRUNNER_LOCAL_LLM` to the server address. The owner's
  large model lives on that server, so these tools use only a model that is **already loaded** (they read `/api/v1/models`, read-only,
  first), never call a load, unload or download endpoint and never use its MCP integrations, run one request at a time across every
  agent's server process, pause after each call and keep reasoning off by default. Their output is a draft for an agent to check.

All 30 original tool names are preserved. The bundled `mcp_introspect.gd` adapter reads the target game's
compiled command/role/switch definitions, so no server implementation needs to live in the game repository.
The test worker and collaboration guide ship inside the package, including wheels.

## Working together

Read [COLLABORATION.md](src/skyrunner_mcp/COLLABORATION.md). Claims coordinate explicit paths, use unique
agent-session owners, renew before expiry and release at handoff. Claims are advisory: they do not
intercept shell/editor writes. Notes persist under the game's Git common directory, do not travel to
unrelated clones and do not wake the other agent. Both agents must read their inbox during work. To share
one board between separate clones on one machine, set `SKYRUNNER_BOARD` to the same absolute `.sqlite3`
path in every client's environment (for example, an existing clone's
`.git/skyrunner-mcp/collaboration.sqlite3`).

## Development and tests

```text
uv sync
uv run python tests/test_guards.py src/skyrunner_mcp/server.py
uv run python tests/test_runtime.py
uv run python tests/test_collaboration.py
uv run python tests/test_github_collab.py
uv run python tests/test_local_llm.py
uv build
```

Tests create temporary fixture projects; they require neither Godot nor a Skyrunner clone and make no
real GitHub writes. Windows/Linux CI runs the same checks. Actual game integration remains a separate
validation in the game's repository. After server updates, reconnect clients; running clients do not
hot-load tools. See [SOURCE.md](SOURCE.md) for extraction provenance.

## Licences

MIT, copyright 2026 jawaman14; the original notice is retained in [LICENSE](LICENSE). Dependencies retain
their own licences. No game assets, radio recordings, credentials or runtime databases are shipped here.
