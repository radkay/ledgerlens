"""Read-only GitHub CLI access for release reports."""

import json
from pathlib import Path
import subprocess

REPO = "radkay/ledgerlens"
ROOT = Path(__file__).resolve().parents[1]
HISTORY_LIMIT = 1000


def gh_rows(*args, limit=HISTORY_LIMIT):
    result = subprocess.run(
        ["gh", *args, "--repo", REPO, "--limit", str(limit)],
        cwd=ROOT, check=True, capture_output=True, text=True, timeout=90,
    )
    rows = json.loads(result.stdout)
    if not isinstance(rows, list):
        raise ValueError("Expected a list from GitHub CLI")
    if limit == HISTORY_LIMIT and len(rows) >= limit:
        raise ValueError("GitHub history reached the report limit; narrow the history before reporting")
    return rows
