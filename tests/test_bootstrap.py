from pathlib import Path
import re
import subprocess
import sys
import tomllib
import unittest


ROOT = Path(__file__).resolve().parents[1]


class BootstrapTests(unittest.TestCase):
    def test_runtime_and_lock_match(self):
        with (ROOT / "pyproject.toml").open("rb") as project_file:
            project = tomllib.load(project_file)["project"]
        with (ROOT / "uv.lock").open("rb") as lock_file:
            lock = tomllib.load(lock_file)
        matches = [package for package in lock["package"] if package["name"] == project["name"]]
        self.assertEqual(len(matches), 1)
        self.assertEqual(matches[0]["version"], project["version"])
        self.assertEqual(lock["requires-python"], project["requires-python"])
        self.assertEqual((ROOT / ".python-version").read_text().strip(), "3.12.3")

    def test_catalogs_are_explicit(self):
        contract = (ROOT / "CONTRACT.md").read_text(encoding="utf-8")
        workflow = (ROOT / ".github/workflows/integration.yml").read_text(encoding="utf-8")
        for catalog in ("ledgerlens_dev", "ledgerlens_demo"):
            self.assertIn(catalog, contract)
            self.assertIn(catalog, workflow)

    def test_provisioning_sql_stays_in_target_catalog(self):
        for environment in ("dev", "demo"):
            with self.subTest(environment=environment):
                catalog = f"ledgerlens_{environment}"
                plan = subprocess.check_output(
                    [sys.executable, "provisioning/provision.py", environment, "--plan"],
                    cwd=ROOT, text=True,
                )
                statements = [statement.strip() for statement in plan.split(";") if statement.strip()]
                self.assertIn(f"CREATE CATALOG IF NOT EXISTS {catalog}", statements)
                self.assertIn(f"CREATE SCHEMA IF NOT EXISTS {catalog}.bronze", statements)
                for statement in statements:
                    self.assertEqual(
                        set(re.findall(r"\bledgerlens_[a-z0-9_]+\b", statement, re.IGNORECASE)),
                        {catalog},
                        statement,
                    )
