"""Generate build metrics from merged PRs, PR CI history and the review log."""

import argparse
from collections import Counter, defaultdict
from datetime import datetime, timezone
import re
import subprocess

from github_reports import ROOT, gh_rows

LANES = ("first", "extraction", "processing", "app", "governance", "mcp")
METRICS_SECTION = re.compile(
    r"^#{1,6} (?:PR )?Metrics\b[^\n]*\n(.*?)(?=^#{1,6} |\Z)", re.I | re.M | re.S,
)
START = "<!-- build-metrics:start -->"
END = "<!-- build-metrics:end -->"


def timestamp(value):
    return datetime.fromisoformat(value.replace("Z", "+00:00"))


def minute_utc(time):
    return time.astimezone(timezone.utc).strftime("%Y-%m-%d %H:%M UTC")


def associate_runs(prs, workflow_runs):
    """Associate PR CI using the head branch and the PR's open-to-merge window."""
    windows = defaultdict(list)
    for pr in prs:
        windows[pr["headRefName"]].append((timestamp(pr["createdAt"]), timestamp(pr["mergedAt"]), pr["number"]))
    by_pr = defaultdict(list)
    ambiguous = 0
    for run in workflow_runs:
        created = timestamp(run["createdAt"])
        matches = [number for start, end, number in windows[run["headBranch"]] if start <= created <= end]
        if len(matches) == 1:
            by_pr[matches[0]].append(run)
        elif len(matches) > 1:
            ambiguous += 1
    return by_pr, ambiguous


def report(prs, workflow_runs, review_log):
    if not prs:
        raise ValueError("No merged pull requests found")
    by_pr, ambiguous = associate_runs(prs, workflow_runs)
    totals = Counter()
    for pr in prs:
        labels = {label["name"] for label in pr["labels"]}
        lanes = [lane for lane in LANES if f"lane:{lane}" in labels]
        totals[lanes[0] if len(lanes) == 1 else "unclassified"] += 1

    merge_times = sorted(timestamp(pr["mergedAt"]) for pr in prs)
    elapsed = merge_times[-1] - merge_times[0]
    hours, seconds = divmod(int(elapsed.total_seconds()), 3600)
    minutes = seconds // 60
    metrics_count = sum(
        bool(section and section.group(1).strip())
        for pr in prs if (section := METRICS_SECTION.search(pr["body"]))
    )
    attempted = [run for runs in by_pr.values() for run in runs]
    retries = sum(max(0, run["attempt"] - 1) for run in attempted)
    additional = sum(len(runs) - 1 for runs in by_pr.values())
    outcomes = Counter(run["conclusion"] or "pending" for run in attempted)
    findings = set(re.findall(r"^\| (R-\d{3}) \|", review_log, re.M))

    lines = [
        f"Snapshot through **{minute_utc(merge_times[-1])}**. "
        f"**{len(prs)} merged PRs** over **{hours}h {minutes}m** from the first merge "
        f"({minute_utc(merge_times[0])}) to the latest.",
        "",
        "| Lane | Merged PRs |",
        "|---|---:|",
        *(f"| {lane} | {totals[lane]} |" for lane in LANES),
    ]
    if totals["unclassified"]:
        lines.append(f"| Unclassified | {totals['unclassified']} |")
    lines.extend([
        "",
        f"- **PR Metrics coverage.** {metrics_count}/{len(prs)} merged PRs have a Metrics section in their body.",
        f"- **PR CI.** {len(attempted)} distinct workflow runs linked to {len(by_pr)} merged PRs. "
        f"{additional} were additional event runs after a PR's first run and {retries} were retries "
        "of the same run ID. "
        + ", ".join(f"{count} {result}" for result, count in sorted(outcomes.items())) + ".",
        f"- **Review.** {len(findings)} findings in [REVIEW-LOG.md](docs/REVIEW-LOG.md).",
        "",
        "CI totals use GitHub Actions rather than free-text counts in PR bodies. Runs are matched by "
        "head branch and time from PR creation through merge. They cover merged PRs only. Canceled "
        f"workflow runs are reported separately from failures. {ambiguous} runs with ambiguous "
        f"matches were excluded. {len(prs) - len(by_pr)} merged PRs have no matching run in the returned history.",
    ])
    return "\n".join(lines)


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--update-readme", action="store_true", help="Replace the marked README metrics block")
    args = parser.parse_args()
    prs = gh_rows(
        "pr", "list", "--state", "merged",
        "--json", "number,body,labels,createdAt,mergedAt,headRefName",
    )
    runs = gh_rows(
        "run", "list", "--workflow", "ci.yml", "--event", "pull_request",
        "--json", "databaseId,attempt,conclusion,createdAt,headBranch",
    )
    markdown = report(prs, runs, (ROOT / "docs/REVIEW-LOG.md").read_text())
    if args.update_readme:
        readme = ROOT / "README.md"
        text = readme.read_text()
        if text.count(START) != 1 or text.count(END) != 1 or text.index(START) >= text.index(END):
            raise ValueError("Expected a single ordered build-metrics block in README.md")
        prefix, after_start = text.split(START)
        _, suffix = after_start.split(END)
        readme.write_text(f"{prefix}{START}\n{markdown}\n{END}{suffix}")
    print(markdown)


if __name__ == "__main__":
    try:
        main()
    except (OSError, subprocess.SubprocessError, ValueError) as exc:
        raise SystemExit(f"Build metrics failed: {exc}") from exc
