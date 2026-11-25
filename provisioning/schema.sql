CREATE CATALOG IF NOT EXISTS {catalog};

CREATE SCHEMA IF NOT EXISTS {catalog}.bronze;
CREATE SCHEMA IF NOT EXISTS {catalog}.silver;
CREATE SCHEMA IF NOT EXISTS {catalog}.gold;
CREATE SCHEMA IF NOT EXISTS {catalog}.review;
CREATE SCHEMA IF NOT EXISTS {catalog}.eval;
CREATE SCHEMA IF NOT EXISTS {catalog}.ops;

CREATE VOLUME IF NOT EXISTS {catalog}.bronze.inbox;
CREATE VOLUME IF NOT EXISTS {catalog}.eval.fixtures;
CREATE VOLUME IF NOT EXISTS {catalog}.review.masked_previews;

CREATE TABLE IF NOT EXISTS {catalog}.bronze.documents (
  doc_id STRING NOT NULL, content_sha256 STRING NOT NULL, file_path STRING NOT NULL,
  received_at TIMESTAMP NOT NULL, run_id STRING NOT NULL, status STRING NOT NULL
) USING DELTA;

CREATE TABLE IF NOT EXISTS {catalog}.bronze.extractions (
  extraction_id STRING NOT NULL, doc_id STRING NOT NULL, run_id STRING NOT NULL,
  extracted_at TIMESTAMP NOT NULL, extractor_name STRING NOT NULL, extractor_version STRING NOT NULL,
  model_name STRING, model_version STRING, status STRING NOT NULL, raw_json STRING NOT NULL,
  fields_json STRING NOT NULL, error_code STRING
) USING DELTA;

CREATE TABLE IF NOT EXISTS {catalog}.bronze.expected_forms (
  person_key STRING NOT NULL, tax_year INT NOT NULL, expected_form STRING NOT NULL
) USING DELTA;

CREATE TABLE IF NOT EXISTS {catalog}.review.reason_codes (
  action STRING NOT NULL, reason_code STRING NOT NULL, enabled BOOLEAN NOT NULL, description STRING NOT NULL
) USING DELTA;

MERGE INTO {catalog}.review.reason_codes AS target
USING (SELECT * FROM VALUES
  ('approve', 'verified', true, 'Verified extraction'),
  ('correct_field', 'extraction_error', true, 'Corrected an extraction error'),
  ('reject', 'invalid_document', true, 'Invalid document'),
  ('reprocess', 'low_confidence', true, 'Retry low-confidence extraction'),
  ('recall', 'release_correction', true, 'Correct a released document')
  AS seed(action, reason_code, enabled, description)) AS source
ON target.action = source.action AND target.reason_code = source.reason_code
WHEN NOT MATCHED THEN INSERT *;

CREATE TABLE IF NOT EXISTS {catalog}.review.decisions (
  decision_id STRING NOT NULL, doc_id STRING NOT NULL, extraction_id STRING, field_id STRING,
  action STRING NOT NULL, reason_code STRING NOT NULL, corrected_value STRING,
  actor_id STRING NOT NULL, decided_at TIMESTAMP NOT NULL
) USING DELTA;

CREATE TABLE IF NOT EXISTS {catalog}.silver.fields (
  run_id STRING NOT NULL, doc_id STRING NOT NULL, field_id STRING NOT NULL,
  field_name STRING NOT NULL, field_type STRING NOT NULL, extracted_value STRING, final_value STRING,
  final_decimal DECIMAL(18,2), final_integer BIGINT, final_date DATE, value_source STRING NOT NULL,
  extraction_id STRING NOT NULL, decision_id STRING, extractor_name STRING NOT NULL,
  extractor_version STRING NOT NULL, model_name STRING, model_version STRING,
  confidence DOUBLE, bbox_json STRING, status STRING NOT NULL, person_key STRING NOT NULL,
  tax_year INT NOT NULL, form_type STRING NOT NULL
) USING DELTA;

CREATE TABLE IF NOT EXISTS {catalog}.gold.person_year (
  run_id STRING NOT NULL, gold_row_id STRING NOT NULL, person_key STRING NOT NULL, tax_year INT NOT NULL,
  wages DECIMAL(18,2), other_income DECIMAL(18,2), mortgage_interest DECIMAL(18,2)
) USING DELTA;

CREATE TABLE IF NOT EXISTS {catalog}.gold.missing_documents (
  run_id STRING NOT NULL, gold_row_id STRING NOT NULL, person_key STRING NOT NULL,
  tax_year INT NOT NULL, expected_form STRING NOT NULL, status STRING NOT NULL
) USING DELTA;

CREATE TABLE IF NOT EXISTS {catalog}.gold.prefill (
  run_id STRING NOT NULL, gold_row_id STRING NOT NULL, person_key STRING NOT NULL, tax_year INT NOT NULL,
  form_type STRING NOT NULL, output_field STRING NOT NULL, output_value STRING
) USING DELTA;

CREATE TABLE IF NOT EXISTS {catalog}.gold.lineage (
  run_id STRING NOT NULL, gold_row_id STRING NOT NULL, output_field STRING NOT NULL,
  doc_id STRING NOT NULL, field_id STRING NOT NULL, extraction_id STRING NOT NULL, decision_id STRING,
  extractor_name STRING NOT NULL, extractor_version STRING NOT NULL, model_name STRING, model_version STRING
) USING DELTA;

CREATE TABLE IF NOT EXISTS {catalog}.gold.review_summary (
  run_id STRING NOT NULL, status STRING NOT NULL, document_count BIGINT NOT NULL
) USING DELTA;

CREATE TABLE IF NOT EXISTS {catalog}.gold.confidence_summary (
  run_id STRING NOT NULL, form_type STRING NOT NULL, confidence_bucket STRING NOT NULL,
  count BIGINT NOT NULL
) USING DELTA;

CREATE TABLE IF NOT EXISTS {catalog}.gold.extraction_accuracy (
  run_id STRING NOT NULL, form_type STRING NOT NULL, variant STRING NOT NULL,
  expected_count BIGINT NOT NULL, exact_match_count BIGINT NOT NULL
) USING DELTA;

CREATE TABLE IF NOT EXISTS {catalog}.eval.answer_key (
  fixture_id STRING NOT NULL, doc_id STRING NOT NULL, field_id STRING NOT NULL,
  field_name STRING NOT NULL, expected_value STRING NOT NULL, variant STRING NOT NULL,
  form_type STRING NOT NULL
) USING DELTA;

CREATE TABLE IF NOT EXISTS {catalog}.ops.pipeline_runs (
  run_id STRING NOT NULL, event_seq BIGINT NOT NULL, event_at TIMESTAMP NOT NULL,
  status STRING NOT NULL, detail_code STRING, job_run_id STRING NOT NULL, trigger_type STRING NOT NULL
) USING DELTA;

CREATE TABLE IF NOT EXISTS {catalog}.ops.publications (
  publication_seq BIGINT NOT NULL, run_id STRING NOT NULL, published_at TIMESTAMP NOT NULL,
  input_digest STRING NOT NULL
) USING DELTA;

CREATE TABLE IF NOT EXISTS {catalog}.ops.document_releases (
  run_id STRING NOT NULL, doc_id STRING NOT NULL, extraction_id STRING NOT NULL, status STRING NOT NULL
) USING DELTA;

CREATE OR REPLACE FUNCTION {catalog}.silver.mask_identifier(value STRING, field_name STRING)
RETURNS STRING
RETURN CASE
  WHEN lower(field_name) RLIKE '(^|[^a-z])(ssn|tin|itin|ein|atin|social[^a-z]+security[^a-z]+number|taxpayer[^a-z]+identification[^a-z]+number)([^a-z]|$)'
    THEN CAST(NULL AS STRING)
  ELSE value
END;

ALTER TABLE {catalog}.silver.fields
  ALTER COLUMN extracted_value SET MASK {catalog}.silver.mask_identifier USING COLUMNS (field_name);

ALTER TABLE {catalog}.silver.fields
  ALTER COLUMN final_value SET MASK {catalog}.silver.mask_identifier USING COLUMNS (field_name);
