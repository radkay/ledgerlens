"""Launch the bundled Databricks processing and evaluation jobs."""

import argparse
from pathlib import Path
import sys


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("role", choices=("process", "evaluate"))
    parser.add_argument("catalog", choices=("ledgerlens_dev", "ledgerlens_demo"))
    parser.add_argument("trigger", choices=("one_time", "periodic"))
    parser.add_argument("--job-run-id", type=int)
    args = parser.parse_args()
    if args.role == "process" and (args.job_run_id is None or args.job_run_id <= 0):
        parser.error("processing requires a positive --job-run-id")
    if sys.version_info[:3] != (3, 12, 3):
        raise RuntimeError(f"Expected Python 3.12.3, found {sys.version.split()[0]}")

    from pyspark.sql import SparkSession

    spark = SparkSession.builder.getOrCreate()
    # Databricks invokes this file as a script. Import the synced package from src.
    sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
    if args.role == "process":
        from ledgerlens.processing.job import run

        run(spark, args.catalog, str(args.job_run_id), args.trigger)
        return

    from ledgerlens.evaluation import run

    run(spark, args.catalog)


if __name__ == "__main__":
    main()
