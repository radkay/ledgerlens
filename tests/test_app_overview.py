import unittest
from datetime import UTC, datetime, timedelta

from src.ledgerlens.app.ledgerlens_ui.dashboard import latest_batch_event, read_overview
from src.ledgerlens.app.ledgerlens_ui.fixtures import PUBLISHED, FixtureBatch, FixtureReader


class OverviewTests(unittest.TestCase):
    def test_pins_published_metrics_but_reads_current_document_states(self):
        class Reader(FixtureReader):
            def latest_publication(self):
                calls.append("pin")
                return PUBLISHED

            def confidence_scores(self, run_id):
                calls.append(run_id)
                return super().confidence_scores(run_id)

            def review_summary(self, run_id):
                calls.append(run_id)
                return super().review_summary(run_id)

            def extraction_accuracy(self, run_id):
                calls.append(run_id)
                return []

        calls = []
        publication, live, review, confidence, accuracy = read_overview(Reader())
        self.assertEqual(calls, ["pin", *[PUBLISHED.run_id] * 3])
        self.assertEqual(publication, PUBLISHED)
        self.assertEqual(live["received"], 1)
        self.assertNotIn("received", review)
        self.assertEqual(confidence, {"[0, 0.5)": 1, "[0.5, 0.8)": 1, "[0.8, 1]": 2, "Unreported": 2})
        self.assertEqual(accuracy, [])

    def test_batch_uses_latest_event_and_deduplicates_submission(self):
        now = datetime(2025, 11, 23, tzinfo=UTC)
        operator = FixtureBatch(lambda: now)
        run = operator.run_batch("request-1")
        self.assertEqual(run, operator.run_batch("request-1"))
        self.assertEqual(latest_batch_event(operator, run).status, "queued")
        with self.assertRaises(ValueError):
            operator.run_batch("request-2")
        now += timedelta(seconds=3)
        self.assertEqual(latest_batch_event(operator, run).status, "running")
        now += timedelta(seconds=3)
        self.assertEqual(latest_batch_event(operator, run).status, "skipped_duplicate")
        self.assertNotEqual(run, operator.run_batch("request-2"))
