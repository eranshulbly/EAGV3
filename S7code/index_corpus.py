"""Bulk-index every PDF in sandbox/travel/ via the same code path the
`index_pdf` MCP tool uses. Skips files whose source label is already present
in memory so reruns are idempotent.

Running this through the agent loop would burn ~62 multi-iteration runs and
take half an hour. The script imports the MCP server's indexing function
directly, so the chunking, embedding, and FAISS persistence are identical
to what the agent would produce — but without the per-file Perception /
Decision overhead. The agent path is still exercised end-to-end by the five
custom queries.

Run from the S7code directory:
    uv run index_corpus.py
"""
from __future__ import annotations

import sys
import time
from pathlib import Path

# Import the indexing helpers from mcp_server. The FastMCP @tool decorator
# does not hide the underlying function — we can call it like any callable.
sys.path.insert(0, str(Path(__file__).parent))
import memory as _memory
from mcp_server import index_pdf  # noqa: E402

TRAVEL_DIR = Path(__file__).parent / "sandbox" / "travel"


def already_indexed(source_label: str) -> bool:
    """True if memory.json already contains chunks from this source."""
    for item in _memory._load():  # type: ignore[attr-defined]
        if item.kind == "fact" and item.source == source_label:
            return True
    return False


def main() -> int:
    pdfs = sorted(TRAVEL_DIR.glob("*.pdf"))
    if not pdfs:
        print(f"no PDFs found in {TRAVEL_DIR}")
        return 1

    total_chunks = 0
    skipped = 0
    failed: list[tuple[str, str]] = []

    t0 = time.time()
    for i, pdf in enumerate(pdfs, 1):
        rel = f"travel/{pdf.name}"
        source = f"sandbox:{rel}"
        if already_indexed(source):
            skipped += 1
            print(f"[{i:2}/{len(pdfs)}] skip  {pdf.name} (already indexed)")
            continue
        try:
            result = index_pdf(rel)
        except Exception as e:
            failed.append((pdf.name, str(e)))
            print(f"[{i:2}/{len(pdfs)}] FAIL  {pdf.name}: {e}")
            continue
        chunks = result.get("chunks_indexed", 0)
        pages = result.get("page_count", 0)
        total_chunks += chunks
        elapsed = time.time() - t0
        print(f"[{i:2}/{len(pdfs)}] ok    {pdf.name:38} pages={pages:3} chunks={chunks:3}  (+{elapsed:5.1f}s)")

    print()
    print(f"indexed {len(pdfs) - skipped - len(failed)} new PDFs "
          f"({skipped} skipped, {len(failed)} failed)")
    print(f"new chunks: {total_chunks}")
    print(f"elapsed:    {time.time() - t0:.1f}s")
    if failed:
        print("failures:")
        for name, msg in failed:
            print(f"  - {name}: {msg}")
    return 0 if not failed else 1


if __name__ == "__main__":
    sys.exit(main())
