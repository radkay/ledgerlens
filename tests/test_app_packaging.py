import tomllib
import unittest
from pathlib import Path


APP = Path(__file__).resolve().parents[1] / "src" / "ledgerlens" / "app"


class CommunityCloudPackageTests(unittest.TestCase):
    def test_secret_template_has_only_empty_reader_settings(self):
        template = APP / ".streamlit" / "secrets.toml.example"
        settings = tomllib.loads(template.read_text())
        self.assertEqual(settings, dict.fromkeys((
            "LEDGERLENS_DATA_SOURCE", "LEDGERLENS_ENVIRONMENT", "DATABRICKS_HOST",
            "DATABRICKS_CLIENT_ID", "DATABRICKS_CLIENT_SECRET", "DATABRICKS_WAREHOUSE_ID",
        ), ""))
