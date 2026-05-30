"""Download the travel-destination corpus from Wikivoyage.

Wikivoyage exposes a REST endpoint that renders any article to a PDF:
    https://en.wikivoyage.org/api/rest_v1/page/pdf/<Title>
The script downloads one PDF per destination into sandbox/travel/, sleeping
between requests to stay polite. Failures are reported but do not abort the
run — the corpus manifest in the README lists which titles landed.

Selection rationale: ~60 destinations chosen for thematic spread across
beach, mountain, desert, urban, historical, off-beat, romance, and food
categories. The variety is what makes semantic-recall queries like "quiet
beach holiday on a budget" actually discriminate between guides — if every
PDF were a European capital, semantic search would degenerate into keyword
search over the same vocabulary.

Run from the S7code directory:
    uv run build_corpus.py
"""
from __future__ import annotations

import sys
import time
from pathlib import Path

import httpx

OUT_DIR = Path(__file__).parent / "sandbox" / "travel"
BASE_URL = "https://en.wikivoyage.org/api/rest_v1/page/pdf"
USER_AGENT = "EAGV3-S7-assignment/1.0 (educational; pypdf-indexing demo)"
SLEEP_BETWEEN = 1.5  # seconds; Wikimedia asks for politeness

# Themed selection. Tag is for documentation only; not used by the indexer.
DESTINATIONS: list[tuple[str, str]] = [
    # iconic cities (broad)
    ("Paris", "iconic"),
    ("Tokyo", "iconic"),
    ("London", "iconic"),
    ("New_York_City", "iconic"),
    ("Rome", "iconic"),
    ("Barcelona", "iconic"),
    ("Istanbul", "iconic"),
    ("Cairo", "iconic"),
    ("Bangkok", "iconic"),
    ("Singapore", "iconic"),
    ("Dubai", "iconic"),
    ("Sydney", "iconic"),
    ("Rio_de_Janeiro", "iconic"),

    # beach / island
    ("Bali", "beach"),
    ("Maldives", "beach"),
    ("Santorini", "beach"),
    ("Phuket", "beach"),
    ("Maui", "beach"),
    ("Goa", "beach"),
    ("Mykonos", "beach"),
    ("Boracay", "beach"),

    # mountain / nature
    ("Banff_National_Park", "mountain"),
    ("Queenstown_(New_Zealand)", "mountain"),
    ("Interlaken", "mountain"),
    ("Cusco", "mountain"),
    ("Reykjavík", "mountain"),
    ("Kathmandu", "mountain"),
    ("Aspen", "mountain"),
    ("Zermatt", "mountain"),

    # desert / unique landscapes
    ("Marrakech", "desert"),
    ("Petra", "desert"),
    ("San_Pedro_de_Atacama", "desert"),
    ("Wadi_Rum", "desert"),
    ("Death_Valley_National_Park", "desert"),

    # cultural / historical
    ("Kyoto", "cultural"),
    ("Varanasi", "cultural"),
    ("Jerusalem", "cultural"),
    ("Athens", "cultural"),
    ("Prague", "cultural"),
    ("Vienna", "cultural"),
    ("Florence", "cultural"),
    ("Lhasa", "cultural"),
    ("Hoi_An", "cultural"),
    ("Bagan", "cultural"),

    # adventure / off-beat
    ("Antarctica", "offbeat"),
    ("Madagascar", "offbeat"),
    ("Bhutan", "offbeat"),
    ("Mongolia", "offbeat"),
    ("Greenland", "offbeat"),
    ("Galápagos_Islands", "offbeat"),
    ("Iceland", "offbeat"),
    ("Tasmania", "offbeat"),

    # foodie cities
    ("Lyon", "foodie"),
    ("Lima", "foodie"),
    ("Bologna", "foodie"),
    ("San_Sebastián", "foodie"),
    ("New_Orleans", "foodie"),
    ("Penang", "foodie"),
    ("Chengdu", "foodie"),

    # romance / serene
    ("Venice", "romance"),
    ("Bruges", "romance"),
    ("Hokkaido", "serene"),
    ("Lofoten", "serene"),
]


def safe_filename(title: str) -> str:
    """Turn a Wikivoyage title into a sandbox-safe filename."""
    cleaned = title.replace("/", "_").replace("(", "").replace(")", "")
    return f"{cleaned}.pdf"


def download_one(client: httpx.Client, title: str) -> tuple[bool, str]:
    """Download a single destination PDF. Returns (ok, message)."""
    url = f"{BASE_URL}/{title}"
    out_path = OUT_DIR / safe_filename(title)
    if out_path.exists() and out_path.stat().st_size > 10_000:
        return True, f"skip (already present, {out_path.stat().st_size:,} bytes)"
    try:
        r = client.get(url, timeout=60.0, follow_redirects=True)
    except httpx.HTTPError as e:
        return False, f"HTTP error: {e}"
    if r.status_code != 200:
        return False, f"status {r.status_code}: {r.text[:120]}"
    if not r.content.startswith(b"%PDF"):
        return False, f"not a PDF (got {r.content[:20]!r})"
    out_path.write_bytes(r.content)
    return True, f"ok ({len(r.content):,} bytes)"


def main() -> int:
    OUT_DIR.mkdir(parents=True, exist_ok=True)
    headers = {"User-Agent": USER_AGENT, "Accept": "application/pdf"}
    ok, failed = 0, []
    with httpx.Client(headers=headers) as client:
        for i, (title, tag) in enumerate(DESTINATIONS, 1):
            success, msg = download_one(client, title)
            marker = "✓" if success else "✗"
            print(f"[{i:2}/{len(DESTINATIONS)}] {marker} {tag:10}  {title:35} — {msg}")
            if success:
                ok += 1
            else:
                failed.append((title, msg))
            if not msg.startswith("skip"):
                time.sleep(SLEEP_BETWEEN)
    print()
    print(f"downloaded {ok}/{len(DESTINATIONS)} destinations")
    if failed:
        print(f"failures ({len(failed)}):")
        for title, msg in failed:
            print(f"  - {title}: {msg}")
    return 0 if ok >= 50 else 1


if __name__ == "__main__":
    sys.exit(main())
