# LedgerLens build agents

Read CONTRACT.md before editing. Treat it as the sole source of identifiers, state, schema and environment boundaries. Propose contract amendments through a protected PR. List every accepted tool or framework default or explicitly report none.

Use one feature branch and session per worktree. Use radkay-bot for commits, pushes and PRs, target dev, and apply both the actual agent label and the owning lane label when creating the PR. Keep attribution lines out of commits and PRs. Use squash auto-merge for eligible PRs. Ron alone approves and merges dev into main.

Use one lane label per PR. Use `lane:first` for bootstrap, integration and cross-lane changes coordinated by First, `lane:extraction` for fixtures and extraction, `lane:processing` for Processing jobs and evaluation code, `lane:app` for the dashboard and workbench, `lane:governance` for catalog governance, and `lane:mcp` for Runtime agent and MCP evidence.

Keep WSL bot-only for GitHub. Authenticate and perform GitHub operations there as radkay-bot. Ron uses a separate environment for his account and administrator operations.

Use the needs-ron label only for decisions Ron must make. Finish independent work, open a draft with a Questions for Ron section, and leave it held. A PR waiting on another lane stays a draft without needs-ron. Keep secrets out of sessions, commits, logs and fixtures. Use synthetic data and $0 services only.

From the repository root, set `UV_CACHE_DIR=.uv-cache` for uv commands because Build agent sessions cannot write to the user cache. Run `uv sync --locked` and `uv run --no-sync python -m unittest discover -s tests`. Use the PR Metrics section. Record decisions and review findings with coordinated IDs. Use Build agents, Processing jobs and Runtime agent as defined in CONTRACT.md.

Tests must run when they depend on packages in the lockfile. Missing locked dependencies must fail the test run. Count a dependency-based skip in CI as a failure and address it before merge.

List every failed, canceled, or skipped check in the PR Metrics and every summary, with its cause and disposition. Required checks must succeed before merge.
