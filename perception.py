"""Perception — orchestrator role. One LLM call per iteration, pinned to Gemini.

Outputs Observation (typed Goal list) by:
  1. Decomposing query into bounded goals on first call.
  2. Marking goals done when history shows a satisfying action.
  3. Attaching one artifact to the next unfinished goal when it needs bytes.
  4. Preserving goal order/position across iterations (positional identity).
"""
import json
import sys
import uuid

from gateway_client import LLM
from artifacts import ArtifactStore
from schemas import (
    Goal,
    MemoryItem,
    Observation,
    PerceptionOutput,
)


SYSTEM = """You are the Perception role in a multi-step agent.

Your single job each iteration is to maintain a list of bounded GOALS that
satisfy the user's query, and decide whether the next unfinished goal needs
the bytes of a previously fetched artifact.

OBLIGATIONS (follow strictly):

1. If PRIOR GOALS is empty, decompose USER QUERY into one or more goals.
   Each goal text is a short imperative statement (one verb, one object).
   Keep the goal list MINIMAL: prefer 1-4 goals. Do not duplicate goals.

2. If PRIOR GOALS is non-empty, you MUST preserve its order and length:
   - Emit exactly the same number of goals, in the same order.
   - Each goal text should remain semantically the same as the prior one.
   - Update only the `done` flag and `artifact_index`.

3. Mark a goal `done: true` the moment HISTORY contains an action whose
   result satisfies that goal. A done goal STAYS done in every subsequent
   iteration; never flip done back to false.

4. For the FIRST unfinished goal only, decide if it needs the raw bytes of
   a previously fetched artifact (e.g. extracting facts from a fetched web
   page). If so, set `artifact_index` to the integer index of one MEMORY
   HITS entry that is marked "(has artifact)". Otherwise set it to null.
   Never invent indexes — only choose from indexes you actually see.

5. NEVER ask the user for clarification. NEVER add goals like "ask the user".
   Make reasonable assumptions instead.

Output: a JSON object {"goals": [{"text", "done", "artifact_index"}, ...]}
matching the provided response_format schema. No prose."""


SYNTHESIS_KEYWORDS = {
    "synthes", "extract", "list", "compare", "decide", "choose",
    "summar", "tell me", "give me",
}


def _render_hits(hits: list[MemoryItem]) -> tuple[str, list[str]]:
    """Render MEMORY HITS section and return (text, mapping_of_idx_to_artifact_handle).

    artifact_handles[i] is the art:... handle for index i, or None if that
    hit carries no artifact. The model is told to pick from the indexed list."""
    lines = []
    handles: list[str | None] = []
    for i, h in enumerate(hits):
        marker = "(has artifact)" if h.artifact_id else ""
        # keep the line short — Perception only needs to recognise what's there
        lines.append(f"  [{i}] kind={h.kind} {marker} :: {h.descriptor[:200]}")
        handles.append(h.artifact_id)
    if not lines:
        lines = ["  (none)"]
    return "\n".join(lines), handles


def _render_history(history: list[dict]) -> str:
    if not history:
        return "  (none yet)"
    lines = []
    for ev in history[-12:]:
        if ev["kind"] == "action":
            args = json.dumps(ev.get("arguments", {}))[:160]
            lines.append(
                f"  iter {ev['iter']}: ACTION goal={ev.get('goal_id')} "
                f"tool={ev['tool']} args={args} "
                f"result={ev.get('result_descriptor','')[:160]}"
            )
        elif ev["kind"] == "answer":
            lines.append(
                f"  iter {ev['iter']}: ANSWER goal={ev.get('goal_id')} "
                f"text={ev.get('text','')[:200]}"
            )
        else:
            lines.append(f"  iter {ev['iter']}: {ev}")
    return "\n".join(lines)


def _render_prior_goals(prior: list[Goal]) -> str:
    if not prior:
        return "  (empty — this is iteration 1; decompose the query)"
    lines = []
    for i, g in enumerate(prior):
        attach = f" attach={g.attach_artifact_id}" if g.attach_artifact_id else ""
        status = "done" if g.done else "open"
        lines.append(f"  [{i}] [{status}] {g.text}{attach}")
    return "\n".join(lines)


def _force_attach_if_synthesis(
    goals: list[Goal],
    hits: list[MemoryItem],
    artifacts: ArtifactStore,
) -> None:
    """Safety net: synthesis goals usually need bytes. If the first unfinished
    goal looks like a synthesis goal and Perception didn't attach anything,
    auto-attach the most recent valid artifact in hits."""
    nxt = None
    for g in goals:
        if not g.done:
            nxt = g
            break
    if nxt is None or nxt.attach_artifact_id is not None:
        return
    text_l = nxt.text.lower()
    if not any(k in text_l for k in SYNTHESIS_KEYWORDS):
        return
    for h in hits:
        if h.artifact_id and artifacts.exists(h.artifact_id):
            nxt.attach_artifact_id = h.artifact_id
            return


class Perception:
    def __init__(self, artifacts: ArtifactStore):
        self.artifacts = artifacts
        self._llm = LLM()

    def observe(
        self,
        query: str,
        hits: list[MemoryItem],
        history: list[dict],
        prior_goals: list[Goal],
        run_id: str,
    ) -> Observation:
        hits_text, idx_to_handle = _render_hits(hits)
        history_text = _render_history(history)
        prior_text = _render_prior_goals(prior_goals)

        user_msg = (
            f"USER QUERY:\n  {query}\n\n"
            f"MEMORY HITS:\n{hits_text}\n\n"
            f"HISTORY:\n{history_text}\n\n"
            f"PRIOR GOALS:\n{prior_text}\n\n"
            "Emit the updated goal list now."
        )

        parsed = None
        # Try Gemini first (the spec pins Perception to Gemini for reliability).
        # On 5xx / transient failures, retry once via auto_route so the
        # worker-pool failover can pick a different provider.
        for attempt in ("provider_g", "auto_only"):
            kwargs = dict(
                messages=[{"role": "user", "content": user_msg}],
                system=SYSTEM,
                temperature=1.0,
                max_tokens=900,
                response_format={
                    "type": "json_schema",
                    "schema": PerceptionOutput.model_json_schema(),
                    "name": "observation",
                    "strict": True,
                },
            )
            if attempt == "provider_g":
                kwargs["provider"] = "g"
            else:
                kwargs["auto_route"] = "perception"
            try:
                result = self._llm.chat(**kwargs)
                parsed = result.get("parsed")
                if parsed:
                    break
            except Exception as e:
                print(
                    f"[perception] attempt={attempt} failed: {e}",
                    file=sys.stderr,
                )
                continue

        if not parsed:
            # Fall back to keeping the prior goal list unchanged — never
            # crash the loop on a single perception failure.
            print(
                "[perception] all attempts failed; reusing prior goals",
                file=sys.stderr,
            )
            return Observation(goals=list(prior_goals))

        wire = PerceptionOutput.model_validate(parsed)

        # Translate wire -> internal Observation
        goals: list[Goal] = []
        for i, pg in enumerate(wire.goals):
            # Positional identity: reuse prior id at this position; mint new
            # if the model added a goal beyond prior length.
            if i < len(prior_goals):
                gid = prior_goals[i].id
                # Sticky-done — once true, always true.
                done = prior_goals[i].done or pg.done
            else:
                gid = f"g_{run_id}_{i}_{uuid.uuid4().hex[:4]}"
                done = pg.done

            # Map artifact_index -> art: handle (only for the first unfinished goal)
            attach: str | None = None
            if pg.artifact_index is not None and 0 <= pg.artifact_index < len(idx_to_handle):
                cand = idx_to_handle[pg.artifact_index]
                if cand and self.artifacts.exists(cand):
                    attach = cand

            goals.append(Goal(id=gid, text=pg.text, done=done, attach_artifact_id=attach))

        # If the LLM dropped or shortened the prior list, restore the dropped
        # tail in their original state (preserve order obligation).
        if prior_goals and len(goals) < len(prior_goals):
            for j in range(len(goals), len(prior_goals)):
                goals.append(prior_goals[j])

        # Clear attaches on already-done goals; only the next unfinished goal
        # should carry one.
        seen_unfinished = False
        for g in goals:
            if g.done:
                g.attach_artifact_id = None
            else:
                if seen_unfinished:
                    g.attach_artifact_id = None
                seen_unfinished = True

        _force_attach_if_synthesis(goals, hits, self.artifacts)
        return Observation(goals=goals)
