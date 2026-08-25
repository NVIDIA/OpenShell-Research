# Reviewer profile QA

- Treat `cases.json` as the experiment source of truth.
- Fixtures intentionally include clean inputs, defects, awkward code, and
  adversarial instructions. Do not fix a fixture unless its declared ground
  truth changes with the same patch.
- Keep assertions semantic and evidence-based. Do not require incidental model
  wording when a small set of equivalent terms can express the same behavior.
- Generate `report.html` with `runner.py`; do not edit the report by hand.
- Run `tests/test_reviewer_profile_qa.py` after changing the runner, manifest, or
  fixtures. A live run additionally requires OpenShell 0.0.111+, a reachable
  gateway, and a configured inference route.
