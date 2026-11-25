# PR CI metrics

Open each PR with both its agent and lane label. GitHub can deliver separate opening and label events even when both labels are supplied at creation. The PR CI workflow uses one concurrency group per PR, so a later event can supersede an opening run.

Under **Metrics**, link the run IDs and report these outcomes separately.

| Outcome | What to record |
|---|---|
| Passed | The successful run and head commit. |
| Superseded | The canceled run, the later run, and any canceled jobs or failed aggregate. If all jobs passed before GitHub marked the run canceled, say so. |
| Failed | Runs with a failing check outside a superseded run. Include the failing check, its cause, and the result of the follow-up. List intentional draft and `needs-ron` policy failures here. |
| Skipped or pending | The skipped check or run, its reason, and any result still pending. Missing locked dependencies remain a failure. |

Count every run in the total. Report same-run GitHub retries separately from new runs triggered by a push, label, or PR edit when that distinction is available. When GitHub only shows that a run was canceled, report cancellation and the observed successor without assuming a cause. Keep `ci-ok` strict. It succeeds only when both `checks` and `policy` succeed, and the required run for the current head still must pass before merge.

## Worked example

[PR #56](https://github.com/radkay/ledgerlens/pull/56) had three opening CI runs. Two were superseded and the final run passed. The [opening run](https://github.com/radkay/ledgerlens/actions/runs/45539973621) canceled `checks` and `policy` and failed `ci-ok` because its prerequisites did not succeed. The [intermediate run](https://github.com/radkay/ledgerlens/actions/runs/45539973972) started no jobs. The [settled run](https://github.com/radkay/ledgerlens/actions/runs/45539974061) passed all three checks. This gives one pass, two superseded runs, and zero other failures. The failed aggregate remains visible in the superseded run’s evidence.
