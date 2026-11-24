# GitHub guardrail evidence

Observed November 24, 2026. The Build agent used `radkay-bot` for the GitHub operations below.

| Test | Expected result | Observed result |
|---|---|---|
| 1. Ordinary feature PR to dev | A green documentation-only PR can squash auto-merge without a review. | This evidence-only change supplies the ordinary feature PR. Record the merge outcome after GitHub completes it. |
| 2. Protected file PR to dev | Green CI still leaves a protected contract change blocked pending CODEOWNERS review. | [PR #2](https://github.com/radkay/ledgerlens/pull/2) edited the contract and workflow. Its [settled ci-ok](https://github.com/radkay/ledgerlens/actions/runs/45482327749/job/138758661492) was successful. GitHub returned `BLOCKED` with the owner review requested and no approvals. [PR #1](https://github.com/radkay/ledgerlens/pull/1) also had protected edits and merged after Ron approved. |
| 3. needs-ron draft | The policy and ci-ok checks fail, and the draft stays blocked. | [PR #3](https://github.com/radkay/ledgerlens/pull/3) is open, draft, and labeled `needs-ron`. Its [settled run](https://github.com/radkay/ledgerlens/actions/runs/45484560810) passed `checks` and failed `policy` and `ci-ok`. GitHub returned `BLOCKED`. The local feature merge script rejected the draft as ineligible. |
| 4. Approved release PR to main | GitHub denies the bot's merge of an approved, green dev-to-main PR. Ron releases it separately. | Pending the release PR and Ron's approval. |

The opening events on [PR #2](https://github.com/radkay/ledgerlens/actions/runs/45482161004) and [PR #3](https://github.com/radkay/ledgerlens/actions/runs/45484560249) canceled `checks` and `policy`, causing those `ci-ok` jobs to fail. Three additional PR #3 opening-event runs were canceled before jobs started. The settled Test 3 failure exercised the hold.
