"""Exercise the bundled entry point under the serverless script-loading model."""

from pathlib import Path
import sys
from types import ModuleType
import unittest
from unittest.mock import Mock, patch


class BootstrapJobTests(unittest.TestCase):
    def test_dispatches_both_tasks_without_a_file_global(self):
        script = Path(__file__).resolve().parents[1] / "src/ledgerlens/bootstrap_job.py"
        bootstrap_job = ModuleType("bootstrap_job")
        exec(compile(script.read_bytes(), str(script), "exec"), vars(bootstrap_job))
        src = "/Workspace/Users/test/.bundle/ledgerlens/dev/files/src"
        pyspark = ModuleType("pyspark")
        pyspark.__path__ = []
        sql = ModuleType("pyspark.sql")
        sql.SparkSession = Mock()
        spark = sql.SparkSession.builder.getOrCreate.return_value

        cases = (
            ("process", "ledgerlens.processing.job", ["--job-run-id", "42"],
             (spark, "ledgerlens_dev", "42", "one_time")),
            ("evaluate", "ledgerlens.evaluation", [], (spark, "ledgerlens_dev")),
        )
        for role, module_name, extra_args, expected in cases:
            with self.subTest(role=role):
                task = ModuleType(module_name)
                task.run = Mock()
                argv = ["bootstrap_job.py", role, "ledgerlens_dev", "one_time", "--src", src, *extra_args]
                modules = {"pyspark": pyspark, "pyspark.sql": sql, module_name: task}
                with (
                    patch.dict(sys.modules, modules),
                    patch.object(sys, "argv", argv),
                    patch.object(sys, "path", sys.path.copy()),
                ):
                    bootstrap_job.main()
                    self.assertEqual(sys.path[0], src)
                task.run.assert_called_once_with(*expected)
