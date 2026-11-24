# LedgerLens

LedgerLens is a portfolio project for governed document AI on Databricks. Publication is the core reliability feature. Processing jobs build run-stamped snapshots, validate them, and append a publication record. The app pins each interaction to a published run so a partial batch, retry, correction, or recall cannot mix data from different releases.

The project will show reliability evidence alongside AI-assisted delivery metrics from GitHub PR and Actions history. Codex Build agents implement the work, Claude supplies adversarial review, and Ron owns decisions and release approval. The release README will link to the supporting evidence.

This checkout is the bootstrap for review. See the [contract](CONTRACT.md), [setup and guardrail checks](docs/BOOTSTRAP.md), [decisions](docs/DECISIONS.md), and [review log](docs/REVIEW-LOG.md).
