"""Stage a self-contained LedgerLens source directory for the hosted Databricks App."""

import argparse
from pathlib import Path
import shutil
import tomllib

ROOT = Path(__file__).resolve().parents[1]


def stage_hosted_app(destination):
    ledgerlens = ROOT / "src" / "ledgerlens"
    app = ledgerlens / "app"
    project = tomllib.loads((ROOT / "pyproject.toml").read_text(encoding="utf-8"))
    mcp_requirement, = (requirement for requirement in project["project"]["dependencies"]
                        if requirement.startswith("mcp=="))

    destination.mkdir()
    for name in ("app.yaml", "streamlit_app.py"):
        shutil.copyfile(app / name, destination / name)
    ignore = shutil.ignore_patterns("__pycache__", "*.pyc")
    shutil.copytree(app / "ledgerlens_ui", destination / "ledgerlens_ui", ignore=ignore)
    shutil.copytree(ledgerlens / "mcp", destination / "ledgerlens" / "mcp", ignore=ignore)
    requirements = (app / "requirements.txt").read_text(encoding="utf-8").rstrip()
    (destination / "requirements.txt").write_text(
        f"{requirements}\n{mcp_requirement}\n", encoding="utf-8",
    )


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("destination", type=Path, help="A new directory under an existing parent")
    args = parser.parse_args()
    stage_hosted_app(args.destination)


if __name__ == "__main__":
    main()
