# LedgerLens user guide

LedgerLens turns synthetic tax documents (W-2, 1099, 1098) into governed, published data on Databricks. Processing jobs move each document through Bronze, Silver and Gold. Reviewers approve or correct low-confidence fields. Each validated run is committed as a numbered **publication**. Every published value can be traced back to the exact document, field, extraction and review decision it came from.

All data in LedgerLens is synthetic. No real person, employer or client data is used.

The public app is at **https://rad-ledgerlens.streamlit.app**. It is read-only and needs no sign-in.

## Before you start

- **The first load after an idle period can take up to two minutes.** The shared Databricks SQL warehouse stops when it's idle and starts again on the next request. While it starts, the app shows a startup notice. If you see "Databricks data is temporarily unavailable", refresh once.
- **Everything you see belongs to one publication.** Every page shows the publication number and its run ID near the top, for example `Publication 2 · eb9dece4…`. Pages read that run only, so numbers stay consistent even if a new run publishes while you're browsing.
- **The public app shows published Gold only.** The review workbench, masked document previews, batch controls, raw extractions and evaluation data need a signed-in reviewer in the Databricks App. They aren't available on the public site.

Use the **Page** menu on the left to move between the three pages.

## Overview

The Overview page shows the state of the pipeline.

- **Live document states:** how many documents are currently in each state (received, extracted, needs review, auto-approved, approved, rejected, released, recalled). This chart is live. The rest of the page belongs to the pinned publication.
- **Publication caption:** the publication number, run ID and publication time (UTC) that the rest of the page uses.
- **Review volume at publication:** how many documents needed human review in that publication, by state.
- **Extraction accuracy:** for each form type, how many expected fields were extracted exactly right, compared with an answer key that only the evaluator job can read. If the evaluator hasn't finished for this publication yet, it shows "pending".
- **Extraction confidence:** choose a form type to see how many released fields fall into each confidence band (`[0, 0.5)`, `[0.5, 0.8)`, `[0.8, 1]` and `Unreported`).
- **Processing schedule and batch:** shown for information only. Running a batch needs operator mode.

## Published outputs

The Published outputs page has three sections.

### Document coverage

1. In **Person and tax year**, choose a synthetic person and year.
2. The **expected forms** table lists each form that person should have for that year, and whether it was received and released or is still missing.
3. The **totals** table shows that person-year's published wages, other income and mortgage interest.

### Pre-filled form

1. In **Form**, choose a form, for example `W-2 · 2025 · fixture-person-001`.
2. The table shows each published field and its value.
3. Click **Download PDF** or **Download JSON** to get the pre-filled form. Both files carry the publication number and run ID, so you can always tell which run a downloaded form came from.

### Document lineage

Lineage answers two questions, chosen with **Trace direction**:

- **Gold value → document:** where did this published number come from?
- **Document → output:** which published values did this document contribute to?

Each row of the lineage table shows:

| Column | Meaning |
|---|---|
| `gold_row_id`, `output_field` | The published Gold row and field, for example `wages` |
| `doc_id` | The source document (identified by the SHA-256 of its PDF) |
| `field_id` | The extracted field on that document |
| `extraction_id` | The extraction attempt that produced the value |
| `decision_id` | The reviewer decision that approved or corrected it. Blank when the field was auto-approved. |
| `extractor_name`, `extractor_version` | The adapter that read the document: digital PDF text, or Databricks `ai_parse_document` for scanned pages |
| `model_name`, `model_version` | The model behind the extractor, where one was used |

## How to test lineage

**Trace a published value back to its document:**
1. Open **Published outputs** and scroll to **Document lineage**.
2. Choose **Gold value → document**.
3. In **Gold value**, choose a value, for example `wages · <amount> · <person and year>`.
4. Read the table. You should see exactly one source document and extraction for the value. If a reviewer touched the field, you'll also see a decision ID.

**Trace a document forward to everything it produced:**
1. Choose **Document → output**.
2. In **Source document**, choose the `doc_id` you found above.
3. The table lists every published output that document fed. It should include the value you started from.

**Cross-check against the pre-filled form:**
1. In **Pre-filled form**, choose the same person, year and form.
2. The value in the form table should match the Gold value you traced.

**Cross-check against the runtime agent:**
1. Open **Runtime agent** and click **"Where did the published W-2 wages come from?"**
2. Open the `trace_value` tool call under the answer. Its run ID should match the publication shown on the other pages, and its arguments should point to the same Gold row and field you traced by hand.

## Runtime agent

The runtime agent is an LLM (`databricks-gpt-oss-20b`, hosted on Databricks) that answers questions about published Gold. It can only use two tools on the LedgerLens MCP server:

- `trace_value`: traces a published Gold value to its source document, field, extraction and decision.
- `documents_needing_review`: returns the publication's review count and the live list of documents waiting for review.

Both tools run fixed, parameterized, row-limited queries on Gold serving views, as a read-only identity. The agent can't run free-form SQL, and it can't read Silver, Bronze or evaluation data.

On the public app:
- **You choose from four preset questions.** There's no free-text box.
- **Each answer shows the tool calls the agent made and the publication run ID it used,** so you can check every answer against the lineage table.
- **Answers are cached per publication, and the app makes at most 12 model calls a day.** If a question shows "temporarily unavailable", the daily limit has been reached or the model couldn't be reached. Try again later.
- **The agent says when the data doesn't exist.** If the current publication has no 1099 or 1098, it answers that no such value was published instead of making one up.

## Privacy and masking

LedgerLens treats SSNs and TINs on the synthetic forms as sensitive:

- **Gold and the public app never contain identifier values.**
- **In Silver, Unity Catalog column masks decide what each caller sees.** Members of a governed identifier-readers group (empty by default) see full values. Reviewers see only the last four digits, as `****1234`. Everyone else, including the processing workloads, sees nothing.
- **Reviewers see masked document previews.** The pixels under each identifier are redacted, and a page is withheld if its redaction can't be verified.

## Reviewer and operator mode (Databricks App)

Signed-in members of the reviewer group use the same app as a Databricks App. There they can:
- Open the **Review workbench** to see masked previews and extracted fields with confidence badges.
- **Record decisions:** approve, correct a field, reject, or request reprocessing. Each decision is appended to an audit ledger and takes effect in the next publication.
- **Run a processing batch** and watch its status.

## Where to go next

- The repository README covers the architecture, governance model, publication model and how to rebuild everything from the repo.
- `docs/REVIEW-LOG.md` records the review findings from the build.
- `docs/evidence/` holds the live run evidence, including the runtime agent and MCP checks.
