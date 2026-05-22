"""Decision — one LLM call returning EITHER one MCP tool call OR a final answer."""
import json
from typing import Any

from gateway_client import LLM
from schemas import DecisionOutput, Goal, MemoryItem, ToolCall


SYSTEM = """You are the Decision role in a multi-step agent.

You receive ONE bounded GOAL plus relevant memory hits and recent history.
Your job is to take ONE step toward that goal: EITHER produce a final
answer for this goal OR call exactly ONE tool. Never do both. Never call
two tools. Never narrate or explain.

RULES:

1. If the goal can already be answered from the GOAL text + MEMORY HITS +
   RECENT HISTORY + ATTACHED ARTIFACTS, reply with a plain-text answer.
   No tool call.
   - For extraction / list / compare / decide / synthesise goals, the
     answer MUST be substantive: at least three sentences OR a numbered/
     bulleted list of items. Do not respond with a meta-statement like
     "the page has been fetched". Read the ATTACHED ARTIFACTS bytes and
     produce the actual content the goal asks for.

2. Otherwise, call exactly ONE tool from the available tools. Use realistic
   arguments. For URLs and file paths, use real strings.

3. Strings of the form 'art:<hex>' are INTERNAL artifact handles. They
   reference the agent's content-addressable store. NEVER pass an 'art:'
   string as a `path`, `url`, or any other tool argument. The bytes you
   need are already provided in the ATTACHED ARTIFACTS section when
   relevant. Tools take real file paths and real URLs only.

4. For file tools (read_file, list_dir, create_file, update_file, edit_file),
   paths are relative to a small sandbox. The sandbox does NOT auto-create
   parent directories — use FLAT filenames such as
   'mom_birthday_reminder.txt' or 'reminders_2026-05-01.txt'. Do NOT include
   a subdirectory prefix like 'reminders/...' unless `list_dir` has confirmed
   that subdirectory exists.

5. One step at a time. Do not chain multiple tool calls in one response."""


def mcp_tools_for_decision(mcp_tools: list[Any]) -> list[dict]:
    """Convert FastMCP tool listings into the gateway's ToolDef shape."""
    out = []
    for t in mcp_tools:
        out.append({
            "name": t.name,
            "description": (t.description or "").strip(),
            "input_schema": t.inputSchema or {"type": "object", "properties": {}},
        })
    return out


def _render_hits(hits: list[MemoryItem]) -> str:
    if not hits:
        return "  (none)"
    lines = []
    for h in hits:
        marker = f" artifact={h.artifact_id}" if h.artifact_id else ""
        lines.append(f"  - [{h.kind}]{marker} :: {h.descriptor[:200]}")
    return "\n".join(lines)


def _render_history(history: list[dict]) -> str:
    if not history:
        return "  (none yet)"
    lines = []
    for ev in history[-6:]:
        if ev["kind"] == "action":
            args = json.dumps(ev.get("arguments", {}))[:120]
            lines.append(
                f"  iter {ev['iter']}: ACTION tool={ev['tool']} args={args} "
                f"result={ev.get('result_descriptor','')[:160]}"
            )
        elif ev["kind"] == "answer":
            lines.append(
                f"  iter {ev['iter']}: ANSWER {ev.get('text','')[:160]}"
            )
    return "\n".join(lines)


def _render_attached(attached: list[tuple[str, bytes]]) -> str:
    if not attached:
        return ""
    lines = ["", "ATTACHED ARTIFACTS:"]
    for handle, blob in attached:
        try:
            text = blob.decode("utf-8", errors="replace")
        except Exception:
            text = repr(blob[:200])
        # Cap each artifact at 40KB into the prompt — Gemini handles much
        # more but we don't need the bottom of a 250KB page.
        MAX = 40_000
        if len(text) > MAX:
            text = text[:MAX] + f"\n... [TRUNCATED, original {len(text)} chars]"
        lines.append(f"--- {handle} ---")
        lines.append(text)
        lines.append(f"--- end {handle} ---")
    return "\n".join(lines)


class Decision:
    def __init__(self):
        self._llm = LLM()

    def next_step(
        self,
        goal: Goal,
        hits: list[MemoryItem],
        attached: list[tuple[str, bytes]],
        history: list[dict],
        mcp_tools: list[dict],
    ) -> DecisionOutput:
        user_msg = (
            f"GOAL:\n  {goal.text}\n\n"
            f"MEMORY HITS:\n{_render_hits(hits)}\n\n"
            f"RECENT HISTORY:\n{_render_history(history)}"
            f"{_render_attached(attached)}\n\n"
            "Reply with EITHER a single tool call OR a plain-text final answer "
            "for this goal."
        )

        # Try auto-routed first; on transient gateway failure (5xx, connection
        # blip), retry once pinned to Gemini so we don't crash the loop on a
        # single bad worker.
        result = None
        for attempt in ("auto_route", "provider_g"):
            kwargs = dict(
                messages=[{"role": "user", "content": user_msg}],
                system=SYSTEM,
                temperature=1.0,
                max_tokens=1500,
                tools=mcp_tools,
                tool_choice="auto",
            )
            if attempt == "auto_route":
                kwargs["auto_route"] = "decision"
            else:
                kwargs["provider"] = "g"
            try:
                result = self._llm.chat(**kwargs)
                break
            except Exception as e:
                import sys
                print(
                    f"[decision] attempt={attempt} failed: {e}",
                    file=sys.stderr,
                )
                continue

        if result is None:
            return DecisionOutput(
                answer="(decision LLM call failed on all attempts; check gateway "
                       "and rerun — this goal cannot be progressed)",
            )

        tcs = result.get("tool_calls") or []
        if tcs:
            tc = tcs[0]
            args = tc.get("arguments", {}) or {}
            if isinstance(args, str):
                try:
                    args = json.loads(args)
                except Exception:
                    args = {"_raw": args}
            return DecisionOutput(
                tool_call=ToolCall(name=tc["name"], arguments=args),
            )

        text = (result.get("text") or "").strip()
        if not text:
            # Defensive — surface as an answer error so the loop can continue
            text = "(decision returned empty output; treating as completed for this goal)"
        return DecisionOutput(answer=text)
