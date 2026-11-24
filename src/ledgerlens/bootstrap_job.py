"""Launch processing and verify the evaluator's bootstrap source access."""

import argparse
from pathlib import Path
import sys


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("role", choices=("process", "evaluate"))
    parser.add_argument("catalog", choices=("ledgerlens_dev", "ledgerlens_demo"))
    trigger_types = {"one_time": "manual", "periodic": "scheduled"}
    parser.add_argument("trigger", choices=trigger_types)
    parser.add_argument("--job-run-id", type=int)
    args = parser.parse_args()
    if args.role == "process" and (args.job_run_id is None or args.job_run_id <= 0):
        parser.error("processing requires a positive --job-run-id")
    if sys.version_info[:3] != (3, 12, 3):
        raise RuntimeError(f"Expected Python 3.12.3, found {sys.version.split()[0]}")

    from pyspark.sql import SparkSession

    spark = SparkSession.builder.getOrCreate()
    if args.role == "process":
        # Databricks invokes this file as a script. Import the synced package from src.
        sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
        from ledgerlens.processing.job import run

        run(spark, args.catalog, str(args.job_run_id), args.trigger)
        return

    tables = ("eval.answer_key", "bronze.extractions", "ops.publications")
    for table in tables:
        spark.table(f"{args.catalog}.{table}").limit(1).count()
    spark.read.format("binaryFile").load(f"/Volumes/{args.catalog}/eval/fixtures").limit(1).count()
    print(f"Python {sys.version.split()[0]} and {args.role} source access verified ({trigger_types[args.trigger]} run)")


if __name__ == "__main__":
    main()
