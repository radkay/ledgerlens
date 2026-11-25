import re
import sqlite3
import unittest

from provisioning.provision import ddl


SNAPSHOT_NAMES = (
    "person_year", "missing_documents", "prefill", "lineage", "review_summary",
    "confidence_summary", "extraction_accuracy",
)


class PublicationViewTests(unittest.TestCase):
    def test_current_history_and_request_pin_with_overlapping_runs(self):
        for catalog in ("ledgerlens_dev", "ledgerlens_demo"):
            with self.subTest(catalog=catalog):
                self.assert_publication_views(catalog)

    def assert_publication_views(self, catalog):
        database = sqlite3.connect(":memory:")
        self.addCleanup(database.close)
        database.execute("""
            CREATE TABLE ops_publications (
                run_id TEXT, publication_seq INTEGER, published_at TEXT, input_digest TEXT
            )
        """)
        for table in SNAPSHOT_NAMES:
            database.execute(f"CREATE TABLE gold_{table} (run_id TEXT, grain TEXT)")
            database.executemany(f"INSERT INTO gold_{table} VALUES (?, ?)", [
                ("first", "same-grain"), ("latest", "same-grain"), ("incomplete", "same-grain"),
            ])

        names = {"gold.latest_publication"} | {
            f"gold.{prefix}_{table}" for prefix in ("published", "current") for table in SNAPSHOT_NAMES
        }
        applied = set()
        for statement in ddl(catalog):
            match = re.match(rf"CREATE VIEW IF NOT EXISTS {catalog}\.(\S+) AS\n", statement)
            if match and match[1] in names:
                local_sql = re.sub(rf"{catalog}\.(\w+)\.(\w+)", r"\1_\2", statement)
                database.execute(local_sql)
                applied.add(match[1])
        self.assertEqual(applied, names)
        self.assertEqual(database.execute("SELECT * FROM gold_latest_publication").fetchall(), [])
        for table in SNAPSHOT_NAMES:
            self.assertEqual(database.execute(f"SELECT * FROM gold_current_{table}").fetchall(), [])
            self.assertEqual(database.execute(f"SELECT * FROM gold_published_{table}").fetchall(), [])

        database.execute("INSERT INTO ops_publications VALUES ('first', 1, '2026-11-24 09:00', 'A')")
        pinned = database.execute("SELECT run_id FROM gold_latest_publication").fetchone()[0]
        database.execute("INSERT INTO ops_publications VALUES ('latest', 2, '2026-11-24 08:00', 'B')")
        self.assertEqual(database.execute("SELECT run_id FROM gold_latest_publication").fetchall(), [("latest",)])

        for table in SNAPSHOT_NAMES:
            with self.subTest(snapshot=table):
                self.assertEqual(database.execute(f"SELECT * FROM gold_current_{table}").fetchall(),
                                 [("latest", "same-grain")])
                self.assertEqual(
                    database.execute(f"SELECT * FROM gold_published_{table} ORDER BY run_id").fetchall(),
                    [("first", "same-grain"), ("latest", "same-grain")],
                )
                self.assertEqual(
                    database.execute(f"SELECT * FROM gold_published_{table} WHERE run_id = ?", (pinned,)).fetchall(),
                    [("first", "same-grain")],
                )

        database.execute("DELETE FROM gold_extraction_accuracy WHERE run_id = 'latest'")
        self.assertEqual(database.execute("SELECT * FROM gold_current_extraction_accuracy").fetchall(), [])
        self.assertEqual(database.execute("SELECT * FROM gold_published_extraction_accuracy").fetchall(),
                         [("first", "same-grain")])
