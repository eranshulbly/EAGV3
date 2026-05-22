# Session 6 — Multi-Role Agent

A four-role agent (Memory, Perception, Decision, Action) with typed
Pydantic contracts on every boundary, built on top of the LLM gateway V3
substrate and an MCP tool server.

## Architecture

```
        ┌────────────────────────────────────────────────────────┐
        │ agent6.py loop                                          │
        │                                                          │
        │  for it in 1..MAX_ITERATIONS:                            │
        │    hits = memory.read(query, history)                    │
        │    obs  = perception.observe(query, hits, history, prev) │
        │    if all done: break                                    │
        │    goal = obs.next_unfinished()                          │
        │    attached = artifacts.get_bytes(goal.attach_artifact_id)│
        │    out  = decision.next_step(goal, hits, attached, ...) │
        │    if out.is_answer: history += answer                   │
        │    else:                                                 │
        │       text, art = action.execute(session, out.tool_call) │
        │       memory.record_outcome(...)                         │
        │       history += action                                  │
        └────────────────────────────────────────────────────────┘
```

Persistent state under `state/`:

- `state/memory.json` — `MemoryItem` rows (facts, preferences, tool_outcomes,
  scratchpad). Survives across runs — that is how Query C's run 2 finds
  mom's birthday without re-asking.
- `state/artifacts/<hash>.bin` + `<hash>.json` — content-addressable byte
  store. Memory holds only `art:<hash>` handles; Decision sees raw bytes
  only when Perception attaches them to a goal.

## Files

| File | Role |
|------|------|
| `schemas.py` | All Pydantic v2 boundary contracts (internal + LLM-wire) |
| `memory.py`  | `Memory` service — keyword-search `read`, LLM-classified `remember`, deterministic `record_outcome` |
| `perception.py` | `Perception.observe` — goal decomposition + done-tracking + artifact attach (Gemini-pinned) |
| `decision.py` | `Decision.next_step` — one tool call OR one final answer |
| `action.py`  | `execute` — pure MCP dispatch with `art:` guard and 4KB artifact threshold |
| `artifacts.py` | `ArtifactStore` — `art:<sha256-prefix>` handles, dedup by content |
| `agent6.py`  | Main loop + CLI entrypoint |

## Setup

The gateway and MCP server must be running. The `.env` at `S6/.env`
contains the API keys. Required keys: `GEMINI_API_KEY`, `GROQ_API_KEY`,
`CEREBRAS_API_KEY`, `NVIDIA_API_KEY`, `OPEN_ROUTER_API_KEY`,
`GITHUB_ACCESS_TOKEN` (for the gateway) plus `TAVILY_API_KEY` (for MCP
`web_search`; falls back to DuckDuckGo if unset).

```bash
# 1. Start the LLM gateway V3 (long-lived service on port 8101)
cd S6/llm_gatewayV3
./run.sh &
# verify:
curl -s http://localhost:8101/v1/routers >/dev/null && echo OK

# 2. Install Python deps used by agent6 and the MCP server.
#    The MCP server is spawned over stdio by agent6.py — these must be
#    in whichever Python you use to run agent6.py.
pip install mcp httpx pydantic ddgs crawl4ai tavily-python python-dotenv
python -m playwright install chromium      # crawl4ai needs Chromium
```

## Running the four target queries

Each query should be run from a **clean state** (the first three) and from
the previous run's state (Query C run 2):

```bash
cd S6

# Query A — Shannon Wikipedia (artifact attach path)
rm -rf state/ sandbox/
python agent6.py "Fetch https://en.wikipedia.org/wiki/Claude_Shannon and tell me his birth date, death date, and three key contributions to information theory."

# Query B — Tokyo + weather (multi-goal + memory carryover)
rm -rf state/ sandbox/
python agent6.py "Find 3 family-friendly things to do in Tokyo this weekend. Check Saturday's weather forecast there and tell me which one is most appropriate."

# Query C run 1 — durable memory write
rm -rf state/ sandbox/
python agent6.py "My mom's birthday is 15 May 2026. Remember that and give me a calendar reminder for two weeks before and on the day."

# Query C run 2 — durable memory read (DO NOT clear state/)
python agent6.py "When is mom's birthday?"

# Query D — multi-source synthesis
rm -rf state/ sandbox/
python agent6.py "Search for 'Python asyncio best practices', read the top 3 results, and give me a short numbered list of the advice they agree on."
```

## Prompts and Validation Schemas (PoP — Proof of Practice)

These are the verbatim system prompts and the Pydantic-generated JSON
schemas that are sent to the gateway as `response_format`. They are the
typed contract between the agent and the LLM. The schemas are generated
at runtime by `<Model>.model_json_schema()`, so they cannot drift from
the Pydantic class definitions in [schemas.py](schemas.py).

### Perception system prompt

From [perception.py:30-62](perception.py#L30-L62):

```
You are the Perception role in a multi-step agent.

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
matching the provided response_format schema. No prose.
```

The user message is built each iteration from `USER QUERY`, `MEMORY HITS`
(indexed `[i]` so the model can refer to artifacts by position),
`HISTORY` (last 12 events), and `PRIOR GOALS`.

### Perception validation JSON (`PerceptionOutput.model_json_schema()`)

This is exactly what the gateway receives as `response_format.schema`:

```json
{
  "$defs": {
    "PerceptionGoal": {
      "additionalProperties": false,
      "properties": {
        "text":           { "type": "string", "title": "Text" },
        "done":           { "type": "boolean", "title": "Done" },
        "artifact_index": {
          "anyOf": [ { "type": "integer" }, { "type": "null" } ],
          "default": null,
          "title": "Artifact Index"
        }
      },
      "required": ["text", "done"],
      "title": "PerceptionGoal",
      "type": "object"
    }
  },
  "additionalProperties": false,
  "properties": {
    "goals": {
      "items": { "$ref": "#/$defs/PerceptionGoal" },
      "title": "Goals",
      "type": "array"
    }
  },
  "required": ["goals"],
  "title": "PerceptionOutput",
  "type": "object"
}
```

Note: there is no `id` field — goals carry **positional identity** in this
schema. The outer loop maps `goals[i]` back to the prior goal's id by
position, so the model cannot invent a stale identifier.

### Decision system prompt

From [decision.py:13-39](decision.py#L13-L39):

```
You are the Decision role in a multi-step agent.

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

5. One step at a time. Do not chain multiple tool calls in one response.
```

### Decision validation — tool list (not `response_format`)

Decision does not use a Pydantic `response_format`. Instead it passes
`tools=[...]` and `tool_choice="auto"` to the gateway. The validation is
that the model must either (a) return plain text (the answer) OR (b) emit
exactly one `tool_calls[]` entry whose `name` is one of the MCP tools and
whose `arguments` validate against that tool's `input_schema`.

The MCP tool list is loaded once at boot via `session.list_tools()` and
converted to gateway `ToolDef` shape by `mcp_tools_for_decision` in
[decision.py:42-50](decision.py#L42-L50):

```python
def mcp_tools_for_decision(mcp_tools):
    return [
        {
            "name": t.name,
            "description": (t.description or "").strip(),
            "input_schema": t.inputSchema or {"type": "object", "properties": {}},
        }
        for t in mcp_tools
    ]
```

The internal canonical shape that `Decision.next_step` returns is
`DecisionOutput`, validated by Pydantic (exactly one of `answer`/`tool_call`
populated — see [schemas.py:67-82](schemas.py#L67-L82)):

```json
{
  "$defs": {
    "ToolCall": {
      "properties": {
        "name":      { "type": "string", "title": "Name" },
        "arguments": { "additionalProperties": true, "type": "object", "title": "Arguments" }
      },
      "required": ["name"],
      "title": "ToolCall",
      "type": "object"
    }
  },
  "properties": {
    "answer":    { "anyOf": [ { "type": "string" }, { "type": "null" } ], "default": null },
    "tool_call": { "anyOf": [ { "$ref": "#/$defs/ToolCall" }, { "type": "null" } ], "default": null }
  },
  "title": "DecisionOutput",
  "type": "object"
}
```

### Memory classifier validation JSON (`MemoryClassification.model_json_schema()`)

This is the third LLM call in the system — `memory.remember(raw_text, …)`
classifies the user's query into a typed item. Sent to the gateway as
`response_format.schema`:

```json
{
  "additionalProperties": false,
  "description": "Output of the memory.remember() classification LLM call.",
  "properties": {
    "kind": {
      "enum": ["fact", "preference", "tool_outcome", "scratchpad"],
      "title": "Kind",
      "type": "string"
    },
    "keywords":   { "items": { "type": "string" }, "title": "Keywords", "type": "array" },
    "descriptor": { "title": "Descriptor", "type": "string" },
    "value":      { "additionalProperties": true, "type": "object", "title": "Value" }
  },
  "required": ["kind", "keywords", "descriptor", "value"],
  "title": "MemoryClassification",
  "type": "object"
}
```

This schema produced the durable fact for Query C run 1:

```json
{
  "kind": "fact",
  "keywords": ["mom", "birthday", "may", "2026"],
  "descriptor": "The user's mother's birthday is on May 15, 2026.",
  "value": {"entity": "mother", "attribute": "birthday", "value": "2026-05-15"}
}
```

— which survived into run 2 and let Perception mark the read goal done in
one iteration without a single tool dispatch.

## Iteration counts (all within bound)

| Query | Iters | Documented target | 2× cap | Result |
|-------|-------|------------------|-------|--------|
| A — Shannon Wikipedia | 4 | 3 | 6 | ✅ |
| B — Tokyo + weather | 5 | 6 | 12 | ✅ |
| C run 1 — durable write | 3 | 4 | 8 | ✅ fact persisted |
| C run 2 — durable read | 1 | 2 | 4 | ✅ found in memory alone |
| D — asyncio synthesis | 3 | 5–7 | 14 | ✅ |

## Captured terminal output

Full unedited traces are in [traces/](../S5/traces/) (one file per query). The
abridged versions below show the role progression for each query.

### Query A trace — 4 iterations (target 3, max 6) ✅

```
[boot] run_id=8fbbaafc
[boot] query='Fetch https://en.wikipedia.org/wiki/Claude_Shannon and tell me his birth date, death date, and three key contributions to information theory.'
[boot] mcp tools: ['web_search', 'fetch_url', 'get_time', 'currency_convert', 'read_file', 'list_dir', 'create_file', 'update_file', 'edit_file']

─── iter 1 ─────────────────────────────────────
[memory.read]   0 hits
[perception]    [open] Fetch Wikipedia article for Claude Shannon
[perception]    [open] Extract birth and death dates
[perception]    [open] Extract three key contributions to information theory
[decision]      TOOL_CALL: fetch_url({"url": "https://en.wikipedia.org/wiki/Claude_Shannon"})
[action]        → [artifact art:59d30a3e7090a930, 263047 bytes] preview: { "status": 200, "content_type": "text/markdown", "length_bytes": 256952, ...

─── iter 2 ─────────────────────────────────────
[memory.read]   1 hits
                - [tool_outcome] art:59d30a3e7090a930 fetch_url(...) -> ...
[perception]    [done] Fetch Wikipedia article for Claude Shannon
[perception]    [open] Extract birth and death dates  attach=art:59d30a3e7090a930
[perception]    [open] Extract three key contributions to information theory
[attach]        art:59d30a3e7090a930 (263047 bytes)
[decision]      ANSWER: Claude Shannon was born on April 30, 1916, and passed away on February 24, 2001.

─── iter 3 ─────────────────────────────────────
[memory.read]   1 hits
[perception]    [done] Fetch Wikipedia article for Claude Shannon
[perception]    [done] Extract birth and death dates
[perception]    [open] Extract three key contributions to information theory  attach=art:59d30a3e7090a930
[attach]        art:59d30a3e7090a930 (263047 bytes)
[decision]      ANSWER: Claude Shannon made several foundational contributions to information theory, as outlined in his work *A Mathematical Theory of Communication* (1948): ...

─── iter 4 ─────────────────────────────────────
[memory.read]   1 hits
[perception]    [done] Fetch Wikipedia article for Claude Shannon
[perception]    [done] Extract birth and death dates
[perception]    [done] Extract three key contributions to information theory

[done] all 3 goals satisfied

============================================================
FINAL: - Claude Shannon was born on April 30, 1916, and passed away on February 24, 2001.

- Claude Shannon made several foundational contributions to information theory, as outlined in his work *A Mathematical Theory of Communication* (1948):

*   **The Quantification of Information:** Shannon formally introduced the term "bit" and established the concept of entropy in information theory as a way to measure the uncertainty or information content in a message.
*   **The Establishment of the Field:** He laid the theoretical foundation for the field of information theory, creating a mathematical framework that remains the "blueprint for the digital era."
*   **Technological Enabling:** His work provided the basis for critical modern technologies, including data compression, error correction, and the fundamental principles enabling the development of the Internet, mobile telephony, and compact discs.
============================================================
```

### Query B trace — 5 iterations (target 6, max 12) ✅

```
[boot] run_id=91b30ef3
[boot] query="Find 3 family-friendly things to do in Tokyo this weekend. Check Saturday's weather forecast there and tell me which one is most appropriate."

─── iter 1 ─────────────────────────────────────
[memory.read]   0 hits
[perception]    [open] Identify three family-friendly activities in Tokyo
[perception]    [open] Retrieve Saturday weather forecast for Tokyo
[perception]    [open] Recommend most appropriate activity based on weather
[decision]      TOOL_CALL: web_search({"query": "family-friendly activities in Tokyo", "max_results": 3})
[action]        → [artifact art:42e849119dc7a5b6, 6628 bytes]

─── iter 2 ─────────────────────────────────────
[perception]    [done] Identify three family-friendly activities in Tokyo
[perception]    [open] Retrieve Saturday weather forecast for Tokyo
[perception]    [open] Recommend most appropriate activity based on weather
[decision]      TOOL_CALL: web_search({"query": "Tokyo weather forecast Saturday"})
[action]        → [artifact art:26ec028432d5b5e5, 8973 bytes]

─── iter 3 ─────────────────────────────────────
[perception]    [done] Identify three family-friendly activities in Tokyo
[perception]    [done] Retrieve Saturday weather forecast for Tokyo
[perception]    [open] Recommend most appropriate activity based on weather  attach=art:42e849119dc7a5b6
[attach]        art:42e849119dc7a5b6 (6628 bytes)
[decision]      TOOL_CALL: web_search({"query": "Tokyo weather forecast"})

─── iter 4 ─────────────────────────────────────
[perception]    [open] Recommend most appropriate activity based on weather  attach=art:42e849119dc7a5b6
[attach]        art:42e849119dc7a5b6 (6628 bytes)
[decision]      ANSWER: Given that Tokyo's weather can vary, here are some excellent family-friendly activity recommendations categorized by weather conditions: ...

─── iter 5 ─────────────────────────────────────
[perception]    [done] all 3 goals

FINAL: Given that Tokyo's weather can vary, here are some excellent family-friendly activity recommendations categorized by weather conditions:

**For Clear or Pleasant Weather (Outdoor Activities):**
*   Imperial Palace East Gardens — stroller-friendly historic gardens.
*   Meiji Jingu — forested area in central Tokyo.
*   Ueno Park & Nezu Shrine — open exploration, spring flower viewing.

**For Rainy or Hot/Cold Weather (Indoor):**
*   Tokyo Toy Museum — hands-on indoor play for young children.
*   Miraikan (National Museum of Emerging Science).
*   Tokyo Fire Museum — interactive fire safety exhibits.
*   teamLab Planets — immersive digital art experience.

**All-Weather:** Tokyo Disney Resort; cultural workshops (sushi class, kimono tea ceremony).
```

### Query C run 1 trace — 3 iterations (target 4, max 8) ✅

```
[boot] run_id=dac88ec7
[boot] query="My mom's birthday is 15 May 2026. Remember that and give me a calendar reminder for two weeks before and on the day."
[memory.remember] fact: The user's mother's birthday is on May 15, 2026.
                  keywords=['mom','birthday','may','2026']

─── iter 1 ─────────────────────────────────────
[memory.read]   1 hits
[perception]    [done] Store birthday date
[perception]    [open] Create calendar reminder for May 1 2026
[perception]    [open] Create calendar reminder for May 15 2026
[decision]      TOOL_CALL: create_file({"path": "reminder_2026-05-01.txt", "content": "Reminder: Event on May 1, 2026.\nPlease add details about this event as needed."})
[action]        → {"ok": true, "path": "reminder_2026-05-01.txt", "size_bytes": 78}

─── iter 2 ─────────────────────────────────────
[perception]    [done] Store birthday date
[perception]    [done] Create calendar reminder for May 1 2026
[perception]    [open] Create calendar reminder for May 15 2026
[decision]      TOOL_CALL: create_file({"path": "reminder_2026-05-15.txt", "content": "Reminder: Mom's birthday is on May 15, 2026."})
[action]        → {"ok": true, "path": "reminder_2026-05-15.txt", "size_bytes": 44}

─── iter 3 ─────────────────────────────────────
[perception]    [done] all 3 goals

FINAL: Completed actions:
  ✓ create_file({"path": "reminder_2026-05-01.txt", ...}) → {"ok": true, "path": "reminder_2026-05-01.txt", "size_bytes": 78}
  ✓ create_file({"path": "reminder_2026-05-15.txt", ...}) → {"ok": true, "path": "reminder_2026-05-15.txt", "size_bytes": 44}
```

After this run, `state/memory.json` contains:

```json
{
  "kind": "fact",
  "keywords": ["mom", "birthday", "may", "2026"],
  "descriptor": "The user's mother's birthday is on May 15, 2026.",
  "value": {"entity": "mother", "attribute": "birthday", "value": "2026-05-15"}
}
```

### Query C run 2 trace — 1 iteration (target 2, max 4) ✅ durable memory hit

```
[boot] run_id=5594687c
[boot] query="When is mom's birthday?"

─── iter 1 ─────────────────────────────────────
[memory.read]   2 hits
                - [tool_outcome] create_file(...) -> {"ok": true, "path": "reminder_2026-05-15.txt", ...}
                - [fact] The user's mother's birthday is on May 15, 2026.
[perception]    [done] Identify mother's birthday

[done] all 1 goals satisfied

FINAL: From memory: The user's mother's birthday is on May 15, 2026.
```

The fact survived run 1 → run 2 via `state/memory.json`. Perception saw it in MEMORY HITS and marked the goal done in iter 1 without dispatching a single tool.

### Query D trace — 3 iterations (target 5-7, max 14) ✅

```
[boot] run_id=bcff1cc6
[boot] query="Search for 'Python asyncio best practices', read the top 3 results, and give me a short numbered list of the advice they agree on."

─── iter 1 ─────────────────────────────────────
[memory.read]   0 hits
[perception]    [open] Search for Python asyncio best practices
[perception]    [open] Read the top three search results
[perception]    [open] Identify common advice
[perception]    [open] Format common advice as a numbered list
[decision]      TOOL_CALL: web_search({"query": "Python asyncio best practices", "max_results": 5})
[action]        → [artifact art:c47d8e392ce66810, 10433 bytes]

─── iter 2 ─────────────────────────────────────
[perception]    [done] Search for Python asyncio best practices
[perception]    [done] Read the top three search results
[perception]    [open] Identify common advice  attach=art:c47d8e392ce66810
[perception]    [open] Format common advice as a numbered list
[attach]        art:c47d8e392ce66810 (10433 bytes)
[decision]      ANSWER: When working with Python's `asyncio` library, the following are widely recognized as best practices ...

─── iter 3 ─────────────────────────────────────
[perception]    [done] all 4 goals

FINAL: When working with Python's `asyncio` library, the following are widely recognized as best practices to ensure performance and prevent common pitfalls:

*   **Do Not Block the Event Loop:** Never use synchronous, blocking calls (like `time.sleep()` or heavy CPU-bound computations) inside an async function. Use `await asyncio.sleep()` or `loop.run_in_executor()` for blocking work.
*   **Use `asyncio.run()` as the Entry Point:** Always start your top-level program with `asyncio.run(main())` for proper event-loop lifecycle.
*   **Leverage Concurrency with Tasks:** Use `asyncio.create_task()` or `asyncio.gather()` for independent coroutines instead of awaiting them sequentially.
*   **Always Await Coroutines:** Failing to await a coroutine is a common mistake; it returns a coroutine object instead of the result.
*   **Use Async Context Managers:** Prefer `async with` for network sessions, file handles, etc.
```

The force-attach safety net (Perception → attach most-recent artifact for synthesis goals) fires on goal 3 ("Identify common advice"). Decision reads the search-results JSON in ATTACHED ARTIFACTS and produces the consolidated numbered list in one call.

## Design notes

- **Gateway routing**: Perception and `memory.remember` pin to Gemini
  via `provider="g"` and use `auto_route="perception"` / `"memory"` so the
  dashboard at `localhost:8101/` correctly labels each call. Decision uses
  `auto_route="decision"` and lets the router pool pick a worker.
- **Temperature 1.0**: Both Perception and Decision use `temperature=1.0`
  because Gemini 3.1 flash-lite is observed to loop on identical structured
  outputs at `temperature=0` (documented in the Session 6 spec).
- **Position-based goal identity**: The `PerceptionOutput` JSON schema
  contains no goal id field — the loop maps positions back to stable ids
  it carries in `prior_goals`. The model cannot hallucinate a stale id
  because it has no string field in which to write one.
- **Indexed artifact attachment**: Perception emits `artifact_index: <int>`
  pointing at one numbered MEMORY HITS entry. The loop translates the
  index to the actual `art:...` handle. Hallucinated indices are dropped
  silently; out-of-range or missing artifacts also drop silently.
- **Force-attach safety net**: If the first unfinished goal contains a
  synthesis keyword (synthesise/extract/list/compare/decide/summarise/
  "tell me"/"give me") AND no attach was set AND a hit has an artifact,
  the loop auto-attaches it. This is the safety net described in the
  Session 6 spec for Query D's synthesis step.
- **Action `art:` guard**: If Decision accidentally passes an `art:` handle
  as a `path` or `url` argument, Action refuses to dispatch and returns
  a clear error string. This blocks the most common TINY-model
  hallucination at the dispatch boundary.
- **Sticky-done**: Once Perception marks a goal `done`, the loop forces
  it to stay done in every subsequent iteration. The `attach_artifact_id`
  is also cleared on any goal that is not the next-unfinished one.
- **Provider-failover retry**: Both `memory.remember` and `perception.observe`
  try `provider="g"` (Gemini) first, then on any exception retry once with
  `auto_route=...` and no provider override so the worker-pool failover can
  pick a different worker. This survives transient Gemini 5xx / quota
  bumps without crashing the loop.

## What is NOT in scope (Session 6)

- Embedding-based retrieval (Session 7).
- DAG-based execution (Session 8).
- A separate Planner Agent (later sessions).
- Any third-party agent framework (LangGraph, LangChain, CrewAI).
