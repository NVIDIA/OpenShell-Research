"""
Fixed constants, one-time data preparation, and runtime utilities for the
Nightshift autoresearch experiment.

Adapted from Andrej Karpathy's autoresearch (https://github.com/karpathy/autoresearch,
MIT License, Copyright (c) 2026 Andrej Karpathy). Changes: two hardware
profiles (laptop CPU, station GPU) selected by AUTORESEARCH_PROFILE, a
TinyStories dataset for the laptop profile, device-generic dataloading and
evaluation, and a cache directory that lives read-only in the sandbox image.

This file is not modified by the agent. It runs once at image build time
(download data, train the tokenizer) and is imported by train.py at run time.

Usage:
    AUTORESEARCH_PROFILE=laptop python prepare.py
"""

import argparse
import math
import os
import pickle
import sys
import time

import pyarrow as pa
import pyarrow.parquet as pq
import requests
import rustbpe
import tiktoken
import torch

# ---------------------------------------------------------------------------
# Profiles (fixed, do not modify). The harness selects one with AUTORESEARCH_PROFILE.
# ---------------------------------------------------------------------------

PROFILES = {
    # CPU inside Docker on a laptop. TinyStories keeps the entropy low enough that
    # a few-million-parameter model learns something visible in ninety seconds.
    "laptop": dict(
        DATASET="tinystories",
        MAX_SEQ_LEN=256,
        TIME_BUDGET=90,
        EVAL_TOKENS=2**17,
        VOCAB_SIZE=2048,
        TOKENIZER_CHARS=30_000_000,
        TRAIN_DOCS=150_000,
        VAL_DOCS=4_000,
        DEVICE="cpu",
        COMPILE=False,
        # Defaults train.py starts from; the agent may change them in train.py.
        DEPTH=4, ASPECT_RATIO=64, HEAD_DIM=64, WINDOW_PATTERN="L",
        DEVICE_BATCH_SIZE=32, TOTAL_BATCH_SIZE=2**14,
    ),
    # One NVIDIA GPU. This is the original autoresearch configuration.
    "station": dict(
        DATASET="climbmix",
        MAX_SEQ_LEN=2048,
        TIME_BUDGET=300,
        EVAL_TOKENS=40 * 524288,
        VOCAB_SIZE=8192,
        TOKENIZER_CHARS=1_000_000_000,
        NUM_SHARDS=10,
        DEVICE="cuda",
        COMPILE=True,
        DEPTH=8, ASPECT_RATIO=64, HEAD_DIM=128, WINDOW_PATTERN="SSSL",
        DEVICE_BATCH_SIZE=128, TOTAL_BATCH_SIZE=2**19,
    ),
}

PROFILE_NAME = os.environ.get("AUTORESEARCH_PROFILE", "laptop")
if PROFILE_NAME not in PROFILES:
    raise SystemExit(f"unknown AUTORESEARCH_PROFILE {PROFILE_NAME!r}; choose from {sorted(PROFILES)}")
PROFILE = PROFILES[PROFILE_NAME]

MAX_SEQ_LEN = PROFILE["MAX_SEQ_LEN"]
# The harness may pin the budget explicitly; it always matches the profile it selected.
TIME_BUDGET = int(os.environ.get("AUTORESEARCH_TIME_BUDGET", PROFILE["TIME_BUDGET"]))
EVAL_TOKENS = PROFILE["EVAL_TOKENS"]
VOCAB_SIZE = PROFILE["VOCAB_SIZE"]
DEVICE = PROFILE["DEVICE"]
COMPILE = PROFILE["COMPILE"]

CACHE_DIR = os.environ.get("AUTORESEARCH_CACHE", os.path.join(os.path.expanduser("~"), ".cache", "autoresearch"))
DATA_DIR = os.path.join(CACHE_DIR, "data")
TOKENIZER_DIR = os.path.join(CACHE_DIR, "tokenizer")

CLIMBMIX_URL = "https://huggingface.co/datasets/karpathy/climbmix-400b-shuffle/resolve/main"
CLIMBMIX_MAX_SHARD = 6542
TINYSTORIES_URL = "https://huggingface.co/datasets/karpathy/tinystories-gpt4-clean/resolve/main/tinystories_gpt4_clean.parquet"

# The validation shard is pinned per dataset so results are comparable.
VAL_SHARD = CLIMBMIX_MAX_SHARD if PROFILE["DATASET"] == "climbmix" else 1
VAL_FILENAME = f"shard_{VAL_SHARD:05d}.parquet"

# BPE split pattern (GPT-4 style, with \p{N}{1,2} instead of {1,3})
SPLIT_PATTERN = r"""'(?i:[sdmt]|ll|ve|re)|[^\r\n\p{L}\p{N}]?+\p{L}+|\p{N}{1,2}| ?[^\s\p{L}\p{N}]++[\r\n]*|\s*[\r\n]|\s+(?!\S)|\s+"""

SPECIAL_TOKENS = [f"<|reserved_{i}|>" for i in range(4)]
BOS_TOKEN = "<|reserved_0|>"

# ---------------------------------------------------------------------------
# Data download
# ---------------------------------------------------------------------------

def download_file(url, filepath, attempts=5):
    """Download one file with retries. Returns True on success."""
    if os.path.exists(filepath):
        return True
    for attempt in range(1, attempts + 1):
        try:
            response = requests.get(url, stream=True, timeout=60)
            response.raise_for_status()
            temp_path = filepath + ".tmp"
            with open(temp_path, "wb") as f:
                for chunk in response.iter_content(chunk_size=1024 * 1024):
                    if chunk:
                        f.write(chunk)
            os.rename(temp_path, filepath)
            print(f"  Downloaded {os.path.basename(filepath)}")
            return True
        except (requests.RequestException, IOError) as e:
            print(f"  Attempt {attempt}/{attempts} failed for {url}: {e}")
            for path in [filepath + ".tmp", filepath]:
                if os.path.exists(path):
                    try:
                        os.remove(path)
                    except OSError:
                        pass
            if attempt < attempts:
                time.sleep(2 ** attempt)
    return False


def download_climbmix(num_shards):
    """Training shards 0..num_shards-1 plus the pinned validation shard."""
    ids = list(range(min(num_shards, CLIMBMIX_MAX_SHARD)))
    if VAL_SHARD not in ids:
        ids.append(VAL_SHARD)
    for index in ids:
        filename = f"shard_{index:05d}.parquet"
        if not download_file(f"{CLIMBMIX_URL}/{filename}", os.path.join(DATA_DIR, filename)):
            raise SystemExit(f"could not download {filename}")


def download_tinystories(train_docs, val_docs):
    """One public parquet, split into a training shard (0) and the pinned validation shard (1)."""
    train_path = os.path.join(DATA_DIR, "shard_00000.parquet")
    val_path = os.path.join(DATA_DIR, VAL_FILENAME)
    if os.path.exists(train_path) and os.path.exists(val_path):
        print("Data: TinyStories shards already prepared")
        return
    source = os.path.join(DATA_DIR, "tinystories_gpt4_clean.parquet")
    if not download_file(TINYSTORIES_URL, source):
        raise SystemExit("could not download TinyStories")
    table = pq.read_table(source, columns=["text"])
    needed = train_docs + val_docs
    if table.num_rows < needed:
        raise SystemExit(f"TinyStories has {table.num_rows} rows; need {needed}")
    # Validation documents come from the end of the file so they never overlap training.
    pq.write_table(table.slice(0, train_docs), train_path, row_group_size=10_000)
    pq.write_table(table.slice(table.num_rows - val_docs, val_docs), val_path, row_group_size=10_000)
    os.remove(source)
    print(f"Data: wrote {train_docs} training and {val_docs} validation documents")


def download_data():
    os.makedirs(DATA_DIR, exist_ok=True)
    if PROFILE["DATASET"] == "climbmix":
        download_climbmix(PROFILE["NUM_SHARDS"])
    else:
        download_tinystories(PROFILE["TRAIN_DOCS"], PROFILE["VAL_DOCS"])

# ---------------------------------------------------------------------------
# Tokenizer training
# ---------------------------------------------------------------------------

def list_parquet_files():
    files = sorted(f for f in os.listdir(DATA_DIR) if f.endswith(".parquet") and not f.endswith(".tmp"))
    return [os.path.join(DATA_DIR, f) for f in files]


def text_iterator(max_chars, doc_cap=10_000):
    """Yield documents from the training split (all shards except the pinned val shard)."""
    parquet_paths = [p for p in list_parquet_files() if not p.endswith(VAL_FILENAME)]
    nchars = 0
    for filepath in parquet_paths:
        pf = pq.ParquetFile(filepath)
        for rg_idx in range(pf.num_row_groups):
            rg = pf.read_row_group(rg_idx)
            for text in rg.column("text").to_pylist():
                doc = text[:doc_cap] if len(text) > doc_cap else text
                nchars += len(doc)
                yield doc
                if nchars >= max_chars:
                    return


def train_tokenizer():
    """Train a BPE tokenizer with rustbpe and save it as a tiktoken pickle."""
    tokenizer_pkl = os.path.join(TOKENIZER_DIR, "tokenizer.pkl")
    token_bytes_path = os.path.join(TOKENIZER_DIR, "token_bytes.pt")
    if os.path.exists(tokenizer_pkl) and os.path.exists(token_bytes_path):
        print(f"Tokenizer: already trained at {TOKENIZER_DIR}")
        return
    os.makedirs(TOKENIZER_DIR, exist_ok=True)
    if len(list_parquet_files()) < 2:
        print("Tokenizer: need at least 2 data shards (1 train + 1 val). Download data first.")
        sys.exit(1)

    print("Tokenizer: training BPE tokenizer...")
    t0 = time.time()
    tokenizer = rustbpe.Tokenizer()
    tokenizer.train_from_iterator(text_iterator(PROFILE["TOKENIZER_CHARS"]), VOCAB_SIZE - len(SPECIAL_TOKENS), pattern=SPLIT_PATTERN)
    pattern = tokenizer.get_pattern()
    mergeable_ranks = {bytes(k): v for k, v in tokenizer.get_mergeable_ranks()}
    tokens_offset = len(mergeable_ranks)
    special_tokens = {name: tokens_offset + i for i, name in enumerate(SPECIAL_TOKENS)}
    enc = tiktoken.Encoding(name="rustbpe", pat_str=pattern, mergeable_ranks=mergeable_ranks, special_tokens=special_tokens)
    with open(tokenizer_pkl, "wb") as f:
        pickle.dump(enc, f)
    print(f"Tokenizer: trained in {time.time() - t0:.1f}s, saved to {tokenizer_pkl}")

    # token_bytes lookup for bits-per-byte evaluation; special tokens count zero bytes.
    special_set = set(SPECIAL_TOKENS)
    token_bytes_list = []
    for token_id in range(enc.n_vocab):
        token_str = enc.decode([token_id])
        token_bytes_list.append(0 if token_str in special_set else len(token_str.encode("utf-8")))
    torch.save(torch.tensor(token_bytes_list, dtype=torch.int32), token_bytes_path)

    test = "Hello world! Numbers: 123. Unicode: 你好"
    assert enc.decode(enc.encode_ordinary(test)) == test, "tokenizer roundtrip failed"
    print(f"Tokenizer: sanity check passed (vocab_size={enc.n_vocab})")

# ---------------------------------------------------------------------------
# Runtime utilities (imported by train.py)
# ---------------------------------------------------------------------------

class Tokenizer:
    """Minimal tokenizer wrapper. Training is handled above."""

    def __init__(self, enc):
        self.enc = enc
        self.bos_token_id = enc.encode_single_token(BOS_TOKEN)

    @classmethod
    def from_directory(cls, tokenizer_dir=TOKENIZER_DIR):
        with open(os.path.join(tokenizer_dir, "tokenizer.pkl"), "rb") as f:
            enc = pickle.load(f)
        return cls(enc)

    def get_vocab_size(self):
        return self.enc.n_vocab

    def get_bos_token_id(self):
        return self.bos_token_id

    def encode(self, text, prepend=None, num_threads=8):
        if prepend is not None:
            prepend_id = prepend if isinstance(prepend, int) else self.enc.encode_single_token(prepend)
        if isinstance(text, str):
            ids = self.enc.encode_ordinary(text)
            if prepend is not None:
                ids.insert(0, prepend_id)
        elif isinstance(text, list):
            ids = self.enc.encode_ordinary_batch(text, num_threads=num_threads)
            if prepend is not None:
                for row in ids:
                    row.insert(0, prepend_id)
        else:
            raise ValueError(f"Invalid input type: {type(text)}")
        return ids

    def decode(self, ids):
        return self.enc.decode(ids)


def get_token_bytes(device=DEVICE):
    path = os.path.join(TOKENIZER_DIR, "token_bytes.pt")
    with open(path, "rb") as f:
        return torch.load(f, map_location=device)


def _document_batches(split, tokenizer_batch_size=128):
    """Infinite iterator over document batches from parquet files."""
    parquet_paths = list_parquet_files()
    assert len(parquet_paths) > 0, "No parquet files found. Run prepare.py first."
    val_path = os.path.join(DATA_DIR, VAL_FILENAME)
    if split == "train":
        parquet_paths = [p for p in parquet_paths if p != val_path]
        assert len(parquet_paths) > 0, "No training shards found."
    else:
        parquet_paths = [val_path]
    epoch = 1
    while True:
        for filepath in parquet_paths:
            pf = pq.ParquetFile(filepath)
            for rg_idx in range(pf.num_row_groups):
                rg = pf.read_row_group(rg_idx)
                batch = rg.column("text").to_pylist()
                for i in range(0, len(batch), tokenizer_batch_size):
                    yield batch[i:i + tokenizer_batch_size], epoch
        epoch += 1


def make_dataloader(tokenizer, B, T, split, buffer_size=1000, device=DEVICE):
    """
    BOS-aligned dataloader with best-fit packing. Every row starts with BOS;
    documents are packed best-fit to minimize cropping, and when nothing fits the
    shortest document is cropped to fill the row exactly. No padding.
    """
    assert split in ["train", "val"]
    row_capacity = T + 1
    batches = _document_batches(split)
    bos_token = tokenizer.get_bos_token_id()
    doc_buffer = []
    epoch = 1

    def refill_buffer():
        nonlocal epoch
        doc_batch, epoch = next(batches)
        doc_buffer.extend(tokenizer.encode(doc_batch, prepend=bos_token))

    use_cuda = str(device).startswith("cuda")
    row_buffer = torch.empty((B, row_capacity), dtype=torch.long)
    cpu_buffer = torch.empty(2 * B * T, dtype=torch.long, pin_memory=use_cuda)
    device_buffer = torch.empty(2 * B * T, dtype=torch.long, device=device) if use_cuda else cpu_buffer
    cpu_inputs = cpu_buffer[:B * T].view(B, T)
    cpu_targets = cpu_buffer[B * T:].view(B, T)
    inputs = device_buffer[:B * T].view(B, T)
    targets = device_buffer[B * T:].view(B, T)

    while True:
        for row_idx in range(B):
            pos = 0
            while pos < row_capacity:
                while len(doc_buffer) < buffer_size:
                    refill_buffer()
                remaining = row_capacity - pos
                best_idx = -1
                best_len = 0
                for i, doc in enumerate(doc_buffer):
                    doc_len = len(doc)
                    if doc_len <= remaining and doc_len > best_len:
                        best_idx = i
                        best_len = doc_len
                if best_idx >= 0:
                    doc = doc_buffer.pop(best_idx)
                    row_buffer[row_idx, pos:pos + len(doc)] = torch.tensor(doc, dtype=torch.long)
                    pos += len(doc)
                else:
                    shortest_idx = min(range(len(doc_buffer)), key=lambda i: len(doc_buffer[i]))
                    doc = doc_buffer.pop(shortest_idx)
                    row_buffer[row_idx, pos:pos + remaining] = torch.tensor(doc[:remaining], dtype=torch.long)
                    pos += remaining
        cpu_inputs.copy_(row_buffer[:, :-1])
        cpu_targets.copy_(row_buffer[:, 1:])
        if use_cuda:
            device_buffer.copy_(cpu_buffer, non_blocking=True)
        yield inputs, targets, epoch

# ---------------------------------------------------------------------------
# Evaluation (DO NOT CHANGE — this is the fixed metric)
# ---------------------------------------------------------------------------

@torch.no_grad()
def evaluate_bpb(model, tokenizer, batch_size, device=DEVICE):
    """
    Bits per byte: vocabulary-independent. Sums per-token cross-entropy in nats
    and target byte lengths, then converts nats per byte to bits per byte.
    Special tokens (byte length 0) are excluded. Uses the fixed MAX_SEQ_LEN.
    """
    token_bytes = get_token_bytes(device=device)
    val_loader = make_dataloader(tokenizer, batch_size, MAX_SEQ_LEN, "val", device=device)
    steps = max(1, EVAL_TOKENS // (batch_size * MAX_SEQ_LEN))
    total_nats = 0.0
    total_bytes = 0
    for _ in range(steps):
        x, y, _ = next(val_loader)
        loss_flat = model(x, y, reduction="none").view(-1)
        y_flat = y.view(-1)
        nbytes = token_bytes[y_flat]
        mask = nbytes > 0
        total_nats += (loss_flat * mask).sum().item()
        total_bytes += nbytes.sum().item()
    return total_nats / (math.log(2) * total_bytes)

# ---------------------------------------------------------------------------
# Main: one-time preparation (image build time)
# ---------------------------------------------------------------------------

if __name__ == "__main__":
    parser = argparse.ArgumentParser(description="Prepare data and tokenizer for the autoresearch experiment")
    parser.parse_args()
    print(f"Profile: {PROFILE_NAME}  cache: {CACHE_DIR}")
    download_data()
    train_tokenizer()
    print("Done! Ready to train.")
