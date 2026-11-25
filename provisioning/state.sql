CREATE OR REPLACE VIEW {catalog}.review.document_state AS
WITH events AS (
  SELECT doc_id, received_at AS event_at, 0 AS event_kind, doc_id AS event_id,
         CAST(NULL AS STRING) AS extraction_id, 'received' AS action
  FROM {catalog}.bronze.documents
  UNION ALL
  SELECT doc_id, extracted_at, 1, extraction_id, extraction_id, status
  FROM {catalog}.bronze.extractions
  UNION ALL
  SELECT doc_id, decided_at, 2, decision_id, extraction_id, action
  FROM {catalog}.review.decisions
  UNION ALL
  SELECT r.doc_id, p.published_at, 3, lpad(CAST(p.publication_seq AS STRING), 20, '0'),
         r.extraction_id, r.status
  FROM {catalog}.ops.document_releases AS r
  JOIN {catalog}.ops.publications AS p ON r.run_id = p.run_id
), ordered AS (
  SELECT doc_id, sort_array(collect_list(named_struct(
    'event_at', event_at, 'event_kind', event_kind, 'event_id', event_id,
    'extraction_id', extraction_id, 'action', action
  ))) AS history
  FROM events
  GROUP BY doc_id
), folded AS (
  SELECT doc_id, aggregate(
    history,
    named_struct('status', 'received', 'extraction_id', CAST(NULL AS STRING),
                 'state_at', CAST(NULL AS TIMESTAMP)),
    (state, event) -> CASE
      WHEN event.event_kind = 0 AND state.state_at IS NULL
        THEN named_struct('status', 'received', 'extraction_id', event.extraction_id, 'state_at', event.event_at)
      WHEN event.event_kind = 1
        THEN named_struct('status', IF(event.action = 'failed', 'needs_review', event.action),
                          'extraction_id', event.extraction_id, 'state_at', event.event_at)
      WHEN (event.extraction_id IS NULL OR event.extraction_id = state.extraction_id)
        THEN CASE
          WHEN event.event_kind = 2 AND event.action IN ('approve', 'correct_field')
            AND state.status IN ('needs_review', 'approved')
            THEN named_struct('status', 'approved', 'extraction_id', state.extraction_id, 'state_at', event.event_at)
          WHEN event.event_kind = 2 AND event.action = 'reject'
            AND state.status IN ('needs_review', 'approved')
            THEN named_struct('status', 'rejected', 'extraction_id', state.extraction_id, 'state_at', event.event_at)
          WHEN event.event_kind = 2 AND event.action = 'reprocess'
            AND state.status IN ('needs_review', 'rejected', 'recalled')
            THEN named_struct('status', 'received', 'extraction_id', CAST(NULL AS STRING), 'state_at', event.event_at)
          WHEN event.event_kind = 3 AND event.action = 'released'
            THEN named_struct('status', 'released', 'extraction_id', state.extraction_id, 'state_at', event.event_at)
          WHEN event.event_kind = 3 AND event.action = 'recalled' AND state.status = 'released'
            THEN named_struct('status', 'recalled', 'extraction_id', state.extraction_id, 'state_at', event.event_at)
          ELSE state
        END
      ELSE state
    END
  ) AS current_state
  FROM ordered
)
SELECT doc_id, current_state.status, current_state.extraction_id, current_state.state_at
FROM folded;

CREATE OR REPLACE VIEW {catalog}.gold.current_document_state AS
SELECT doc_id, status, state_at FROM {catalog}.review.document_state;

CREATE OR REPLACE VIEW {catalog}.review.current_workbench AS
WITH recent_fields AS (
  SELECT f.* FROM {catalog}.silver.fields AS f
  JOIN (
    SELECT run_id, max(event_at) AS latest_event_at FROM {catalog}.ops.pipeline_runs
    WHERE status IN ('validated', 'published') GROUP BY run_id
  ) AS runs ON f.run_id = runs.run_id
  QUALIFY row_number() OVER (
    PARTITION BY f.doc_id, f.extraction_id, f.field_id ORDER BY runs.latest_event_at DESC, f.run_id DESC
  ) = 1
)
SELECT d.doc_id, d.status, d.state_at, d.extraction_id, f.field_id, f.field_name,
       {catalog}.silver.mask_workbench_identifier(f.extracted_value, f.field_name) AS extracted_value,
       {catalog}.silver.mask_workbench_identifier(f.final_value, f.field_name) AS final_value,
       f.confidence, f.bbox_json, f.decision_id
FROM {catalog}.review.document_state AS d
LEFT JOIN recent_fields AS f ON d.doc_id = f.doc_id AND d.extraction_id = f.extraction_id;
