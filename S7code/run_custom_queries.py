"""Run the five custom queries twice: once with the indexed corpus and once
without. Saves both traces under traces/. Backs up state/ before the wipe so
the corpus index does not need to be rebuilt between queries.

Run from the S7code directory:
    uv run run_custom_queries.py           # all five queries
    uv run run_custom_queries.py 1 3       # only #1 and #3
    uv run run_custom_queries.py --with    # corpus run only
    uv run run_custom_queries.py --without # no-corpus run only

Output lands in traces/Q<N>_<mode>.txt — one file per (query, mode) pair.
"""
from __future__ import annotations

import shutil
import subprocess
import sys
from datetime import datetime
from pathlib import Path

ROOT = Path(__file__).parent
STATE = ROOT / "state"
BACKUP = ROOT / "state_corpus_backup"
TRACES = ROOT / "traces"

QUERIES = [
    "Across the destinations I have indexed, which ones are well-suited for a quiet honeymoon away from crowds?",
    "Which of my indexed destinations are best for solo female travelers on a tight budget?",
    "Across the destinations I have indexed, compare how Kyoto and Hoi An differ in their approach to heritage preservation and visitor experience.",
    "I have three days in Iceland in late October. Based on the destinations I have indexed, what should I prioritize and what should I skip?",
    "Across my indexed destinations, which ones can a vegetarian comfortably eat at without struggling to find food?",
]


def backup_state() -> None:
    if BACKUP.exists():
        shutil.rmtree(BACKUP)
    shutil.copytree(STATE, BACKUP)
    print(f"[backup] saved corpus state → {BACKUP.name}/")


def restore_state() -> None:
    if STATE.exists():
        shutil.rmtree(STATE)
    shutil.copytree(BACKUP, STATE)
    print(f"[restore] corpus state restored from {BACKUP.name}/")


def wipe_state() -> None:
    for name in ("memory.json", "index.faiss", "index_ids.json"):
        p = STATE / name
        if p.exists():
            p.unlink()
    print("[wipe] memory.json + FAISS files removed")


PYTHON = str(ROOT / ".venv" / "bin" / "python")


def run_agent(query: str) -> str:
    """Run agent7.py as a subprocess and capture the full trace.

    Uses the project venv's python directly (uv may not be on PATH in
    background-task contexts)."""
    result = subprocess.run(
        [PYTHON, "agent7.py", query],
        cwd=str(ROOT),
        capture_output=True,
        text=True,
        timeout=600,
    )
    return result.stdout + ("\n--- stderr ---\n" + result.stderr if result.stderr.strip() else "")


def save_trace(qnum: int, mode: str, query: str, trace: str) -> Path:
    TRACES.mkdir(exist_ok=True)
    path = TRACES / f"Q{qnum}_{mode}.txt"
    header = (
        f"Query #{qnum} ({mode.replace('_', ' ')})\n"
        f"timestamp: {datetime.now().isoformat(timespec='seconds')}\n"
        f"query: {query}\n"
        f"{'=' * 78}\n\n"
    )
    path.write_text(header + trace, encoding="utf-8")
    return path


def run_one(qnum: int, mode: str) -> None:
    query = QUERIES[qnum - 1]
    print(f"\n{'#' * 78}")
    print(f"# Query {qnum} — {mode.replace('_', ' ')}")
    print(f"# {query}")
    print(f"{'#' * 78}")
    trace = run_agent(query)
    out = save_trace(qnum, mode, query, trace)
    last_lines = trace.strip().splitlines()[-5:]
    print(f"[saved] {out}")
    for line in last_lines:
        print(f"  | {line}")


def parse_args(argv: list[str]) -> tuple[list[int], set[str]]:
    modes = {"with_corpus", "without_corpus"}
    if "--with" in argv:
        modes = {"with_corpus"}
        argv.remove("--with")
    if "--without" in argv:
        modes = {"without_corpus"}
        argv.remove("--without")
    nums = [int(a) for a in argv if a.isdigit()]
    if not nums:
        nums = list(range(1, len(QUERIES) + 1))
    return nums, modes


def main() -> int:
    nums, modes = parse_args(sys.argv[1:])
    if not BACKUP.exists() and "with_corpus" in modes:
        backup_state()  # snapshot current (indexed) state

    if "with_corpus" in modes:
        if not STATE.exists() or not (STATE / "index.faiss").exists():
            print("restoring corpus before with-corpus runs")
            restore_state()
        for n in nums:
            run_one(n, "with_corpus")
        backup_state()  # refresh backup (memory grows via memory.remember)

    if "without_corpus" in modes:
        for n in nums:
            wipe_state()
            run_one(n, "without_corpus")
        restore_state()

    return 0


if __name__ == "__main__":
    sys.exit(main())
