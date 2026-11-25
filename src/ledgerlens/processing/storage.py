"""Bounded, parameterized access to the provisioned Delta tables."""

from datetime import UTC, datetime

CATALOGS = ("ledgerlens_dev", "ledgerlens_demo")
MAX_ROWS = 1000
INSERT_BATCH = 50


def utc(value):
    return value.replace(tzinfo=UTC) if value.tzinfo is None else value.astimezone(UTC)


class DeltaStore:
    def __init__(self, spark, catalog, *, readable, writable):
        if catalog not in CATALOGS:
            raise ValueError("Unexpected catalog")
        self.spark = spark
        self.catalog = catalog
        self.readable = frozenset(readable) | frozenset(writable)
        self.writable = frozenset(writable)
        self.spark.sql("SET TIME ZONE 'UTC'").collect()

    def name(self, table):
        if table not in self.readable:
            raise ValueError("Unexpected table")
        return f"{self.catalog}.{table}"

    def version(self, table):
        return self.spark.sql(f"DESCRIBE HISTORY {self.name(table)} LIMIT 1").first()["version"]

    def read(self, table, *, version=None, run_id=None, job_run_id=None, event_status=None,
             latest_event=False):
        if (job_run_id is not None or event_status is not None or latest_event) and table != "ops.pipeline_runs":
            raise ValueError("Run event filters require the pipeline ledger")
        source = self.name(table)
        if version is not None:
            source += f" VERSION AS OF {int(version)}"
        args = {column: value for column, value in (
            ("run_id", run_id), ("job_run_id", job_run_id), ("status", event_status),
        ) if value is not None}
        where = " WHERE " + " AND ".join(f"{column} = :{column}" for column in args) if args else ""
        order = " ORDER BY event_seq DESC" if latest_event else ""
        limit = 1 if latest_event else MAX_ROWS + 1
        rows = self.spark.sql(
            f"SELECT * FROM {source}{where}{order} LIMIT {limit}", args=args,
        ).collect()
        if len(rows) > MAX_ROWS:
            raise ValueError("Table exceeds the synthetic job row limit")
        return [
            {key: utc(value) if isinstance(value, datetime) else value
             for key, value in row.asDict().items()}
            for row in rows
        ]

    def append(self, table, rows):
        if table not in self.writable:
            raise ValueError("Unexpected write target")
        if not rows:
            return
        columns = tuple(rows[0])
        for start in range(0, len(rows), INSERT_BATCH):
            args = {}
            values = []
            for index, row in enumerate(rows[start:start + INSERT_BATCH]):
                markers = []
                for column in columns:
                    name = f"p{index}_{column}"
                    value = row[column]
                    if isinstance(value, datetime):
                        args[name] = utc(value).isoformat()
                        markers.append(f"CAST(:{name} AS TIMESTAMP)")
                    else:
                        args[name] = value
                        markers.append(f":{name}")
                values.append(f"({', '.join(markers)})")
            self.spark.sql(
                f"INSERT INTO {self.name(table)} ({', '.join(columns)}) VALUES {', '.join(values)}",
                args=args,
            ).collect()


def unique(rows, keys):
    index = {}
    for row in rows:
        key = tuple(row[column] for column in keys)
        if key in index:
            raise ValueError("Duplicate table grain")
        index[key] = row
    return index


def ensure_rows(store, table, rows, keys, *, run_id=None, projection=lambda row: row):
    """Resume a partial append and confirm the complete persisted payload."""
    expected = unique(rows, keys)

    def check():
        actual = unique(store.read(table, run_id=run_id), keys)
        for key, row in actual.items():
            if key in expected and projection(expected[key]) != row:
                raise ValueError("Persisted row differs from the frozen payload")
            if run_id is not None and key not in expected:
                raise ValueError("Unexpected row in run payload")
        return actual

    existing = check()
    store.append(table, [row for key, row in expected.items() if key not in existing])
    if expected.keys() - check().keys():
        raise ValueError("Incomplete persisted payload")
