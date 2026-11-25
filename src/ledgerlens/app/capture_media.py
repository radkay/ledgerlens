"""Capture the LedgerLens fixture app without calling live services."""

import argparse
from contextlib import contextmanager
from io import BytesIO
import os
from pathlib import Path
import socket
import subprocess
import sys
from tempfile import TemporaryDirectory
from time import monotonic, sleep
from urllib.error import URLError
from urllib.request import urlopen

APP = Path(__file__).resolve().parent
REPO = APP.parents[2]


@contextmanager
def fixture_server():
    with TemporaryDirectory(prefix="ledgerlens-capture-") as folder:
        local = Path(folder)
        entry = local / "fixtures_app.py"
        entry.write_text(f"""
import os
import sys

sys.path.insert(0, {str(APP)!r})
import streamlit_app as app
from ledgerlens_ui.databricks import OperatorIdentity

os.environ["DATABRICKS_APP_NAME"] = "fixture-capture"
app.delegated_identity = lambda *_: (OperatorIdentity("fixture-capture-reviewer"), None)
app.main({{"LEDGERLENS_DATA_SOURCE": "fixture"}})
""".lstrip(), encoding="utf-8")
        with socket.socket() as listener:
            listener.bind(("127.0.0.1", 0))
            port = listener.getsockname()[1]
        address = f"http://127.0.0.1:{port}"
        env = {key: value for key, value in os.environ.items()
               if not key.startswith(("DATABRICKS_", "LEDGERLENS_", "STREAMLIT_"))}
        with (local / "streamlit.log").open("w+", encoding="utf-8") as log:
            process = subprocess.Popen(
                [sys.executable, "-m", "streamlit", "run", str(entry), "--server.address=127.0.0.1",
                 f"--server.port={port}", "--server.headless=true", "--browser.gatherUsageStats=false"],
                cwd=local, env=env, stdout=log, stderr=subprocess.STDOUT,
            )
            try:
                deadline = monotonic() + 30
                while monotonic() < deadline and process.poll() is None:
                    try:
                        with urlopen(f"{address}/_stcore/health", timeout=1) as response:
                            if response.status == 200:
                                break
                    except (URLError, TimeoutError):
                        sleep(0.2)
                else:
                    log.seek(0)
                    raise RuntimeError(f"Fixture app did not start.\n{log.read()[-2000:]}")
                yield address
            finally:
                if process.poll() is None:
                    process.terminate()
                    try:
                        process.wait(timeout=5)
                    except subprocess.TimeoutExpired:
                        process.kill()
                        process.wait()


def write_media(output, name, frames, *, still=-1):
    from PIL import Image

    (output / f"{name}.png").write_bytes(frames[still])
    images = []
    for frame in frames:
        with Image.open(BytesIO(frame)) as image:
            image.thumbnail((1080, 760))
            images.append(image.convert("P", palette=Image.Palette.ADAPTIVE))
    images[0].save(output / f"{name}.gif", save_all=True, append_images=images[1:],
                   duration=[950] * (len(images) - 1) + [1600], loop=0, disposal=2)


def capture(page, output, address):
    from playwright.sync_api import expect
    expect.set_options(timeout=45_000)

    def heading(name):
        return page.get_by_role("heading", name=name, exact=True)

    def scroll(locator):
        expect(locator).to_be_visible()
        locator.evaluate("(element) => element.scrollIntoView({block: 'start'})")

    def navigate(name):
        page.get_by_text(name, exact=True).first.click()
        expect(heading(name)).to_be_visible()

    page.goto(address, wait_until="domcontentloaded")
    expect(page.get_by_text("Synthetic fixture · Operator mode")).to_be_visible()
    expect(page.get_by_role("button", name="Run batch")).to_be_enabled()
    scroll(heading("Overview"))
    overview = page.screenshot()
    scroll(heading("Processing schedule and batch"))
    page.get_by_role("button", name="Run batch").click()
    expect(page.get_by_text("Skipped duplicate", exact=False)).to_be_visible()
    scroll(heading("Processing schedule and batch"))
    write_media(output, "overview-dashboard", [overview, page.screenshot()], still=0)

    scroll(heading("Extraction confidence"))
    expect(page.get_by_text("Released fields", exact=True)).to_be_visible()
    confidence = page.screenshot()
    scroll(heading("Extraction accuracy"))
    write_media(output, "confidence-panel", [confidence, page.screenshot()], still=0)

    navigate("Review workbench")
    expect(page.locator("canvas").first).to_be_visible()
    scroll(heading("Review workbench"))
    masked = page.screenshot()
    page_input = page.get_by_role("spinbutton", name="Page", exact=True)
    page_input.fill("2")
    page_input.press("Enter")
    expect(page.get_by_text("A verified masked preview is unavailable for this page.")).to_be_visible()
    write_media(output, "masked-workbench", [masked, page.screenshot()], still=0)

    navigate("Published outputs")
    scroll(heading("Document lineage"))
    expect(page.get_by_text("Source document", exact=True)).to_be_visible()
    forward = page.screenshot()
    page.get_by_text("Gold value → document", exact=True).click()
    expect(page.get_by_text("Gold value", exact=True).first).to_be_visible()
    scroll(heading("Document lineage"))
    write_media(output, "lineage-trace", [forward, page.screenshot()])

    navigate("Runtime agent")
    question = page.get_by_role("button", name="Where did the published W-2 wages come from?")
    expect(question).to_be_visible()
    prompt = page.screenshot()
    question.click()
    expect(page.get_by_text("Published wages of 25000.00 trace to 1 source field.")).to_be_visible()
    write_media(output, "runtime-agent-panel", [prompt, page.screenshot()])


def main():
    from playwright.sync_api import sync_playwright

    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--output", type=Path, default=REPO / "docs" / "media")
    output = parser.parse_args().output
    output.mkdir(parents=True, exist_ok=True)
    with fixture_server() as address, sync_playwright() as playwright:
        browser = playwright.chromium.launch()
        page = browser.new_page(viewport={"width": 1440, "height": 980}, device_scale_factor=1, timezone_id="UTC")
        page.set_default_timeout(45_000)
        try:
            capture(page, output, address)
        finally:
            browser.close()


if __name__ == "__main__":
    main()
