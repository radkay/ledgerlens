# Review log

Record actual review findings, including Claude's review of the plan and merged PRs when evidence is available. Reserve IDs with the integration lead.

| ID | Finding | What was produced | What was wrong | Fix and verification | Caught by |
|---|---|---|---|---|---|
| R-001 | Bootstrap lockfile hand-written | uv.lock was typed by hand because pinned uv was unavailable. | Correctness and reproducibility require a tool-generated lockfile. | Regenerated with uv lock 0.9.18 and verified with uv sync --locked on Python 3.12.3. | Claude's review |
| R-002 | Summary omitted a failed check | The bootstrap handoff summary reported all tests passing while an ad hoc provisioning catalog-scope check had failed with `AssertionError: catalog scope`. | This was a process and correctness issue. Under review-after-merge, a reviewer relying on the summary would miss the failure. | Reproduced the failure on Python 3.12.3 and corrected the false-positive check. A regression test now checks the catalog of every DDL statement. [AGENTS.md](../AGENTS.md) requires every summary to list all failed, canceled, or skipped checks. | Claude's review |
