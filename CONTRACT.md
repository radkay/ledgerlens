# LedgerLens contract

Proposed for Ron's approval before parallel implementation. All data is synthetic. Repository radkay/ledgerlens, workspace radkay-lab. Pin Databricks Standard serverless environment v6 and Python 3.12.3. The v6 default was observed in radkay-lab on Nov 23. Databricks documents Python 3.12.3 for v6. Capture the workspace default and exact interpreter with the bootstrap runtime probe before enabling integration. Every object reference is catalog-qualified.

## Environments

| Setting | Development | Demo |
|---|---|---|
| Catalog | ledgerlens_dev | ledgerlens_demo |
| GitHub environment | ledgerlens-dev | ledgerlens-demo |
| Credentialed workflow ref | dev | main |
| GitHub repository readiness variable | LEDGERLENS_INTEGRATION_READY_DEV | LEDGERLENS_INTEGRATION_READY_DEMO |
| Processing job | ledgerlens-dev-process | ledgerlens-demo-process |
| Processing job identity | ledgerlens-dev-processor | ledgerlens-demo-processor |
| Evaluation job | ledgerlens-dev-evaluate | ledgerlens-demo-evaluate |
| Evaluator | ledgerlens-dev-evaluator | ledgerlens-demo-evaluator |
| Integration / evidence | ledgerlens-dev-ci | ledgerlens-demo-evidence |
| App Gold reader | ledgerlens-dev-reader | ledgerlens-demo-reader |
| Databricks App | ledgerlens-dev-app | ledgerlens-demo-app |
| Operator | Ron’s Databricks user | Ron’s Databricks user |
| Reviewer group | ledgerlens-dev-reviewers | ledgerlens-demo-reviewers |

Schemas in each catalog: bronze, silver, gold, review, eval, ops. Volumes: bronze.inbox and eval.fixtures. The inbox path is /Volumes/{catalog}/bronze/inbox/{sha256}.pdf. The private answer-key manifest is /Volumes/{catalog}/eval/fixtures/manifest.json. Use the existing shared 2X-Small warehouse by explicit ID. Ron creates the workload identities and secrets, grants the Service Principal User roles, and runs the repository provisioner as admin once per environment. The provisioner creates the Unity Catalog objects and reviewer groups, assigns access, and seeds lookup data. The repository fixture generator supplies only synthetic inputs and their corresponding answer key. No fallback catalog, workspace, job, endpoint, model or warehouse.

For every service principal grant workspace access, USE CATALOG only on its catalog, USE SCHEMA only for the objects below, and warehouse CAN USE only for SQL clients. Unity Catalog CREATE, ownership, and grant options stay with the provisioning identity. Referenced privileges are additive.

| Identity | Grants within own catalog |
|---|---|
| processor | READ VOLUME on bronze.inbox. SELECT on bronze.expected_forms. SELECT and MODIFY on bronze.documents, bronze.extractions, silver.fields, gold.person_year, gold.missing_documents, gold.prefill, gold.lineage, gold.review_summary, gold.confidence_summary, ops.pipeline_runs, ops.publications, ops.document_releases. SELECT on review.decisions and review.reason_codes. |
| evaluator | READ VOLUME on eval.fixtures. SELECT on eval.answer_key, bronze.documents, bronze.extractions and ops.publications. SELECT and MODIFY on gold.extraction_accuracy. |
| ci / evidence | SELECT on the serving views listed below and ops.pipeline_runs. Owns the matching bundled jobs and has Service Principal User on the matching processor and evaluator. |
| reader | SELECT on the serving views listed below. Optional CAN QUERY on the explicit model endpoint after the live probe. |
| Ron | Direct READ VOLUME on bronze.inbox. Direct SELECT on gold.current_document_state, review.current_workbench, review.reason_codes and ops.pipeline_runs. Direct MODIFY on review.decisions. CAN USE on the SQL warehouse. Ron can manage job runs as workspace admin during provisioning and verification. |
| reviewers | Workspace group for authenticated application entitlement. Ron is a member. The group receives no direct data, job, warehouse, or unmask privilege. |

Configure jobs with their matching run-as identities. Use Delta append-only properties on ledgers and snapshots. MODIFY is broader than INSERT, so restrict backend code to parameterized inserts, keep credentials server-side, and test UPDATE and DELETE denial. Unity Catalog masks cover both Silver string value columns when field_name identifies SSN or TIN. Typed projections and Gold exclude identifier values. The workbench masks identifiers independently of the serving path. No workload other than the evaluator reads the answer key. Use separate identities for dev and demo.

The credentialed integration workflow deploys the matching bundle before running the processing job. On dev, ledgerlens-dev-ci deploys and owns the dev jobs. On main, ledgerlens-demo-evidence deploys and owns the demo jobs. Both deployers use workspace access without an administrator role and grant their processor and evaluator read access to the deployed source files. The workflow resolves the processing job ID from the matching bundle deployment state.

GitHub environment secret: DATABRICKS_CLIENT_SECRET. Environment variables: DATABRICKS_HOST, DATABRICKS_CLIENT_ID, DATABRICKS_WAREHOUSE_ID, DATABRICKS_PROCESSOR_APPLICATION_ID, DATABRICKS_EVALUATOR_APPLICATION_ID. Streamlit Community Cloud receives the demo reader's corresponding host, client ID, secret and warehouse ID. Authenticated operator actions use Ron’s delegated Databricks user identity and require membership in the matching reviewer group. The public app uses only the reader identity. Each repository readiness variable is false or unset until its own environment is provisioned and its workflow can deploy and run the publication-capable jobs for the live smoke test. Only the demo bundle includes a Databricks App. The demo reviewer group has CAN_USE on it. Its host-managed principal receives reader grants once its resolved identity is recorded. Record the actual hosted model endpoint and managed dbsql MCP endpoint before claiming hands-on use.

The optional GitHub environment variable LEDGERLENS_PROCESSING_SCHEDULE_PAUSE_STATUS selects PAUSED or UNPAUSED for the matching Processing job schedule. Both targets start PAUSED. The periodic schedule is daily at 09:00 UTC and invokes the same Processing job as a manual run.

## Tables

IDs and keys are STRING unless stated, timestamps are UTC TIMESTAMP, and tax_year is INT. A question mark indicates nullable. JSON columns are strings with validated contracts. Grain keys are logical and checked before publication.

| Table | Columns and grain |
|---|---|
| bronze.documents | doc_id, content_sha256, file_path, received_at, run_id, status. One received row per content hash. |
| bronze.expected_forms | person_key, tax_year, expected_form. Key person/year/expected form. Contains synthetic inventory only. |
| bronze.extractions | extraction_id, doc_id, run_id, extracted_at, extractor_name, extractor_version, model_name?, model_version?, status, raw_json, fields_json, error_code?. One row per extraction attempt. |
| review.reason_codes | action, reason_code, enabled BOOLEAN, description. Key action/reason. |
| review.decisions | decision_id, doc_id, extraction_id?, field_id?, action, reason_code, corrected_value?, actor_id, decided_at. One row per decision ID. |
| silver.fields | run_id, doc_id, field_id, field_name, field_type, extracted_value?, final_value?, final_decimal DECIMAL(18,2)?, final_integer BIGINT?, final_date DATE?, value_source, extraction_id, decision_id?, extractor_name, extractor_version, model_name?, model_version?, confidence DOUBLE?, bbox_json?, status, person_key, tax_year, form_type. Key run/field. |
| gold.person_year | run_id, gold_row_id, person_key, tax_year, wages DECIMAL(18,2)?, other_income DECIMAL(18,2)?, mortgage_interest DECIMAL(18,2)?. Key run/person/year. |
| gold.missing_documents | run_id, gold_row_id, person_key, tax_year, expected_form, status. Key run/person/year/expected form. |
| gold.prefill | run_id, gold_row_id, person_key, tax_year, form_type, output_field, output_value?. Key run/person/year/form/field. |
| gold.lineage | run_id, gold_row_id, output_field, doc_id, field_id, extraction_id, decision_id?, extractor_name, extractor_version, model_name?, model_version?. Key run/Gold row/output field/source field. |
| gold.review_summary | run_id, status, document_count BIGINT. Key run/status. |
| gold.confidence_summary | run_id, form_type, confidence_bucket, count BIGINT. Key run/form type/confidence bucket. Count is the number of released Silver field rows in the bucket. |
| gold.extraction_accuracy | run_id, form_type, variant, expected_count BIGINT, exact_match_count BIGINT. Key run/form/variant, evaluator-written. |
| eval.answer_key | fixture_id, doc_id, field_id, field_name, expected_value, variant, form_type. Key fixture/field. |
| ops.pipeline_runs | run_id, event_seq BIGINT, event_at, status, detail_code?, job_run_id, trigger_type. Key run/event sequence. |
| ops.document_releases | run_id, doc_id, extraction_id, status. Key run/document. Status is released or recalled. Write the applicable release transitions before the publication marker. |
| ops.publications | publication_seq BIGINT, run_id, published_at, input_digest. Unique run and strictly increasing publication sequence. |

Serving views in gold: latest_publication (run_id, publication_seq, published_at), current_person_year, current_missing_documents, current_prefill, current_lineage, current_review_summary, current_confidence_summary, current_extraction_accuracy, current_document_state. Gold snapshot result views join to the publication ledger to expose published history only. Resolve the latest run once per request, then filter every snapshot read by that run ID. Accuracy can arrive after publication and the UI reports pending for that run. The dashboard displays live document status from gold.current_document_state alongside pinned publication metrics. This view exposes doc_id, status and state_at. It folds received documents, extraction attempts, accepted decisions and document release events ordered by timestamp, then event kind (received, extraction, decision, publication), then stable event ID. Release events join ops.publications, so incomplete runs do not advance document state. The masked review.current_workbench uses the same state projection and includes the latest Silver field rows for the current extraction when available. Missing-document output compares bronze.expected_forms to the release. Missing forms can have no contributing document fields.

## State and publication

Document states: received, extracted, needs_review, auto_approved, approved, rejected, released, recalled. Extraction attempt status is extracted, needs_review, auto_approved or failed. An extracted attempt awaits classification. A failed attempt enters needs_review. Approve and correct_field transition needs_review or approved to approved. Reject transitions needs_review or approved to rejected. Reprocess from needs_review, rejected or recalled requests a fresh attempt and returns the document to received until that attempt arrives. Publication transitions eligible approved or auto_approved rows to released. Recall applies to released documents and transitions to recalled when the next publication commits its recalled marker. Each release marker refers to the affected extraction. A decision scoped to an extraction applies only while that extraction is current. Only released documents contribute to consumer values.

Decision actions and initial reasons: approve/verified, correct_field/extraction_error, reject/invalid_document, reprocess/low_confidence, recall/release_correction. Validate the reason against the managed registry. Replay accepted decisions ordered by (decided_at, decision_id), against a frozen input watermark vector. decision_id and content SHA-256 enforce logical idempotency. A repeated ID with a different payload fails validation. value_source is extraction or human_correction. Job events are queued, running, validated, published, failed or skipped_duplicate. Each event records the invocation trigger_type as manual or scheduled. Map Databricks job.trigger.type values one_time to manual and periodic to scheduled. Integration run-now and workbench invocations are manual.

Use content SHA-256 as doc_id. For field_id, SHA-256 the UTF-8 text `{doc_id}:{canonical_field_name}:{occurrence}` with occurrence numbered from one. Each attempt has a stable extraction_id, including deliberate reprocess attempts. Manual and scheduled invocations run the same job, with max concurrent runs 1. Use the digest of the frozen logical inputs and processing configuration to identify repeated triggers. Retries resume incomplete appends under the same logical run_id. Run-stamp all Silver and Gold publication payloads. Validate completeness, uniqueness, release states and lineage coverage before appending the publication record. A failed or partial run leaves the last successful release visible. Consumers pin their reads to one published run. A recall removes the document from the next release without changing earlier release contents.

Required acceptance tests cover derived status and publication-gated recall, partial and failed runs, identical replay output, duplicate PDF, duplicate decision, duplicate job trigger, original lineage retention, reader and integration denial checks, and reviewer masking.

After replay and release selection, the Processing job groups released Silver field rows by form type and original extraction confidence. Store bucket labels `[0, 0.5)`, `[0.5, 0.8)`, `[0.8, 1]`, and `Unreported` for null scores. Validate that scores fall within [0, 1] and bucket counts sum to the released field count for each form type. Append the run-stamped counts before the publication marker. The rows expose aggregate counts without field or document identifiers or values. Grant the reader SELECT on gold.current_confidence_summary.

## Fixture interface

Synthetic SSNs use area 000 and synthetic EIN-formatted TINs use prefix 00. Derive person keys independently of identifiers.

Manifest fields: fixture_id, filename, sha256, form_type (W-2, 1099, 1098), variant (digital, scanned), tax_year, person_key, expected form inventory. Private eval entries add expected field names and values. Scanned variants are raster-only PDFs. Extractors accept bytes, doc_id and run_id and return the Bronze attempt record with field entries of field_name, occurrence, extracted_value, confidence (0–1 or null), and bbox (page index and normalized coordinates or null). Missing versions and unreported values stay null. Adapters: pypdf/pdfplumber and ai_parse_document. Accuracy uses original extraction and includes expected missing fields in its denominator.

## Glossary

- **Build agents** are Codex instances writing the code.
- **Processing jobs** are Databricks jobs that move data through the layers.
- **Runtime agent** is the LLM in the app that queries Gold through the MCP server.
- **Published run** is a validated snapshot committed through ops.publications.
