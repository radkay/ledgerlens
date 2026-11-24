"""Verify the bundled serverless interpreter and the job principal's source access."""

import argparse
import sys


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("role", choices=("process", "evaluate"))
    parser.add_argument("catalog", choices=("ledgerlens_dev", "ledgerlens_demo"))
    trigger_types = {"one_time": "manual", "periodic": "scheduled"}
    parser.add_argument("trigger", choices=trigger_types)
    args = parser.parse_args()
    if sys.version_info[:3] != (3, 12, 3):
        raise RuntimeError(f"Expected Python 3.12.3, found {sys.version.split()[0]}")

    from pyspark.sql import SparkSession

    spark = SparkSession.builder.getOrCreate()
    if args.role == "process":
        tables = ("bronze.expected_forms", "bronze.documents", "review.reason_codes")
        volume = "bronze/inbox"
    else:
        tables = ("eval.answer_key", "bronze.extractions", "ops.publications")
        volume = "eval/fixtures"
    for table in tables:
        spark.table(f"{args.catalog}.{table}").limit(1).count()
    spark.read.format("binaryFile").load(f"/Volumes/{args.catalog}/{volume}").limit(1).count()
    print(f"Python {sys.version.split()[0]} and {args.role} source access verified ({trigger_types[args.trigger]} run)")


if __name__ == "__main__":
    main()
