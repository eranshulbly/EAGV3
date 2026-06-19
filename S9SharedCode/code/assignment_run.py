"""Session 9 assignment — Browser Comparison Agent (driver).

Runs ONE real comparison task end-to-end through the UNMODIFIED Session 8
orchestrator. We only call the public `Executor.run(query)` entry point; we
do not touch flow.py, recovery.py, or any orchestrator internals. The Browser
skill is reached the normal way: the Planner decides to emit a `browser` node
because the query targets a specific site's interactive trending listing.

Task: compare the top 3 trending Python repositories on GitHub *this week*.
This demands real interaction that web_search + fetch_url cannot do:
  - open the Language dropdown and choose Python
  - open the Date-range dropdown and choose "This week"
  - read the resulting filtered list
…i.e. >=3 visible browser actions, landing on the a11y cascade layer.

Usage:
    ../.venv/bin/python assignment_run.py
Then build the replay report:
    ../.venv/bin/python assignment_report.py s9_assignment_gh_trending
"""
from __future__ import annotations

import asyncio
import sys

from flow import Executor

SESSION_ID = "s9_assignment_gh_trending"

QUERY = (
    "Go to the GitHub Trending page (https://github.com/trending) and find the "
    "top 3 trending Python repositories THIS WEEK, using the page's own "
    "Language and Date-range filter dropdowns rather than a pre-filtered URL. "
    "For each of the top 3 repositories, report the repository name, its star "
    "count, and a one-line description. Present the final answer as a "
    "comparison table."
)


async def main() -> int:
    print("=" * 78)
    print("S9 ASSIGNMENT — Browser Comparison Agent")
    print(f"session : {SESSION_ID}")
    print(f"query   : {QUERY}")
    print("=" * 78)

    ex = Executor()
    answer = await ex.run(QUERY, session_id=SESSION_ID)

    print("\n" + "=" * 78)
    print("FINAL ANSWER")
    print("=" * 78)
    print(answer)
    print("\nNext: ../.venv/bin/python assignment_report.py", SESSION_ID)
    return 0


if __name__ == "__main__":
    sys.exit(asyncio.run(main()))
