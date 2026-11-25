from unittest import TestCase
from unittest.mock import Mock
from urllib.error import URLError

from scripts.databricks_smoke import task_error


class TaskErrorTests(TestCase):
    def test_reads_the_failed_task_and_omits_connector_details(self):
        run = {"tasks": [
            {"run_id": 123, "state": {"result_state": "SUCCESS"}},
            {"run_id": 456, "state": {"result_state": "FAILED"}},
        ]}
        api = Mock(return_value={
            "error": "RuntimeError: Processing job failed (PERMISSION_DENIED)\nsynthetic private fixture"
        })

        self.assertEqual(task_error(api, run), "Processing job failed (PERMISSION_DENIED)")
        api.assert_called_once_with("/api/2.1/jobs/runs/get-output?run_id=456")

    def test_falls_back_to_a_bounded_task_status_when_output_is_unavailable(self):
        run = {"tasks": [{"run_id": 456, "state": {
            "result_state": "FAILED",
            "state_message": "AnalysisException: [TABLE_OR_VIEW_NOT_FOUND] synthetic table name",
        }}]}

        self.assertEqual(
            task_error(Mock(side_effect=URLError("unavailable")), run),
            "AnalysisException: [TABLE_OR_VIEW_NOT_FOUND]",
        )

    def test_keeps_unrecognized_task_output_out_of_logs(self):
        run = {"tasks": [{"run_id": 456, "state": {"result_state": "FAILED"}}]}
        api = Mock(return_value={"error": "synthetic raw output [SYNTHETIC_TOKEN]"})

        self.assertEqual(task_error(api, run), "")
