from unittest import TestCase
from unittest.mock import Mock, patch
from urllib.error import URLError

from scripts.databricks_smoke import task_error, verify_permission_denial


class TaskErrorTests(TestCase):
    def test_reads_the_failed_task_and_omits_connector_details(self):
        run = {"tasks": [
            {"run_id": 123, "state": {"result_state": "SUCCESS"}},
            {"run_id": 456, "state": {"result_state": "FAILED"}},
        ]}
        api = Mock(return_value={
            "error": "RuntimeError: Processing job failed (PERMISSION_DENIED)\nsynthetic private fixture"
        })

        self.assertEqual(task_error(api, run), "task run 456, Processing job failed (PERMISSION_DENIED)")
        api.assert_called_once_with("/api/2.2/jobs/runs/get-output?run_id=456")

    def test_falls_back_to_a_bounded_task_status_when_output_is_unavailable(self):
        run = {"tasks": [{"run_id": 456, "state": {
            "result_state": "FAILED",
            "state_message": "AnalysisException: [TABLE_OR_VIEW_NOT_FOUND] synthetic table name",
        }}]}

        self.assertEqual(
            task_error(Mock(side_effect=URLError("unavailable")), run),
            "task run 456, task output unavailable, AnalysisException: [TABLE_OR_VIEW_NOT_FOUND]",
        )

    def test_keeps_unrecognized_task_output_out_of_logs(self):
        run = {"tasks": [{"run_id": 456, "state": {"result_state": "FAILED"}}]}
        api = Mock(return_value={"error": "synthetic raw output [SYNTHETIC_TOKEN]"})

        self.assertEqual(task_error(api, run), "task run 456")

    def test_reads_python_task_logs_and_keeps_the_termination_code(self):
        run = {"tasks": [{"run_id": 456, "status": {"termination_details": {"code": "DRIVER_ERROR"}}}]}
        api = Mock(return_value={"logs": "ModuleNotFoundError: No module named 'ledgerlens'\nsynthetic trace"})

        self.assertEqual(
            task_error(api, run),
            "task run 456, DRIVER_ERROR, ModuleNotFoundError: No module named 'ledgerlens'",
        )


class PermissionDenialTests(TestCase):
    def test_accepts_insufficient_privilege_from_the_sql_statement_api(self):
        error = {"error_code": "BAD_REQUEST", "sql_state": "42501"}
        with patch("builtins.print") as log:
            verify_permission_denial("silver.fields", {"state": "FAILED", "error": error})
        log.assert_called_once_with(
            "Permission denial confirmed for silver.fields (state=FAILED, error_code=BAD_REQUEST, sql_state=42501)",
            flush=True,
        )

    def test_unexpected_outcomes_fail_without_logging_sql_error_text(self):
        for status, detail in (
            ({"state": "SUCCEEDED"}, "state=SUCCEEDED"),
            ({"state": "FAILED", "error": {
                "error_code": "BAD_REQUEST", "sql_state": "42P01", "message": "synthetic private text",
            }}, "state=FAILED, error_code=BAD_REQUEST, sql_state=42P01"),
        ):
            with self.subTest(status=status), self.assertRaises(RuntimeError) as raised:
                verify_permission_denial("eval.answer_key", status)
            self.assertEqual(
                str(raised.exception), f"Expected an explicit permission denial for eval.answer_key ({detail})",
            )
