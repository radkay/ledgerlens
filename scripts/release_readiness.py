"""Report the LedgerLens release queue, integration status and README placeholders."""

from concurrent.futures import ThreadPoolExecutor
import re
import subprocess
import sys

from github_reports import ROOT, gh_rows


def latest_run(branch):
    return gh_rows(
        "run", "list", "--workflow", "integration.yml", "--branch", branch,
        "--json", "headSha,status,conclusion,url", limit=1,
    )


def print_prs(title, prs):
    print(f"\n{title}")
    for pr in sorted(prs, key=lambda pr: pr["number"]):
        print(f"- #{pr['number']} {pr['title']} ({pr['baseRefName']}) {pr['url']}")
    if not prs:
        print("- None")


def main():
    with ThreadPoolExecutor(max_workers=3) as executor:
        pull_requests = executor.submit(
            gh_rows, "pr", "list", "--state", "open",
            "--json", "number,title,url,baseRefName,isDraft,labels",
        )
        integrations = {name: executor.submit(latest_run, branch)
                        for name, branch in (("Dev", "dev"), ("Demo", "main"))}
        prs = pull_requests.result()
        print_prs("Open drafts", [pr for pr in prs if pr["isDraft"]])
        print_prs("Open needs-ron PRs", [
            pr for pr in prs if any(label["name"] == "needs-ron" for label in pr["labels"])
        ])
        print("\nLatest integration")
        for name, pending in integrations.items():
            runs = pending.result()
            if runs:
                run = runs[0]
                result = run["conclusion"] or run["status"]
                print(f"- {name} {result} at {run['headSha'][:7]} {run['url']}")
            else:
                print(f"- {name}: no workflow run found")

    print("\nUnfilled README placeholders")
    pattern = re.compile(r"\{\{RELEASE:([A-Z][A-Z0-9_]*)\}\}")
    readme = (ROOT / "README.md").read_text()
    placeholders = [(line_no, match.group(1)) for line_no, line in enumerate(readme.splitlines(), 1)
                    for match in pattern.finditer(line)]
    for line_no, name in placeholders:
        print(f"- {name} (README.md:{line_no})")
    if not placeholders:
        print("- None")


if __name__ == "__main__":
    try:
        main()
    except (OSError, subprocess.SubprocessError, ValueError) as exc:
        sys.exit(f"Release readiness failed: {exc}")
