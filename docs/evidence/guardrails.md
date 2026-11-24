# GitHub guardrail evidence

Observed November 24, 2026. The Build agent used `radkay-bot` for the GitHub operations below.

| Test | Expected result | Observed result |
|---|---|---|
| 1. Ordinary feature PR to dev | A green documentation-only PR can squash auto-merge without a review. | [PR #4](https://github.com/radkay/ledgerlens/pull/4) had [green ci-ok](https://github.com/radkay/ledgerlens/actions/runs/45484849384). GitHub recorded a squash merge by `radkay-bot` as `0e44e11` at 20:52:23 UTC, with no reviews. |
| 2. Protected file PR to dev | Green CI still leaves a protected contract change blocked pending CODEOWNERS review. | [PR #2](https://github.com/radkay/ledgerlens/pull/2) edited the contract and workflow. Its [settled ci-ok](https://github.com/radkay/ledgerlens/actions/runs/45482327749/job/138758661492) was successful. GitHub returned `BLOCKED` with the owner review requested and no approvals. [PR #1](https://github.com/radkay/ledgerlens/pull/1) also had protected edits and merged after Ron approved. |
| 3. needs-ron draft | The policy and ci-ok checks fail, and the draft stays blocked. | [PR #3](https://github.com/radkay/ledgerlens/pull/3) is open, draft, and labeled `needs-ron`. Its [settled run](https://github.com/radkay/ledgerlens/actions/runs/45484560810) passed `checks` and failed `policy` and `ci-ok`. GitHub returned `BLOCKED`. The local feature merge script rejected the draft as ineligible. |
| 4. Approved release PR to main | GitHub denies the bot's merge of an approved, green dev-to-main PR. Ron releases it separately. | Passed. Ron approved [PR #6](https://github.com/radkay/ledgerlens/pull/6) on head `8abb0152ac1d1e2d9d4526e7c4fdc40d8e9102f6`, with [green ci-ok](https://github.com/radkay/ledgerlens/actions/runs/45488415158/job/138780057009). GitHub denied the single merge request from `radkay-bot` at 21:14 UTC. The probe verified the PR remained open and `main` stayed at `e4b6caf`. |

GitHub CLI returned this exact error for Test 4.

```text
GraphQL: Repository rule violations found

Cannot update this protected ref.

 (mergePullRequest)
```

The opening runs for [PR #2](https://github.com/radkay/ledgerlens/actions/runs/45482161004), [PR #3](https://github.com/radkay/ledgerlens/actions/runs/45484560249), [PR #4](https://github.com/radkay/ledgerlens/actions/runs/45484849122), [PR #5](https://github.com/radkay/ledgerlens/actions/runs/45485994797), and [PR #6](https://github.com/radkay/ledgerlens/actions/runs/45486051935) canceled `checks` and `policy`, causing those `ci-ok` jobs to fail. Three additional PR #3 opening-event runs were canceled before jobs started. The [PR #3 Metrics edit run](https://github.com/radkay/ledgerlens/actions/runs/45484736846) also failed `policy` and `ci-ok` as expected for the held draft. No PR CI jobs were skipped in these probes. The [dev integration job after the readiness split](https://github.com/radkay/ledgerlens/actions/runs/45486547075) was skipped because its readiness gate evaluated false.
