"""Run the 8 base S7 queries (A-H) from AxiomS7.txt in their canonical order.

Background:
    Queries E, F, G, H operate on the 5 reference papers under sandbox/papers/.
    Queries C and F each have two runs that exercise cross-run state persistence.
    All 10 runs share state — wiping between would break the demonstration.

The script:
    1. Saves the current travel-corpus state to state_corpus_backup/ (if not
       already present from earlier work).
    2. Wipes state/ so the base-query sequence starts cold.
    3. Runs A → B → C1 → C2 → D → E → F1 → F2 → G → H sequentially, teeing
       each agent's stdout to traces/base_<key>.txt.
    4. Restores the travel-corpus state.

Run from the S7code directory:
    uv run run_base_queries.py
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
PYTHON = str(ROOT / ".venv" / "bin" / "python")

# (key, query) in canonical order. C and F have two runs each.
QUERIES: list[tuple[str, str]] = [
    ("A",
     "Fetch https://en.wikipedia.org/wiki/Claude_Shannon and tell me his birth date, death date, and three key contributions to information theory."),
    ("B",
     "Find 3 family-friendly things to do in Tokyo this weekend. Check Saturday's weather forecast there and tell me which one is most appropriate."),
    ("C_run1",
     "My mom's birthday is 15 May 2026. Remember that and create reminders for two weeks before and on the day."),
    ("C_run2",
     "When is mom's birthday?"),
    ("D",
     "Search for 'Python asyncio best practices', read the top 3 results, and give me a short numbered list of the advice they agree on."),
    ("E",
     "Index the file papers/attention.md and tell me what the three key contributions of the Transformer architecture are according to this paper."),
    ("F_run1",
     "Index every .md file under papers/. Confirm how many chunks were indexed in total."),
    ("F_run2",
     "Across the papers I have indexed, what do they say about chain-of-thought reasoning?"),
    ("G",
     "Across these papers, how do they handle the credit assignment problem?"),
    ("H",
     "Compare how the ReAct paper and the Chain-of-Thought paper differ in their treatment of intermediate reasoning."),
]


def save_corpus_state() -> None:
    if BACKUP.exists():
        print(f"[backup] {BACKUP.name}/ already present — preserving as is")
        return
    shutil.copytree(STATE, BACKUP)
    print(f"[backup] saved corpus state → {BACKUP.name}/")


def wipe_state() -> None:
    for name in ("memory.json", "index.faiss", "index_ids.json"):
        p = STATE / name
        if p.exists():
            p.unlink()
    print("[wipe] memory.json + FAISS files removed")


def restore_corpus_state() -> None:
    if not BACKUP.exists():
        print("[restore] no backup present — skipping")
        return
    for name in ("memory.json", "index.faiss", "index_ids.json"):
        src = BACKUP / name
        if src.exists():
            shutil.copy2(src, STATE / name)
    print(f"[restore] corpus state restored from {BACKUP.name}/")


def run_agent(query: str, trace_path: Path) -> bool:
    """Run agent7.py and tee output to trace_path. Returns True on success."""
    header = (
        f"timestamp: {datetime.now().isoformat(timespec='seconds')}\n"
        f"query: {query}\n"
        f"{'=' * 78}\n\n"
    )
    trace_path.write_text(header, encoding="utf-8")
    with trace_path.open("a", encoding="utf-8") as f:
        proc = subprocess.Popen(
            [PYTHON, "agent7.py", query],
            cwd=str(ROOT),
            stdout=subprocess.PIPE,
            stderr=subprocess.STDOUT,
            text=True,
            bufsize=1,
        )
        assert proc.stdout is not None
        for line in proc.stdout:
            print(line, end="")
            f.write(line)
        proc.wait()
    return proc.returncode == 0


def main() -> int:
    TRACES.mkdir(exist_ok=True)
    save_corpus_state()
    wipe_state()

    failures: list[str] = []
    for key, query in QUERIES:
        print()
        print("#" * 78)
        print(f"# Base query {key}")
        print(f"# {query}")
        print("#" * 78)
        trace = TRACES / f"base_{key}.txt"
        if not run_agent(query, trace):
            failures.append(key)
            print(f"[FAIL] {key}")

    restore_corpus_state()

    print()
    print("=" * 78)
    print(f"completed {len(QUERIES) - len(failures)}/{len(QUERIES)} base queries")
    if failures:
        print(f"failures: {failures}")
    return 0 if not failures else 1


if __name__ == "__main__":
    sys.exit(main())
