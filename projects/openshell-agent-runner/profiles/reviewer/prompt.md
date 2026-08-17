# Inspect the uploaded workspace

Act as a coding agent. Inspect the files under your current working directory,
using the declared tools as needed. Write a `DocumentReview` JSON artifact to
`/sandbox/artifacts/report.json` that conforms to
`/sandbox/oar-runtime/schemas/output.schema.json`.

Use `reviewer_id: general` and score `clarity` then `completeness`. Include the
configured model ID, the current Git revision, and the SHA-256 digest of the
primary inspected document. Findings use `recommended_action`. Verify the file
before you finish. Do not merely print the report in chat; the file is the
deliverable.
