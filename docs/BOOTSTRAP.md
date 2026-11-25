# Ron's bootstrap checklist

Review CONTRACT.md and the decision log before the split. Ron initializes and pushes the reviewed initial commit to main, creates dev at the same SHA, then enables the controls below. Use a sacrificial PR to register the ci-ok check before selecting it in the ruleset. Complete the four probes before enabling unattended work.

## Repository settings

Create public radkay/ledgerlens with main as default. Give radkay-bot Write only and keep Ron as the sole administrator. Enable squash merges, merge commits, auto-merge, and automatic head-branch deletion. Disable rebase merges. Create labels agent:codex and needs-ron. Set Actions default permissions to read-only and leave permission to approve PRs with Actions disabled.

| Active ruleset | Exact target | Rules |
|---|---|---|
| ledgerlens-dev-protection | dev | Require PR, zero general approvals, code-owner review, and ci-ok from GitHub Actions. Leave “Require branches to be up to date before merging” disabled. Permit squash only for PR merges. Block force pushes and restrict deletions. No bypass. |
| ledgerlens-main-protection | main | Require PR, one approval, code-owner review, dismissal of stale approvals, and ci-ok from GitHub Actions. Permit merge commits only for PR merges. Block force pushes and restrict deletions. No bypass. |
| ledgerlens-main-ron-only | main | Restrict updates. Add repository administrator as a PR-only bypass, with Ron the sole administrator. Keep that actor off both protection bypass lists. |

The release policy job requires the main PR head to be dev in the same repo. Open the release PR as radkay-bot so Ron can approve and manually merge it. Protected branch deletion restrictions preserve dev after a release merge.

GitHub Actions identifies ci-ok by its publisher and name. CODEOWNERS protects workflow edits. GitHub's label reruns are asynchronous, so open needs-ron PRs as drafts and keep them drafts for the entire hold. Leave auto-merge disabled on a held PR or a protected-file PR awaiting Ron.

Prepare bot-authored PRs for a normal dev increment, a protected-file change, a held draft, and an actual dev-to-main release. Enable squash auto-merge only for the normal increment. Ron supervises `scripts/guardrail_probe.ps1` in a separate bot-authenticated terminal. The opt-in main probe uses an approved release ready to publish if permissions are misconfigured. Close unsuccessful temporary fixture PRs after recording evidence.

When the first three probes are already recorded, run `scripts/guardrail_probe.ps1 -ReleaseOnly -ReleasePr <number> -AttemptApprovedReleaseMerge` after Ron approves the current release head. The script checks the current head's PR CI and Ron's approval, then submits the merge request with the bot's credentials. The CLI's `--admin` flag skips its local merge-state check so the probe exercises GitHub's authorization. Record the denial and verify the PR stays open in the [guardrail evidence](evidence/guardrails.md).

## Databricks and secrets

Create the named workload service principals in the radkay-lab workspace and their required OAuth secrets. Use Ron's user as the operator with delegated workspace authentication. The provisioner uses Ron's workspace-admin CLI profile and the existing shared 2X-Small warehouse ID. Keep this profile and all credentials outside Build agent sessions.

Grant Service Principal User to `ledgerlens-dev-ci` on both `ledgerlens-dev-processor` and `ledgerlens-dev-evaluator`. Grant the same role to `ledgerlens-demo-evidence` on both `ledgerlens-demo-processor` and `ledgerlens-demo-evaluator`. Give the deployers workspace access without an administrator role. The [provisioning guide](../provisioning/README.md) shows where to apply the grants.

The workspace must provide Unity Catalog, serverless jobs, OAuth M2M, and workspace identity APIs for group provisioning. Confirm those capabilities in radkay-lab within the project's $0 boundary. [Databricks Free Edition limitations](https://docs.databricks.com/aws/en/getting-started/free-edition-limitations)

Run from the checkout using Databricks CLI 1.28.0. Apply once per environment with the same Ron profile and warehouse. Reruns reconcile the bootstrap objects.

```bash
uv sync --locked
uv run --no-sync python provisioning/provision.py dev --plan
uv run --no-sync python provisioning/provision.py dev \
  --workspace-profile radkay-lab --warehouse-id "<warehouse-id>" --ron-user "<Ron’s workspace username>"
uv run --no-sync python provisioning/provision.py demo \
  --workspace-profile radkay-lab --warehouse-id "<warehouse-id>" --ron-user "<Ron’s workspace username>"
```

The script creates catalogs, schemas, volumes, tables, views, masks and reviewer groups, adds Ron to each reviewer group, and grants the named identities access. It seeds reasons, the non-secret expected-forms inventory, a digital W-2, and an evaluator-only manifest and answer-key table. Each credentialed workflow deploys both jobs, grants their run-as identities access to the bundle files, and resolves both job IDs from the bundle state. The smoke test runs processing and evaluation, verifies published Gold lineage and accuracy for the same run, and checks that the workflow identity is denied sensitive reads.

In a fresh radkay-lab serverless notebook, leave the environment on its workspace default, record the Environment panel version, and run `import sys; print(sys.version.split()[0])`. Expected results are Standard v6 and Python 3.12.3. Record the observed version and date in D-011. The bundled jobs also require that interpreter and use their configured run-as identities. Capture the live workflow result once the matching environment is ready. [Databricks serverless environment v6](https://docs.databricks.com/aws/en/release-notes/serverless/environment-version/six)

Create GitHub environments ledgerlens-dev and ledgerlens-demo with Selected branches and tags set to dev only and main only respectively. Enter DATABRICKS_CLIENT_SECRET for ledgerlens-dev-ci or ledgerlens-demo-evidence respectively. Set the five matching environment variables from CONTRACT.md, including the application IDs of the processor and evaluator.

Set each readiness variable under **Settings → Secrets and variables → Actions → Variables** when its own catalog, fixtures, credentials, role grants, and publication-capable job definitions are ready. The workflow deploys the jobs on the first eligible run. Leave each variable unset or false until its environment is ready.

| Repository variable | Set to `true` when | Eligible runs |
|---|---|---|
| `LEDGERLENS_INTEGRATION_READY_DEV` | `ledgerlens_dev` and its dev bundle are ready | Pushes to `dev` that match the integration workflow's path filter |
| `LEDGERLENS_INTEGRATION_READY_DEMO` | `ledgerlens_demo` and its demo bundle are ready | Matching pushes to `main` and manual dispatch from `main` |

Each new variable takes effect once the workflow change reaches its branch. Keep the legacy `LEDGERLENS_INTEGRATION_READY` unset or false while any protected branch still runs the earlier workflow.

Create external reader OAuth credentials only for the Community Cloud demo reader and enter them directly there. Verify GitHub Actions and Streamlit OAuth independently. Verify the processor can read expected forms, the evaluator can read the private answer key, and the reader and integration identity are denied access to sensitive tables. Verify the managed dbsql MCP call before publishing claims.

Both Processing job schedules start paused and run daily at 09:00 UTC when enabled. Set `LEDGERLENS_PROCESSING_SCHEDULE_PAUSE_STATUS` to `UNPAUSED` in the matching GitHub environment when automatic runs are wanted. Set it back to `PAUSED` to persist a pause on redeploy. Use the Databricks Jobs UI for an immediate pause.

Bundle deployment creates the matching Databricks App and syncs the existing Streamlit source. The reviewer group can use that app. Start the app from the workspace after verifying Free Edition capacity. Before enabling live app reads, record the resolved app principal and grant it the matching catalog's Gold reader privileges and `CAN_USE` on the existing SQL warehouse. Operator actions use Ron's delegated identity. [Databricks App bundle deployment](https://docs.databricks.com/aws/en/dev-tools/bundles/apps-tutorial)

## Codex and local validation

In Codex CLI 0.172.0, trust the project and start a fresh session per feature worktree. The project config selects the `ledgerlens_build` profile. It extends `:workspace`, explicitly makes the workspace's `.git` writable, keeps `.codex`, `.agents` and `.aws` read-only, enables network access for GitHub and package installs, and sets approvals to `never`. Set radkay-bot as the active GitHub CLI account and commit identity. Build agents run `scripts/push-feature` to push the current feature branch and `gh pr create --repo radkay/ledgerlens --base dev --label agent:codex` to open the PR. Use `scripts/merge-feature PR_NUMBER` for eligible squash auto-merge. Ron handles the initial Git setup and the dev-to-main merge.

Keep GitHub authentication and operations in WSL bot-only. Ron uses his account in a separate environment for repository administration, CODEOWNERS review and release merges.

Use the named profile instead of a legacy `sandbox_mode` override. In a linked Git worktree, the local `.git` file points outside the worktree. Resolve the two backing directories with `git rev-parse --absolute-git-dir` and `git rev-parse --path-format=absolute --git-common-dir`. Give the profile explicit `"write"` entries for those exact absolute paths under `[permissions.ledgerlens_build.filesystem]` in the host's user config. Use a new session after changing permissions. Host-managed read-only mounts and network restrictions still apply. [Codex permission profiles](https://learn.chatgpt.com/docs/permissions#extend-a-profile)

The trusted project loads the forbidden command prefixes from `.codex/rules/default.rules`. The push and merge scripts use fixed arguments and check the active bot identity. Treat prefix rules as local command controls. GitHub rulesets enforce protected refs and merges remotely. [Codex command rules](https://learn.chatgpt.com/docs/agent-configuration/rules)

Use Python 3.12.3 and uv 0.9.18. Regenerate the lock with `uv lock` and validate with `uv sync --locked` followed by `uv run --no-sync python -m unittest discover -s tests`. The workspace runtime probe, bundle deployment and live denial checks require the authenticated radkay-lab admin profile.
