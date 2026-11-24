# LedgerLens build agents

Read CONTRACT.md before editing. Treat it as the sole source of identifiers, state, schema and environment boundaries. Propose contract amendments through a protected PR. List every accepted tool or framework default or explicitly report none.

Use one feature branch and session per worktree. Use radkay-bot for commits, pushes and PRs, target dev, and apply the actual agent label. Keep attribution lines out of commits and PRs. Use squash auto-merge for eligible PRs. Ron alone approves and merges dev into main.

If a decision needs Ron, finish independent work, open a draft labeled needs-ron with a Questions for Ron section, and leave it held. Keep secrets out of sessions, commits, logs and fixtures. Use synthetic data and $0 services only.

Run uv sync --locked and uv run --no-sync python -m unittest discover -s tests. Use the PR Metrics section. Record decisions and review findings with coordinated IDs. Use Build agents, Processing jobs and Runtime agent as defined in CONTRACT.md.
