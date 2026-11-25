# 6 a.m. Central release runbook

Ron runs these steps in his separate administrator terminal and browser. The Build agent account prepares PRs against `dev`. Schedule one demo integration run and keep both Processing schedules paused through verification.

## Before starting

Keep both Processing schedules paused. Use the same reviewed `dev` SHA for both catalogs and the release PR. [#51](https://github.com/radkay/ledgerlens/pull/51) adds published history and changes `current_*` to the latest snapshot. Match the schema and consumers in this order.

1. **Prepare the repository.** The [App in #68](https://github.com/radkay/ledgerlens/pull/68) and the [MCP implementation from #63](https://github.com/radkay/ledgerlens/pull/63) pin a run on the `published_*` views. Land their code together to share one standard dev integration run. Keep the new reader surfaces inactive and `LEDGERLENS_MCP_SMOKE_READY_DEV` unset. [#67](https://github.com/radkay/ledgerlens/pull/67) keeps standard smoke on the currently provisioned view family until its environment flag changes.
2. **Approve masking if it is in this release.** [#52](https://github.com/radkay/ledgerlens/pull/52) follows the App's exact-token regression and CODEOWNERS approval on its merge head. Include it in the reviewed SHA before provisioning if its migration is part of this release. Provisioning creates `ledgerlens-dev-identifier-readers` and `ledgerlens-demo-identifier-readers` without members. Ron manages membership explicitly.
3. **At 6 a.m., apply dev first.** Run the regular dev command below. Verify the new Gold views and dev reader and CI grants. Verify both Silver mask bindings and repeat the following identity checks after the demo apply. Set `LEDGERLENS_SMOKE_PUBLISHED_VIEWS=true` in the `ledgerlens-dev` GitHub environment only after those checks. Defer optional live dev probes until their credentials and endpoints are configured and the demo quota is reserved.
4. **Apply demo second.** When the App consumers are release-ready, run the regular demo command from the same SHA. Confirm the demo views and grants. If reviewed [#65](https://github.com/radkay/ledgerlens/pull/65) is in the SHA and the hosted App will be enabled, run its app-only reader grant after the regular demo apply. Then set `LEDGERLENS_SMOKE_PUBLISHED_VIEWS=true` in `ledgerlens-demo`. Ron merges the release with a merge commit and confirms the one demo run before deploying the new reader surface.

### Identifier masking checks

| Identity and surface | Expected identifier value |
|---|---|
| Matching identifier-readers group querying either Silver value column | Full value |
| Matching reviewer group in the workbench, including a reviewer with full-value group membership | Safe last-four token, such as `****1234` |
| Other identities that are granted access to Silver | `NULL` |
| Silver on the public reader or CI identity | Query denied |

Use only synthetic records for verification. Confirm the last-four projection on both value columns and confirm the workbench masks malformed values.

## 1. Reconcile dev, then demo

From Ron's checkout of `radkay/ledgerlens`, use Databricks CLI 1.28.0, uv 0.9.18, GitHub CLI, and jq. Fetch the release candidate and use a separate worktree. The commands prompt for the existing radkay-lab workspace origin, Ron's workspace username, and the shared 2X-Small warehouse ID. Authenticate interactively as Ron.

```bash
git fetch origin dev main
release_checkout=$(mktemp -d)
git worktree add --detach "$release_checkout" origin/dev

(
  set -e -o pipefail
  cd "$release_checkout"
  read -r -p 'radkay-lab HTTPS workspace origin: ' workspace_host
  read -r -p 'Ron workspace username: ' ron_workspace_user
  read -r -p 'Existing shared 2X-Small SQL warehouse ID: ' warehouse_id

  databricks --version
  databricks auth login --host "$workspace_host" --profile radkay-lab
  databricks current-user me --profile radkay-lab --output json |
    jq -e --arg username "$ron_workspace_user" '.userName == $username'

  export UV_CACHE_DIR=.uv-cache
  uv sync --locked
  uv run --no-sync python provisioning/provision.py dev \
    --workspace-profile radkay-lab --warehouse-id "$warehouse_id" --ron-user "$ron_workspace_user"
  printf 'ledgerlens_dev provisioned at %s\n' "$(git rev-parse --short HEAD)"
)


```

Verify dev and set its history-view smoke variable before starting demo. Use the same worktree, non-secret warehouse ID and workspace username, and the existing authenticated Ron profile.

```bash
(
  set -e
  cd "$release_checkout"
  read -r -p 'Ron workspace username: ' ron_workspace_user
  read -r -p 'Existing shared 2X-Small SQL warehouse ID: ' warehouse_id
  export UV_CACHE_DIR=.uv-cache
  uv run --no-sync python provisioning/provision.py demo \
    --workspace-profile radkay-lab --warehouse-id "$warehouse_id" --ron-user "$ron_workspace_user"
  printf 'ledgerlens_demo provisioned at %s\n' "$(git rev-parse --short HEAD)"
)
```

When #65 is included and the hosted App will be enabled, run the App-only grant after demo provisioning. Keep its identity record outside the repository.

```bash
(
  set -e
  cd "$release_checkout"
  read -r -p 'Ron workspace username: ' ron_workspace_user
  read -r -p 'Existing shared 2X-Small SQL warehouse ID: ' warehouse_id
  export UV_CACHE_DIR=.uv-cache
  uv run --no-sync python provisioning/provision.py demo --demo-app-reader \
    --workspace-profile radkay-lab --warehouse-id "$warehouse_id" --ron-user "$ron_workspace_user"
)
```

Both commands must exit 0. Start demo after the dev verification succeeds. Verify the seeded fixture and manifest, the scoped reviewer and reader grants, and the existing warehouse `CAN_USE` grants in each catalog. [Workspace-user CLI authentication](https://docs.databricks.com/aws/en/dev-tools/cli/authentication)

## 2. Merge the release PR

```bash
gh pr list --repo radkay/ledgerlens --state open --base main --head dev \
  --json number,headRefOid,title,url
```

Use the open bot-authored same-repository `dev` → `main` release PR. If there is none, have the Build agent open one with the current merged-PR list, `lane:first`, and `agent:codex`. Check that its head matches the SHA provisioned above, review that head as Ron, and require green `ci-ok`. In Ron's GitHub browser, select **Create a merge commit**. Use the administrator PR-only bypass for the main update restriction if GitHub presents the bypass control, and confirm the merge commit. Leave the dev branch in place. [Repository rules and release controls](BOOTSTRAP.md#repository-settings) · [GitHub merge methods](https://docs.github.com/en/pull-requests/how-tos/merge-and-close-pull-requests/merging-a-pull-request)

## 3. Confirm the demo integration run

Confirm `LEDGERLENS_INTEGRATION_READY_DEMO=true` is a repository variable in GitHub. The `ledgerlens-demo` environment is restricted to `main`. It has `DATABRICKS_HOST`, `DATABRICKS_WAREHOUSE_ID`, `DATABRICKS_CLIENT_ID` for `ledgerlens-demo-evidence`, and the processor and evaluator application IDs as `DATABRICKS_PROCESSOR_APPLICATION_ID` and `DATABRICKS_EVALUATOR_APPLICATION_ID`. Its environment secret `DATABRICKS_CLIENT_SECRET` belongs to `ledgerlens-demo-evidence`.

```bash
git fetch origin main
release_sha=$(git rev-parse origin/main)
gh run list --repo radkay/ledgerlens --workflow integration.yml --branch main \
  --commit "$release_sha" --limit 1 --json databaseId,headSha,status,conclusion,url
demo_run_id=$(gh run list --repo radkay/ledgerlens --workflow integration.yml --branch main \
  --commit "$release_sha" --limit 1 --json databaseId --jq '.[0].databaseId // empty')
if [ -n "$demo_run_id" ]; then
  gh run watch "$demo_run_id" --repo radkay/ledgerlens --exit-status
  gh run view "$demo_run_id" --repo radkay/ledgerlens --log
fi
```

Wait for the eligible push run to appear. If the release changed only paths outside the integration filter and there is no run for that commit, dispatch it once with `gh workflow run integration.yml --repo radkay/ledgerlens --ref main`. Confirm bundle deploy, Processing, Evaluation, published Gold lineage and accuracy, and denials for `silver.fields` and `eval.answer_key`. Keep the Actions URL and run IDs. On a quota, usage-limit, or capacity error, stop, record the time and run link in a draft PR titled **Quota hit**, and preserve the error. Schedule no retry or workaround.

Use the [first demo evidence record](evidence/first-demo-integration.md) as a format for capturing the next verified run.

## 4. Deploy the public Streamlit app

In radkay-lab, open the service principal `ledgerlens-demo-reader` and generate an OAuth secret for that principal. Enter the secret directly in the Streamlit Community Cloud app's **Advanced settings → Secrets**. Create a public Community Cloud app from repository `radkay/ledgerlens`, branch `main`, entry point `src/ledgerlens/app/community_cloud.py`, and Python 3.12. The adjacent `requirements.txt` supplies dependencies. [Databricks OAuth secret setup](https://docs.databricks.com/aws/en/dev-tools/auth/oauth-m2m) · [Community Cloud secrets](https://docs.streamlit.io/deploy/streamlit-community-cloud/deploy-your-app/secrets-management)

| Cloud Secrets key | Source |
|---|---|
| `LEDGERLENS_DATA_SOURCE` | `live` |
| `LEDGERLENS_ENVIRONMENT` | `demo` |
| `DATABRICKS_HOST` | radkay-lab HTTPS workspace origin |
| `DATABRICKS_WAREHOUSE_ID` | Existing shared 2X-Small warehouse |
| `DATABRICKS_CLIENT_ID` | `ledgerlens-demo-reader` application ID |
| `DATABRICKS_CLIENT_SECRET` | Newly generated `ledgerlens-demo-reader` OAuth secret |

Keep the demo deployer secret in GitHub Actions and the demo reader secret in Community Cloud. Ron's personal credentials stay in his administrator terminal. The [empty template](../src/ledgerlens/app/.streamlit/secrets.toml.example) matches the public entry point.

## 5. Verify signed-out reader access

Open the public app in a fresh private window signed out of GitHub, Streamlit, and Databricks. Confirm it loads published demo Gold without a sign-in prompt. Open published outputs, confirm run-pinned totals and missing-form status, drill into reverse lineage, and download a pre-filled synthetic form. Confirm the workbench, batch controls, private previews, and raw or evaluation data are unavailable on that public surface. Test authenticated reviewer actions separately in the designated review environment before capturing the workbench GIF.

## 6. Finalize links and evidence

Replace the four `{{RELEASE:...}}` entries in [README.md](../README.md) with the verified public app URL, the demo Actions URL, and the two synthetic walkthrough GIF links. Refresh the build metrics with `python3 scripts/build_metrics.py --update-readme` and run `python3 scripts/release_readiness.py` to list any placeholders still open. Submit documentation updates to `dev` through a PR and use the same release flow to publish them on `main`.
