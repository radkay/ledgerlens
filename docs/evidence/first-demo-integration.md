# First demo integration evidence

[Release PR #55](https://github.com/radkay/ledgerlens/pull/55) merged into `main` at `09e544c`. The [first demo Actions run](https://github.com/radkay/ledgerlens/actions/runs/45540750902) completed successfully on November 25, 2026 UTC. The release workflow selected the demo target and authenticated as `ledgerlens-demo-evidence`, with the environment-specific run-as identities from the [contract](../../CONTRACT.md).

| Stage | Verified outcome |
|---|---|
| Deploy | Bundle deployment synced 48 files and created four resources, including the bundled App and its permission resource. |
| Processing | Databricks run `1067035986991796` succeeded at 03:35:34 UTC. |
| Evaluation | Databricks run `138384269145245` succeeded at 03:36:12 UTC. |
| Gold smoke | The Processing invocation produced or reused the current publication. Lineage and digital W-2 accuracy checks passed for that publication. |
| Scoped reads | The evidence principal's queries on `silver.fields` and `eval.answer_key` both returned confirmed permission denials. |

The [release runbook](../RUNBOOK-release.md) covers public app deployment, signed-out reader verification, and the next release's evidence links.
