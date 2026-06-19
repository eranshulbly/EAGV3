"""Direct BrowserSkill smoke test against GitHub Trending.

De-risks the assignment run: confirms the cascade drives the live page,
emits >=3 real actions, and writes per-turn screenshots — independent of
the planner/distiller/formatter LLM variability the full run adds.
"""
from __future__ import annotations

import asyncio
import json
import time
from pathlib import Path

from browser.skill import BrowserSkill
from schemas import NodeSpec

URL = "https://github.com/trending"
GOAL = (
    "On the GitHub Trending page, use the page's own dropdown menus to filter "
    "the list: first open the 'Spoken Language' / programming-language dropdown "
    "(labelled like 'Language' or 'Any') and choose Python; then open the "
    "date-range dropdown (labelled like 'Today' or 'Date range') and choose "
    "'This week'. After both filters are applied, read the repository names, "
    "star counts, and short descriptions of the top 3 repositories shown. "
    "Remember: a dropdown trigger must be the only action in its turn."
)

OUT = Path(__file__).resolve().parent / "out" / "smoke_gh_trending"


async def main() -> int:
    OUT.mkdir(parents=True, exist_ok=True)
    sk = BrowserSkill(
        agent_tag="browser",
        a11y_provider_pin="gemini",
        artifacts_root=str(OUT),
        session="s9_smoke_gh",
        max_steps_a11y=14,
        wall_clock_s=120,
    )
    t0 = time.time()
    res = await sk.run(NodeSpec(skill="browser", inputs=[],
                                metadata={"url": URL, "goal": GOAL}))
    out = res.output
    print("\n=== smoke result ===")
    print(f"success    {res.success}")
    print(f"path       {out.get('path')}")
    print(f"turns      {out.get('turns')}")
    print(f"final_url  {out.get('final_url')}")
    print(f"error_code {res.error_code}")
    print(f"elapsed    {round(time.time()-t0,1)}s")
    print(f"actions    {len(out.get('actions') or [])} turn-records")
    for a in (out.get("actions") or []):
        acts = ", ".join(f"{x.get('type')}({x.get('mark') or x.get('value','')})"
                         for x in a.get("actions", []))
        print(f"  turn {a.get('turn')}: {acts}  -> {a.get('outcome')}")
    content = (out.get("content") or "")[:600]
    print(f"\ncontent[:600]:\n{content}")
    (OUT / "smoke_result.json").write_text(json.dumps(out, indent=2))
    return 0 if res.success else 1


if __name__ == "__main__":
    raise SystemExit(asyncio.run(main()))
