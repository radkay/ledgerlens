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

The Published outputs page has three sections, in this order: Document coverage, Document lineage and Pre-filled form.

### Document coverage

1. In **Person and tax year**, choose a synthetic person and year.
2. The **Form / Status** table lists each form that person should have for that year, and whether it's **Present** or **Missing**.
3. The **totals** table shows that person-year's published wages, other income and mortgage interest.

### Document lineage

Lineage answers two questions, chosen with **Trace direction**:

- **Document → output** (the default): which published values did this document contribute to?
- **Gold value → document:** where did this published value come from?

**Gold value** shows the field, value, output type, person and tax year. Use the output type to tell identical amounts apart. For example, these two Gold rows trace back to the same source field:

- `wages · 25000.00 · person-year total · fixture-person-001 · 2025`
- `wages · 25000.00 · pre-filled W-2 · fixture-person-001 · 2025`

A coverage entry looks like `status · present · expected W-2 · fixture-person-001 · 2025`.

The lineage table shows these columns from left to right. Scroll sideways for the remaining columns.

| Column | Meaning |
|---|---|
| `output_field` | The published field, for example `wages` |
| `extractor_name` | The adapter that read the document, for example `pypdf` for a digital PDF or Databricks `ai_parse_document` for scanned pages |
| `extractor_version` | The adapter version |
| `decision_id` | The reviewer decision that approved or corrected the field. Empty for an auto-approved field. |
| `doc_id` | The source document, identified by the SHA-256 of its PDF |
| `field_id` | The extracted field on the document |
| `extraction_id` | The extraction attempt that produced the value |
| `gold_row_id` | The published Gold row that received the value |
| `model_name` | The model behind the extractor, when used |
| `model_version` | The model version, when used |

The table displays the first 12 characters of long IDs. Expand **Full lineage identifiers** below the table to copy the complete IDs by row.

### Pre-filled form

1. In **Form**, choose a form, for example `W-2 · 2025 · fixture-person-001`.
2. The table shows each published field and its value.
3. Click **Download PDF** or **Download JSON** to get the pre-filled form. Both files carry the publication number and run ID, so you can always tell which run a downloaded form came from.

## How to test lineage

**Trace a document forward to everything it produced:**
1. Open **Published outputs** and scroll to **Document lineage**. **Document → output** is selected by default.
2. In **Source document**, keep or choose a document.
3. The table lists every published output that document fed. For the demo W-2 you should see three rows: two `wages` rows (the person-year total and the pre-filled form) and one `status` row (the W-2 is present). All three share the same `doc_id` and `field_id`.

**Trace a published value back to its document:**
1. Choose **Gold value → document**. The picker initially selects the first available published amount.
2. Choose `wages · 25000.00 · pre-filled W-2 · fixture-person-001 · 2025` in **Gold value**.
3. Read the table. You should see one source document, one field and one extraction. Expand **Full lineage identifiers** to compare complete IDs with other outputs.
4. Choose a `status · missing` entry. The app shows “No source field links were published for this selection.” A missing form has no source document.

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
