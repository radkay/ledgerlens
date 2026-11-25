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


class HostedAppPackageTests(unittest.TestCase):
    def test_stages_standalone_mcp_without_public_entrypoint_or_secrets(self):
        from tempfile import TemporaryDirectory
        from scripts.stage_hosted_app import stage_hosted_app

        with TemporaryDirectory() as directory:
            destination = Path(directory) / "hosted"
            stage_hosted_app(destination)
            self.assertTrue((destination / "app.yaml").is_file())
            self.assertTrue((destination / "ledgerlens" / "mcp" / "panel.py").is_file())
            self.assertIn("mcp==1.30.0", (destination / "requirements.txt").read_text().splitlines())
            self.assertFalse((destination / ".streamlit").exists())
            self.assertFalse((destination / "community_cloud.py").exists())
            with self.assertRaises(FileExistsError):
                stage_hosted_app(destination)
