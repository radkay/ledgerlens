# Provision LedgerLens

Run the catalog provisioner as Ron using the matching setup commands in [BOOTSTRAP.md](../docs/BOOTSTRAP.md). Apply these account-level role grants before enabling either GitHub deployment.

| Grant Service Principal User to | On these service principals |
|---|---|
| `ledgerlens-dev-ci` | `ledgerlens-dev-processor` and `ledgerlens-dev-evaluator` |
| `ledgerlens-demo-evidence` | `ledgerlens-demo-processor` and `ledgerlens-demo-evaluator` |

In Databricks workspace settings, open **Identity and access**, select **Manage** next to **Service principals**, then open each processor or evaluator. On its **Permissions** tab, grant **Service principal: User** to the matching deployer. Ron needs Service Principal Manager on the target principal to make this grant. The deployers need workspace access and the SQL access in the [contract](../CONTRACT.md). [Databricks service principal roles](https://docs.databricks.com/aws/en/security/auth/access-control/service-principal-acl#manage-service-principal-roles-using-the-workspace-admin-settings-page)

The dev integration workflow deploys as `ledgerlens-dev-ci` from `dev`. Demo evidence deploys as `ledgerlens-demo-evidence` from `main`. Each deployer owns its environment's bundled jobs and sets their run-as identities to the matching processor and evaluator. If an earlier bootstrap created administrator-owned jobs, Ron should review those jobs and any schedules before enabling the first workflow deployment. The workflow uses the job IDs from the deployer's bundle state.
