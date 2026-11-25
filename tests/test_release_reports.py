import contextlib
import io
from pathlib import Path
from tempfile import TemporaryDirectory
import unittest
from unittest.mock import patch

from scripts import github_reports

with patch.dict("sys.modules", {"github_reports": github_reports}):
    from scripts import build_metrics, release_readiness


class BuildMetricsTests(unittest.TestCase):
    def test_counts_run_outcomes_without_mixing_reused_branches(self):
        prs = [
            {"number": 1, "labels": [{"name": "lane:first"}], "headRefName": "branch",
             "createdAt": "2026-11-24T01:00:00Z", "mergedAt": "2026-11-24T02:00:00Z",
             "body": "## Metrics\n\nRecorded.\n\n## Outcome\nPublished.\n"},
            {"number": 2, "labels": [{"name": "lane:mcp"}], "headRefName": "branch",
             "createdAt": "2026-11-24T03:00:00Z", "mergedAt": "2026-11-24T04:05:00Z",
             "body": "## Outcome\nPublished.\n"},
        ]
        runs = [
            {"headBranch": "branch", "createdAt": f"2026-11-24T{clock}Z",
             "attempt": attempt, "conclusion": outcome}
            for clock, attempt, outcome in [
                ("01:01:00", 1, "cancelled"),
                ("01:02:00", 2, "success"),
                ("02:05:00", 1, "failure"),
                ("03:10:00", 1, "success"),
            ]
        ]
        markdown = build_metrics.report(prs, runs, "| R-001 | one |\n| R-002 | two |\n")
        self.assertIn("2 merged PRs** over **2h 5m", markdown)
        self.assertIn("| mcp | 1 |", markdown)
        self.assertIn("1/2 merged PRs have a Metrics section", markdown)
        self.assertIn("3 distinct workflow runs linked to 2 merged PRs", markdown)
        self.assertIn("1 were additional event runs", markdown)
        self.assertIn("1 were retries", markdown)
        self.assertIn("1 cancelled, 2 success", markdown)
        self.assertIn("2 findings", markdown)


class ReadinessTests(unittest.TestCase):
    def test_reports_drafts_holds_latest_runs_and_unfilled_tokens(self):
        def rows(*args, **kwargs):
            if args[0] == "pr":
                return [
                    {"number": 1, "title": "Draft", "url": "pr1", "baseRefName": "dev",
                     "isDraft": True, "labels": []},
                    {"number": 2, "title": "Question", "url": "pr2", "baseRefName": "dev",
                     "isDraft": True, "labels": [{"name": "needs-ron"}]},
                ]
            if "dev" in args:
                return [{"headSha": "12345678", "conclusion": "success", "status": "completed", "url": "run1"}]
            return [{"headSha": "23456789", "conclusion": "", "status": "queued", "url": "run2"}]

        stdout = io.StringIO()
        with TemporaryDirectory() as directory:
            root = Path(directory)
            (root / "README.md").write_text("A real URL\n{{RELEASE:DEMO_APP_URL}}\n")
            with patch.object(release_readiness, "ROOT", root), patch.object(
                release_readiness, "gh_rows", side_effect=rows,
            ), contextlib.redirect_stdout(stdout):
                release_readiness.main()
        output = stdout.getvalue()
        self.assertIn("Open drafts\n- #1 Draft", output)
        self.assertIn("Open needs-ron PRs\n- #2 Question", output)
        self.assertIn("Dev success at 1234567", output)
        self.assertIn("Demo queued at 2345678", output)
        self.assertIn("DEMO_APP_URL (README.md:2)", output)
