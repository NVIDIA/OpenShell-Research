---
title: Run autoresearch
description: Let an agent train a small GPT overnight, one fixed-budget experiment at a time, inside an OpenShell sandbox on a laptop or a DGX Station.
agent_markdown: true
---

# Run autoresearch

`autoresearch` is Andrej Karpathy's
[autoresearch](https://github.com/karpathy/autoresearch) loop run under
Nightshift. An agent edits one training script, trains a small GPT for a fixed
wall-clock budget, reads the validation bits per byte, keeps the change if the
number went down and discards it otherwise, and repeats until you stop it.

What Nightshift adds is the envelope. The agent works in an OpenShell sandbox
whose opening policy reaches only its model endpoint. The fixed data,
tokenizer, evaluator, and scorer are baked read-only into the sandbox image. If
the agent wants a package or a dataset, it has to ask, and a reviewer decides
against the folder's `reviewer.md`. The ledger is written by the host.

## Two profiles, one folder

| Profile | Hardware | Data | Budget per trial | Model start |
| --- | --- | --- | --- | --- |
| `laptop` (default) | CPU inside Docker, for example Docker Desktop on a Mac | TinyStories, 150k training stories, 2048-token vocabulary | 90 s | 4 layers, about 5M parameters |
| `station` | One NVIDIA GPU, for a DGX Station | climbmix shards, 8192-token vocabulary, the original autoresearch data | 300 s | 8 layers, the original configuration |

The profile is selected with `--profile`, and every constant it fixes lives in
`experiments/autoresearch/image/prepare.py` under `PROFILES`. The agent's
`train.py` reads its starting hyperparameters from the active profile and is
free to change them.

Both profiles have run. The station profile was calibrated on a DGX Station
(GB300, driver 590, CUDA 13.1) on 2026-09-04 with Claude Code as the agent:

| | Baseline | Second trial |
| --- | ---: | ---: |
| `val_bpb` | 0.971971 | 0.967259 |
| Optimizer steps in 300 s | 1,442 | 2,864 |
| Tokens per second | about 2.5M | about 2.5M |
| Model FLOPs utilization | 60% | 60% |
| Peak GPU memory | 51 GB | 51 GB |

`torch.compile` stays on for the GPU profile; the first compiled step costs
about 40 seconds, which the budget excludes. Each trial takes roughly six and a
half minutes end to end, so an eight-hour night is about 70 experiments.

!!! note "GPU sandboxes need read-only `/sys`"
    `policy.json` lists `/sys` in the read-only filesystem paths. Without it,
    `nvidia-smi` works but CUDA initialization crashes inside the sandbox on
    Grace Blackwell, because the driver reads CPU topology from sysfs. Keep
    that entry in any GPU experiment's opening policy.

## Build the image

The image is the OpenShell base image plus PyTorch and the fixed files under
`/opt/autoresearch`. Building it downloads the data and trains the tokenizer,
so the sandbox itself needs no network access to start training.

```bash
npm run image:autoresearch
```

For the station profile, on the GPU machine (the host needs the NVIDIA
Container Toolkit and a CDI spec, `sudo nvidia-ctk cdi generate --output=/etc/cdi/nvidia.yaml`,
generated before the gateway starts):

```bash
docker build --build-arg PROFILE=station --build-arg TORCH_INDEX=https://download.pytorch.org/whl/cu130 \
  --tag nightshift/autoresearch:station --file experiments/autoresearch/image/Dockerfile experiments/autoresearch/image
```

## Run

Set the agent's model key in `.env` (see the
[configuration reference](reference/configuration.md)). Then:

```bash
nightshift run autoresearch --runtime claude-code
```

Like upstream autoresearch, the run continues until you stop it: press Ctrl-C
once and Nightshift settles any pending proposals, takes a final score, writes
the evidence, and prints the ledger. Pass `--minutes 480` instead for a fixed
eight-hour night.

The default reviewer is `model-reviewer`, which needs an OpenAI
Responses-compatible endpoint and reads `experiments/autoresearch/reviewer.md`.
To run with no reviewer model at all, use `--reviewer reject-all`; the agent
then has exactly what the image provides.

Useful variants:

```bash
nightshift run autoresearch --runtime codex --image nightshift/autoresearch:laptop
```

```bash
nightshift run autoresearch --profile station --minutes 480
```

The simplest way to run the station profile is to run Nightshift on the GPU
machine itself: clone the repository there, `npm ci`, and the harness finds
the local gateway and its certificates automatically. It also works from
another machine over an ssh tunnel to the gateway; see the
[configuration reference](reference/configuration.md#openshell).

## What happens during a run

1. The harness creates the sandbox from the profile's image with the opening
   policy in `policy.json` plus an egress rule for the agent's model endpoint,
   uploads `workdir/` to `/sandbox/work`, and makes it a git repository with
   one pristine commit.
2. The agent reads `program.md`. Its first experiment is the baseline: run
   `train.py` unchanged and read the number.
3. Each turn after that is one experiment: edit `train.py`, commit, run for the
   fixed budget, read `val_bpb`, keep or `git reset`. The driver resumes the
   agent between turns and rotates to a fresh context when it must.
4. Every 20 seconds the harness runs the scorer, `/opt/autoresearch/score.py`,
   inside the sandbox. When a new result exists, a row lands in `results.tsv`
   with the time, the agent's turn, the commit hash and message, and the score.
5. If the agent asks for network capability, the proposal goes to the reviewer.
   `reviewer.md` tells it to grant read-only access to PyPI, Hugging Face, and
   documentation, and to refuse anything that writes outward.
6. At the deadline the harness takes a final score, classifies the run, redacts
   the evidence, and deletes the sandbox.

## Read the ledger

```bash
nightshift report
```

With no argument that is the latest run; `runs/latest` links to it, and the
report also prints automatically when a run ends.

`results.tsv` is the morning read:

```text
time	turn	commit	value	done	description
2026-09-03T21:02:11Z	2	a1b2c3d	1.312804	false	nightshift: initial workdir
2026-09-03T21:06:40Z	4	b2c3d4e	1.288119	false	raise matrix LR to 0.06
```

`outcome.json` adds `score.best`, `score.last`, `score.trials`, and the
validity verdict. The agent's own notes are in its working directory, which is
gone with the sandbox unless you pass `--keep`; its reasoning is in
`events.jsonl`.

## What the score guarantees, and what it does not

Be precise about the trust boundary, because the whole point is a ledger you
can believe in the morning.

- **Fixed and read-only:** the data, the tokenizer, `evaluate_bpb`, and the
  scorer. They live under `/opt`, which the sandbox policy makes read-only, and
  are owned by root inside the image.
- **Host-written:** the ledger. The agent cannot add, edit, or delete rows, and
  every row names the commit it belongs to.
- **Budget:** the scorer treats a result whose training time exceeded the
  budget by more than 15 percent as no result, and the driver's per-turn timeout
  bounds the worst case.
- **The agent's:** `train.py`, including the model code. The number is produced
  by the agent's own training process calling the fixed evaluator. A cooperative
  research agent's ledger is trustworthy at the autoresearch level plus
  tamper-proof bookkeeping. A deliberately deceptive agent could still
  misreport its own loss, because within one sandbox it controls the process
  that computes it.

Re-scoring a checkpoint in a clean, network-less second sandbox would close the
last gap. It is the natural next step and is not in this release.

## Change the experiment

Everything you might edit is in `experiments/autoresearch/`:

- `program.md` is the agent's instructions. It is deliberately close to the
  upstream `program.md`, minus the rules that policy now enforces.
- `reviewer.md` is what the reviewer may grant. Tighten or loosen it per run.
- `experiment.json` holds the profiles, the score poll interval, the default
  runtime and reviewer, and the resume nudge the driver uses between turns.
- `image/prepare.py` holds the fixed constants per profile. Changing them means
  rebuilding the image, which is the point: fixed means fixed.

`workdir/train.py` and `image/prepare.py` are adapted from upstream under the
MIT License; see `experiments/autoresearch/NOTICE.md`.
