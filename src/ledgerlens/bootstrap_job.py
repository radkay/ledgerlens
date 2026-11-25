"""Launch the bundled Databricks processing and evaluation jobs."""

import argparse
import sys


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("role", choices=("process", "evaluate"))
    parser.add_argument("catalog", choices=("ledgerlens_dev", "ledgerlens_demo"))
    parser.add_argument("trigger", choices=("one_time", "periodic"))
    parser.add_argument("--job-run-id", type=int)
    parser.add_argument("--src", required=True, help="Deployed bundle source directory")
    args = parser.parse_args()
    if args.role == "process" and (args.job_run_id is None or args.job_run_id <= 0):
        parser.error("processing requires a positive --job-run-id")
    if sys.version_info[:3] != (3, 12, 3):
        raise RuntimeError(f"Expected Python 3.12.3, found {sys.version.split()[0]}")

    from pyspark.sql import SparkSession

    spark = SparkSession.builder.getOrCreate()
    sys.path.insert(0, args.src)
    if args.role == "process":
        from ledgerlens.processing.job import run

        run(spark, args.catalog, str(args.job_run_id), args.trigger)
        return

    from ledgerlens.evaluation import run

    run(spark, args.catalog)


if __name__ == "__main__":
    main()
