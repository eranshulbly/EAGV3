"""Session 6 multi-role agent.

Loop: Memory -> Perception -> (attach) -> Decision -> Action.
Each role has typed Pydantic contracts at its boundary (schemas.py).
"""
import asyncio
import json
import sys
import uuid
from contextlib import asynccontextmanager
from pathlib import Path

import httpx
from mcp import ClientSession, StdioServerParameters
from mcp.client.stdio import stdio_client

import action
from artifacts import ArtifactStore
from decision import Decision, mcp_tools_for_decision
from memory import Memory
from perception import Perception
from schemas import Goal


MAX_ITERATIONS = 20
GATEWAY_URL = "http://localhost:8101"


def ensure_gateway() -> None:
    try:
        r = httpx.get(f"{GATEWAY_URL}/v1/routers", timeout=3)
        r.raise_for_status()
    except Exception as e:
        raise RuntimeError(
            f"LLM gateway V3 not reachable at {GATEWAY_URL} ({e}).\n"
            f"Start it with: cd llm_gatewayV3 && ./run.sh"
        ) from None


@asynccontextmanager
async def mcp_session():
    server_params = StdioServerParameters(
        command=sys.executable,
        args=[str(Path(__file__).with_name("mcp_server.py"))],
    )
    async with stdio_client(server_params) as (read, write):
        async with ClientSession(read, write) as session:
            await session.initialize()
            yield session


# ---------- pretty per-iteration trace ----------

def _hr(it: int) -> None:
    print(f"\n─── iter {it} ─────────────────────────────────────")


def _print_hits(hits) -> None:
    print(f"[memory.read]   {len(hits)} hits")
    for h in hits[:5]:
        art = f" {h.artifact_id}" if h.artifact_id else ""
        print(f"                - [{h.kind}]{art} {h.descriptor[:100]}")


def _print_obs(obs) -> None:
    for g in obs.goals:
        status = "done" if g.done else "open"
        attach = f"  attach={g.attach_artifact_id}" if g.attach_artifact_id else ""
        print(f"[perception]    [{status}] {g.text}{attach}")


def _print_decision(out) -> None:
    if out.is_answer:
        snippet = out.answer.replace("\n", " ")
        if len(snippet) > 200:
            snippet = snippet[:200] + "..."
        print(f"[decision]      ANSWER: {snippet}")
    else:
        args = json.dumps(out.tool_call.arguments)[:200]
        print(f"[decision]      TOOL_CALL: {out.tool_call.name}({args})")


def _print_action(result_text, art_id) -> None:
    snippet = result_text.replace("\n", " ")
    if len(snippet) > 200:
        snippet = snippet[:200] + "..."
    print(f"[action]        → {snippet}")


# ---------- main loop ----------

def _final_answer_from(history: list[dict], memory, query: str) -> str:
    answers = [ev for ev in history if ev["kind"] == "answer"]
    if answers:
        if len(answers) == 1:
            return answers[0]["text"]
        return "\n\n".join(f"- {a['text']}" for a in answers)
    # Action-only flow (e.g. Query C run 1 — create reminder files). Summarise
    # the successful tool calls so the FINAL line is informative.
    actions = [ev for ev in history if ev["kind"] == "action"]
    if actions:
        lines = ["Completed actions:"]
        for ev in actions:
            desc = ev.get("result_descriptor", "")[:140]
            ok = "ERROR" not in desc
            marker = "✓" if ok else "✗"
            lines.append(f"  {marker} {ev['tool']}({json.dumps(ev.get('arguments', {}))[:80]}) → {desc}")
        return "\n".join(lines)
    # Memory-only flow (e.g. Query C run 2). Perception saw the answer in
    # MEMORY HITS and marked the goal done immediately without dispatching.
    # Surface the most relevant fact/preference so the user sees the answer.
    hits = memory.read(query, history=[])
    for h in hits:
        if h.kind in ("fact", "preference"):
            return f"From memory: {h.descriptor}"
    if hits:
        return f"From memory: {hits[0].descriptor}"
    return "(no actions and no answer; check iteration trace)"


async def run(query: str) -> str:
    ensure_gateway()

    artifacts = ArtifactStore(Path("state/artifacts"))
    memory = Memory(Path("state/memory.json"))
    perception = Perception(artifacts)
    decision = Decision()

    run_id = uuid.uuid4().hex[:8]
    history: list[dict] = []
    prior_goals: list[Goal] = []

    print(f"[boot] run_id={run_id}")
    print(f"[boot] query={query!r}")

    # Durable memory: classify the user's query so facts/preferences in it
    # survive into future runs (Query C run 2 depends on this).
    remembered = memory.remember(query, source="user_query", run_id=run_id)
    if remembered:
        print(f"[memory.remember] {remembered.kind}: {remembered.descriptor}")
        print(f"                  keywords={remembered.keywords}")

    async with mcp_session() as session:
        mcp_tools = (await session.list_tools()).tools
        tool_defs = mcp_tools_for_decision(mcp_tools)
        print(f"[boot] mcp tools: {[t['name'] for t in tool_defs]}")

        for it in range(1, MAX_ITERATIONS + 1):
            _hr(it)

            hits = memory.read(query, history)
            _print_hits(hits)

            obs = perception.observe(query, hits, history, prior_goals, run_id)
            prior_goals = obs.goals
            _print_obs(obs)

            if obs.all_done:
                print(f"\n[done] all {len(obs.goals)} goals satisfied")
                break

            goal = obs.next_unfinished()
            if goal is None:
                print("[done] no unfinished goals")
                break

            attached: list[tuple[str, bytes]] = []
            if goal.attach_artifact_id and artifacts.exists(goal.attach_artifact_id):
                blob = artifacts.get_bytes(goal.attach_artifact_id)
                attached.append((goal.attach_artifact_id, blob))
                print(f"[attach]        {goal.attach_artifact_id} ({len(blob)} bytes)")

            out = decision.next_step(goal, hits, attached, history, tool_defs)
            _print_decision(out)

            if out.is_answer:
                history.append({
                    "iter": it,
                    "kind": "answer",
                    "goal_id": goal.id,
                    "text": out.answer,
                })
                continue

            result_text, art_id = await action.execute(session, out.tool_call, artifacts)
            _print_action(result_text, art_id)
            memory.record_outcome(
                tool_call=out.tool_call,
                result_text=result_text,
                artifact_id=art_id,
                run_id=run_id,
                goal_id=goal.id,
            )
            history.append({
                "iter": it,
                "kind": "action",
                "goal_id": goal.id,
                "tool": out.tool_call.name,
                "arguments": out.tool_call.arguments,
                "result_descriptor": result_text[:300],
                "artifact_id": art_id,
            })

        else:
            print(f"\n[stop] hit MAX_ITERATIONS ({MAX_ITERATIONS}) without all goals done")

    answer = _final_answer_from(history, memory, query)
    print("\n" + "=" * 60)
    print(f"FINAL: {answer}")
    print("=" * 60)
    return answer


def main() -> None:
    if len(sys.argv) < 2:
        print('Usage: python agent6.py "<query>"', file=sys.stderr)
        sys.exit(2)
    query = " ".join(sys.argv[1:])
    asyncio.run(run(query))


if __name__ == "__main__":
    main()
