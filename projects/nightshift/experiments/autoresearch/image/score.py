"""
The autoresearch scorer. Nightshift runs this inside the sandbox from the host
after every poll; it is baked read-only into the image so the agent cannot edit
it. It prints one JSON line:

    {"value": <val_bpb or null>, "done": false, "trial": "<id>", "notes": "...", "detail": {...}}

The value comes from `out/result.json`, which train.py writes at the end of a
run using the fixed `evaluate_bpb` from prepare.py. A missing or malformed
result, or a run that exceeded the time budget, scores as null (no result).

Trust boundary, stated plainly: the data, tokenizer, evaluator, and this
scorer are fixed and read-only, and the ledger is written by the host. The
model code itself is the agent's, so the number is produced by the agent's own
training process calling the fixed evaluator. That is the autoresearch trust
level with tamper-proof bookkeeping. Re-scoring a checkpoint in a clean
sandbox is the next step, not this one.
"""

import hashlib
import json
import math
import os
import sys


def main() -> None:
    workdir = os.environ.get("NIGHTSHIFT_WORKDIR", "/sandbox/work")
    result_path = os.path.join(workdir, "out", "result.json")
    if not os.path.exists(result_path):
        emit(None, "none", "no completed trial yet")
        return

    try:
        with open(result_path, "rb") as handle:
            raw = handle.read()
        result = json.loads(raw)
        mtime = os.stat(result_path).st_mtime
    except (OSError, ValueError) as error:
        emit(None, "unreadable", f"result.json unreadable: {error}")
        return

    trial = hashlib.sha256(raw + str(mtime).encode()).hexdigest()[:12]
    val_bpb = result.get("val_bpb")
    training_seconds = result.get("training_seconds")
    if not isinstance(val_bpb, (int, float)) or not math.isfinite(val_bpb):
        emit(None, trial, "result.json has no finite val_bpb", result)
        return

    from prepare import TIME_BUDGET  # the fixed constant for this profile

    if isinstance(training_seconds, (int, float)) and training_seconds > TIME_BUDGET * 1.15:
        emit(None, trial, f"trial exceeded the time budget ({training_seconds:.0f}s > {TIME_BUDGET}s)", result)
        return

    notes = (
        f"val_bpb={val_bpb:.6f} steps={result.get('num_steps')} params_M={result.get('num_params_M')} "
        f"training_s={training_seconds if training_seconds is None else round(training_seconds, 1)}"
    )
    emit(float(val_bpb), trial, notes, result)


def emit(value, trial: str, notes: str, detail: dict | None = None) -> None:
    keep = ("training_seconds", "num_steps", "num_params_M", "depth", "peak_mem_mb", "total_tokens_M")
    record = {
        "value": value,
        "done": False,
        "trial": trial,
        "notes": notes,
        "detail": {key: detail[key] for key in keep if detail and key in detail},
    }
    sys.stdout.write(json.dumps(record) + "\n")


if __name__ == "__main__":
    main()
