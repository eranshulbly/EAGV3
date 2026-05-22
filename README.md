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

- `state/memory.json` — `MemoryItem` rows (`fact` / `preference` / `tool_outcome` / `scratchpad`). Survives across runs.
- `state/artifacts/<hash>.bin` + `<hash>.json` — content-addressable byte store. Memory holds only `art:<hash>` handles; Decision sees raw bytes only when Perception attaches them to a goal.

## Files

| File | Role |
|------|------|
| `schemas.py` | All Pydantic v2 boundary contracts (internal + LLM-wire) |
| `memory.py`  | `Memory` service — keyword-search `read`, LLM-classified `remember`, deterministic `record_outcome` |
| `perception.py` | `Perception.observe` — goal decomposition + done-tracking + artifact attach (Gemini-pinned, retry+fallback) |
| `decision.py` | `Decision.next_step` — one tool call OR one final answer (retry+fallback on 5xx) |
| `action.py`  | `execute` — pure MCP dispatch with `art:` guard and 4KB artifact threshold |
| `artifacts.py` | `ArtifactStore` — `art:<sha256-prefix>` handles, dedup by content |
| `agent6.py`  | Main loop + CLI entrypoint |
| `gateway_client.py` | Loads `LLM` from `llm_gatewayV3/client.py` without sys.path pollution |
| `mcp_server.py` | 9 MCP tools over stdio: web_search, fetch_url, get_time, currency_convert, read_file, list_dir, create_file, update_file, edit_file |
| `llm_gatewayV3/` | FastAPI gateway with `auto_route`, `provider="g"`, `response_format`, and `tools` translation across 7 providers |

## Setup

```bash
# 1. Start the LLM gateway V3 (long-lived service on port 8101)
cd S6/llm_gatewayV3
./run.sh &
curl -s http://localhost:8101/v1/routers >/dev/null && echo OK

# 2. Install Python deps used by agent6 + the MCP server
pip install mcp httpx pydantic ddgs crawl4ai tavily-python python-dotenv
python -m playwright install chromium      # crawl4ai needs Chromium
```

Required `.env` keys at `S6/.env`: `GEMINI_API_KEY`, `GROQ_API_KEY`, `CEREBRAS_API_KEY`, `NVIDIA_API_KEY`, `OPEN_ROUTER_API_KEY`, `GITHUB_ACCESS_TOKEN`, `TAVILY_API_KEY` (last one optional — falls back to DuckDuckGo).

## Running the four target queries

```bash
cd S6

# A — Shannon Wikipedia
rm -rf state/ sandbox/
python -u agent6.py "Fetch https://en.wikipedia.org/wiki/Claude_Shannon and tell me his birth date, death date, and three key contributions to information theory."

# B — Tokyo + weather
rm -rf state/ sandbox/
python -u agent6.py "Find 3 family-friendly things to do in Tokyo this weekend. Check Saturday's weather forecast there and tell me which one is most appropriate."

# C run 1 — durable memory write
rm -rf state/ sandbox/
python -u agent6.py "My mom's birthday is 15 May 2026. Remember that and give me a calendar reminder for two weeks before and on the day."

# C run 2 — durable memory read (DO NOT clear state/)
python -u agent6.py "When is mom's birthday?"

# D — multi-source synthesis
rm -rf state/ sandbox/
python -u agent6.py "Search for 'Python asyncio best practices', read the top 3 results, and give me a short numbered list of the advice they agree on."
```

## Iteration counts

| Query | Iters | Target | 2× cap | Status |
|-------|-------|------------------|-------|--------|
| A — Shannon Wikipedia | 4 | 3 | 6 | ✅ |
| B — Tokyo + weather | 4 | 6 | 12 | ✅ |
| C run 1 — durable write | 3 | 4 | 8 | ✅ fact persisted |
| C run 2 — durable read | 1 | 2 | 4 | ✅ found in memory alone |
| D — asyncio synthesis | 4 | 5–7 | 14 | ✅ |

## Perception's Prompt + PoP Validation JSON

> **PoP** = *Prompt of Prompts* — the JSON Schema sent to the LLM as
> `response_format`. It's the meta-prompt that constrains every Perception
> output into a typed `Observation` (no free-text drift, no hallucinated
> fields). The schema is generated at runtime by
> `PerceptionOutput.model_json_schema()`, so it cannot drift from
> [schemas.py](schemas.py).

The Decision role uses a different validation mechanism (MCP tool list +
`tool_choice="auto"` instead of `response_format`), and the memory
classifier has its own PoP. All three are documented below.

### Perception system prompt

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

### Perception validation JSON (`PerceptionOutput.model_json_schema()`)

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

The output schema has no `id` field — goals carry **positional identity**. The outer loop maps `goals[i]` back to the prior goal's id by position, so the model cannot invent a stale identifier.

### Decision system prompt

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

### Decision validation — tool list

Decision does not use `response_format`. It passes `tools=[…]` + `tool_choice="auto"` and validates the response is either plain text OR exactly one `tool_calls[]` entry against the MCP tool's `input_schema`. The tool list is loaded once at boot via `session.list_tools()` and converted to gateway `ToolDef` shape:

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

The internal canonical shape (`DecisionOutput`, [schemas.py:67-82](schemas.py#L67-L82)) is validated by Pydantic — exactly one of `answer` / `tool_call` populated:

```json
{
  "$defs": {
    "ToolCall": {
      "properties": {
        "name":      { "type": "string" },
        "arguments": { "additionalProperties": true, "type": "object" }
      },
      "required": ["name"],
      "type": "object"
    }
  },
  "properties": {
    "answer":    { "anyOf": [ { "type": "string" }, { "type": "null" } ], "default": null },
    "tool_call": { "anyOf": [ { "$ref": "#/$defs/ToolCall" }, { "type": "null" } ], "default": null }
  },
  "type": "object"
}
```

### Memory classifier validation JSON (`MemoryClassification.model_json_schema()`)

Sent to the gateway as `response_format.schema` on every `memory.remember()` call:

```json
{
  "additionalProperties": false,
  "description": "Output of the memory.remember() classification LLM call.",
  "properties": {
    "kind": {
      "enum": ["fact", "preference", "tool_outcome", "scratchpad"],
      "type": "string"
    },
    "keywords":   { "items": { "type": "string" }, "type": "array" },
    "descriptor": { "type": "string" },
    "value":      { "additionalProperties": true, "type": "object" }
  },
  "required": ["kind", "keywords", "descriptor", "value"],
  "type": "object"
}
```

## Captured terminal outputs

### Query A — Shannon Wikipedia (4 iterations)

```
[boot] run_id=99d46eae
[boot] query='Fetch https://en.wikipedia.org/wiki/Claude_Shannon and tell me his\nbirth date, death date, and three key contributions to information\ntheory.'
[boot] mcp tools: ['web_search', 'fetch_url', 'get_time', 'currency_convert', 'read_file', 'list_dir', 'create_file', 'update_file', 'edit_file']

─── iter 1 ─────────────────────────────────────
[memory.read]   0 hits
[perception]    [open] Fetch Claude Shannon wikipedia page
[perception]    [open] Identify birth and death dates
[perception]    [open] Identify three information theory contributions
[decision]      TOOL_CALL: fetch_url({"url": "https://en.wikipedia.org/wiki/Claude_Shannon"})
[action]        → [artifact art:a5444f83543328d9, 262128 bytes] preview: {   "status": 200,   "content_type": "text/markdown",   "length_bytes": 256033,   "text": "[Jump to content](https://en.wikipedia.org/wiki/Claude...

─── iter 2 ─────────────────────────────────────
[memory.read]   1 hits
                - [tool_outcome] art:a5444f83543328d9 fetch_url(...) -> [artifact art:a5444f83543328d9, 262128 bytes] preview: {   "status": 200,   "conte
[perception]    [done] Fetch Claude Shannon wikipedia page
[perception]    [open] Identify birth and death dates  attach=art:a5444f83543328d9
[perception]    [open] Identify three information theory contributions
[attach]        art:a5444f83543328d9 (262128 bytes)
[decision]      ANSWER: Claude Shannon was born on April 30, 1916, and he passed away on February 24, 2001.

─── iter 3 ─────────────────────────────────────
[memory.read]   1 hits
[perception]    [done] Fetch Claude Shannon wikipedia page
[perception]    [done] Identify birth and death dates
[perception]    [open] Identify three information theory contributions  attach=art:a5444f83543328d9
[attach]        art:a5444f83543328d9 (262128 bytes)
[decision]      ANSWER: Claude Shannon's work established the field of information theory, and three key contributions include: ...

─── iter 4 ─────────────────────────────────────
[memory.read]   1 hits
[perception]    [done] Fetch Claude Shannon wikipedia page
[perception]    [done] Identify birth and death dates
[perception]    [done] Identify three information theory contributions

[done] all 3 goals satisfied

============================================================
FINAL: - Claude Shannon was born on April 30, 1916, and he passed away on February 24, 2001.

- Claude Shannon's work established the field of information theory, and three key contributions include:

1.  **Foundational Framework:** He authored the seminal 1948 paper, "A Mathematical Theory of Communication," which serves as the foundational "blueprint" for the digital era and the entire field of information theory.
2.  **Introduction of the 'Bit':** Shannon formally introduced the term "bit" (binary digit) as the fundamental unit of information, a concept that underpins all digital computing and data storage.
3.  **Concept of Entropy:** He pioneered the application of "entropy" to information theory, providing a rigorous mathematical way to measure the uncertainty or information content in data, which remains essential for modern data compression and transmission.
============================================================
```

### Query B — Tokyo activities + weather (4 iterations)

```
[boot] run_id=b855cd4b
[boot] query="Find 3 family-friendly things to do in Tokyo this weekend.\nCheck Saturday's weather forecast there and tell me which one\nis most appropriate."
[boot] mcp tools: ['web_search', 'fetch_url', 'get_time', 'currency_convert', 'read_file', 'list_dir', 'create_file', 'update_file', 'edit_file']

─── iter 1 ─────────────────────────────────────
[memory.read]   0 hits
[perception]    [open] Identify three family-friendly activities in Tokyo for the weekend.
[perception]    [open] Retrieve Saturday's weather forecast for Tokyo.
[perception]    [open] Recommend the best activity based on the weather.
[decision]      TOOL_CALL: web_search({"query": "family-friendly activities Tokyo weekend", "max_results": 5})
[action]        → [artifact art:7526d3964df898fc, 11269 bytes] preview: {   "title": "The best Tokyo Family-friendly activities 2026 - Free cancellation",   "url": "https://www.getyourguide.com/tokyo-l193/family-friend...

─── iter 2 ─────────────────────────────────────
[memory.read]   1 hits
[perception]    [done] Identify three family-friendly activities in Tokyo for the weekend.
[perception]    [open] Retrieve Saturday's weather forecast for Tokyo.
[perception]    [open] Recommend the best activity based on the weather.
[decision]      TOOL_CALL: web_search({"max_results": 5, "query": "Tokyo weather forecast Saturday"})
[action]        → [artifact art:d64b0f8bf8b85828, 8985 bytes] preview: {   "title": "Tokyo Weather in May 2026: Not Too Hot, Not Too Crowded",   "url": "https://www.japanhighlights.com/japan/tokyo/may-weather",   "snip...

─── iter 3 ─────────────────────────────────────
[memory.read]   2 hits
[perception]    [done] Identify three family-friendly activities in Tokyo for the weekend.
[perception]    [done] Retrieve Saturday's weather forecast for Tokyo.
[perception]    [open] Recommend the best activity based on the weather.  attach=art:7526d3964df898fc
[attach]        art:7526d3964df898fc (11269 bytes)
[decision]      ANSWER: Since the weather in Tokyo during May is generally pleasant—often described as "not too hot, not too crowded"—it is a fantastic time for both indoor and outdoor family-friendly activities. ...

─── iter 4 ─────────────────────────────────────
[memory.read]   2 hits
[perception]    [done] Identify three family-friendly activities in Tokyo for the weekend.
[perception]    [done] Retrieve Saturday's weather forecast for Tokyo.
[perception]    [done] Recommend the best activity based on the weather.

[done] all 3 goals satisfied

============================================================
FINAL: Since the weather in Tokyo during May is generally pleasant—often described as "not too hot, not too crowded"—it is a fantastic time for both indoor and outdoor family-friendly activities. Based on the options available, here are the best recommendations:

*   **For Indoor Fun (Weather-Proof & Engaging):**
    *   **Warner Bros. Studio Tour Tokyo – The Making of Harry Potter:** This is a top-rated, large-scale indoor exhibit perfect for escaping any unexpected spring showers.
    *   **Miraikan (National Museum of Emerging Science and Innovation):** Located in Odaiba, this is a highly interactive, hands-on museum that is excellent for children interested in science and technology.
    *   **Pokémon Center & Café:** A massive hit for families; booking the themed café in advance provides a memorable, air-conditioned dining and entertainment experience.

*   **For Outdoor Exploration (Enjoying the May Weather):**
    *   **Ueno Park & Zoo:** With the mild May temperatures, exploring the zoo (the oldest in Japan) and the surrounding park grounds is very comfortable.
    *   **Sumida River Cruise:** A relaxing way to see the city from the water. It is a calm, scenic activity that is particularly pleasant when the weather is clear.
    *   **Asakusa Cultural Exploration:** A visit to Senso-ji Temple is a classic experience; wandering the surrounding historic streets is much more enjoyable in temperate May weather than in the intense heat of summer.

*   **Unique Cultural Experiences:**
    *   **Family Sumo or Samurai Ninja Workshops:** These are highly-rated, engaging, and culturally immersive activities located in areas like Asakusa, suitable for various ages and available as indoor sessions if needed.

**Recommendation:** Given the generally mild and pleasant conditions, I recommend a morning visit to **Ueno Zoo** to take advantage of the fresh air, followed by an afternoon at the **Miraikan Museum** in Odaiba for a balanced, high-quality family day.
============================================================
```

### Query C run 1 — Mom's birthday durable write (3 iterations)

```
[boot] run_id=8741345c
[boot] query="My mom's birthday is 15 May 2026. Remember that and give me\n       a calendar reminder for two weeks before and on the day."
[memory.remember] fact: The user's mother has a birthday on May 15, 2026.
                  keywords=['mom', 'birthday', 'may', '2026']
[boot] mcp tools: ['web_search', 'fetch_url', 'get_time', 'currency_convert', 'read_file', 'list_dir', 'create_file', 'update_file', 'edit_file']

─── iter 1 ─────────────────────────────────────
[memory.read]   1 hits
                - [fact] The user's mother has a birthday on May 15, 2026.
[perception]    [done] Store birthday date
[perception]    [open] Schedule reminder for May 1 2026
[perception]    [open] Schedule reminder for May 15 2026
[decision]      TOOL_CALL: create_file({"path": "reminder_2026-05-01.txt", "content": "Reminder: Scheduled task for May 1, 2026."})
[action]        → {   "ok": true,   "path": "reminder_2026-05-01.txt",   "size_bytes": 41 }

─── iter 2 ─────────────────────────────────────
[memory.read]   2 hits
[perception]    [done] Store birthday date
[perception]    [done] Schedule reminder for May 1 2026
[perception]    [open] Schedule reminder for May 15 2026
[decision]      TOOL_CALL: create_file({"content": "Reminder: Mother's birthday on May 15, 2026.", "path": "reminder_2026-05-15.txt"})
[action]        → {   "ok": true,   "path": "reminder_2026-05-15.txt",   "size_bytes": 44 }

─── iter 3 ─────────────────────────────────────
[memory.read]   3 hits
[perception]    [done] Store birthday date
[perception]    [done] Schedule reminder for May 1 2026
[perception]    [done] Schedule reminder for May 15 2026

[done] all 3 goals satisfied

============================================================
FINAL: Completed actions:
  ✓ create_file({"path": "reminder_2026-05-01.txt", ...}) → {"ok": true, "path": "reminder_2026-05-01.txt", "size_bytes": 41}
  ✓ create_file({"path": "reminder_2026-05-15.txt", ...}) → {"ok": true, "path": "reminder_2026-05-15.txt", "size_bytes": 44}
============================================================
```

After this run, `state/memory.json` contains the durable fact:

```json
{
  "kind": "fact",
  "keywords": ["mom", "birthday", "may", "2026"],
  "descriptor": "The user's mother has a birthday on May 15, 2026."
}
```

### Query C run 2 — Mom's birthday durable read (1 iteration)

```
[boot] run_id=a03f65f9
[boot] query="When is mom's birthday?"
[boot] mcp tools: ['web_search', 'fetch_url', 'get_time', 'currency_convert', 'read_file', 'list_dir', 'create_file', 'update_file', 'edit_file']

─── iter 1 ─────────────────────────────────────
[memory.read]   2 hits
                - [fact] The user's mother has a birthday on May 15, 2026.
                - [tool_outcome] create_file(...) -> {
  "ok": true,
  "path": "reminder_2026-05-15.txt",
  "size_bytes": 44
}
[perception]    [done] Identify mother's birthday

[done] all 1 goals satisfied

============================================================
FINAL: From memory: The user's mother has a birthday on May 15, 2026.
============================================================
```

The fact survived run 1 → run 2 via `state/memory.json`. Perception saw it in MEMORY HITS and marked the goal done in iter 1 without dispatching a single tool.

### Query D — asyncio synthesis (4 iterations)

```
[boot] run_id=83cb6aa5
[boot] query="Search for 'Python asyncio best practices', read the top 3 results,\nand give me a short numbered list of the advice they agree on."
[boot] mcp tools: ['web_search', 'fetch_url', 'get_time', 'currency_convert', 'read_file', 'list_dir', 'create_file', 'update_file', 'edit_file']

─── iter 1 ─────────────────────────────────────
[memory.read]   0 hits
[perception]    [open] Search for Python asyncio best practices
[perception]    [open] Read the top 3 results
[perception]    [open] Synthesize advice list
[decision]      TOOL_CALL: web_search({"query": "Python asyncio best practices", "max_results": 5})
[action]        → [artifact art:310bfd9f176abb93, 10293 bytes] preview: {   "title": "Asyncio Best Practices and Common Pitfalls - Shane's Personal Blog",   "url": "https://www.shanechang.com/p/python-asyncio-best-prac...

─── iter 2 ─────────────────────────────────────
[memory.read]   3 hits
[perception]    [done] Search for Python asyncio best practices
[perception]    [open] Read the top 3 results  attach=art:310bfd9f176abb93
[perception]    [open] Synthesize advice list
[attach]        art:310bfd9f176abb93 (10293 bytes)
[decision]      ANSWER: To read the top 3 results, we need to access the content of the attached artifacts. ...

─── iter 3 ─────────────────────────────────────
[memory.read]   3 hits
[perception]    [done] Search for Python asyncio best practices
[perception]    [done] Read the top 3 results
[perception]    [open] Synthesize advice list  attach=art:310bfd9f176abb93
[attach]        art:310bfd9f176abb93 (10293 bytes)
[decision]      ANSWER: Based on the gathered resources, here is a synthesized list of best practices for working with Python's `asyncio` library: ...

─── iter 4 ─────────────────────────────────────
[memory.read]   3 hits
[perception]    [done] Search for Python asyncio best practices
[perception]    [done] Read the top 3 results
[perception]    [done] Synthesize advice list

[done] all 3 goals satisfied

============================================================
FINAL: - To read the top 3 results, we need to access the content of the attached artifacts. The artifacts contain information about the search results, including their titles, URLs, and snippets. Since the goal is to read the top 3 results, we will provide a final answer based on the information available in the artifacts.

The top 3 results are:
1. "Asyncio Best Practices and Common Pitfalls - Shane's Personal Blog" - This article discusses best practices and common pitfalls when working with Python's asyncio library. It covers topics such as using `asyncio.run()` as the main entry point, preferring async context managers, and handling cancellation gracefully.
2. "Asyncio best practices - Async-SIG - Discussions on Python.org" - This discussion on Python.org talks about best practices for writing asynchronous code in Python. It covers topics such as using async/await, avoiding long-running loops, and using tasks for independent operations.
3. "How to Use asyncio for Concurrent Programming in Python" - This article provides an introduction to using asyncio for concurrent programming in Python. It covers topics such as running blocking code in an async context, creating tasks for independent operations, and handling cancellation gracefully.

- Based on the gathered resources, here is a synthesized list of best practices for working with Python's `asyncio` library:

*   **Proper Entry Point:** Always use `asyncio.run()` to start your asynchronous programs. This function correctly manages the event loop lifecycle, including creation, cleanup, and exception handling.
*   **Prevent Event Loop Blocking:** Never use blocking synchronous calls (like `time.sleep()` or heavy CPU-bound computations) directly inside an `async` function, as these will halt the entire event loop. Instead, use non-blocking alternatives (e.g., `asyncio.sleep()`) or offload blocking operations to a thread pool using `loop.run_in_executor()`.
*   **Leverage Concurrency:** Avoid running tasks sequentially if they are independent. Use `asyncio.create_task()` to schedule operations to run concurrently, or utilize `asyncio.gather()` to manage and await multiple tasks at once for better performance.
*   **Manage Resources Correctly:** Prefer using asynchronous context managers (`async with`) to ensure that resources like network connections or file handles are opened and closed correctly within an async context.
*   **Handle Cancellations Gracefully:** Always catch `asyncio.CancelledError` within your tasks to perform necessary cleanup operations before the task terminates.
*   **Enable Debugging:** When troubleshooting complex issues, use `asyncio.run(main(), debug=True)` to enable debug mode, which helps identify issues like unawaited coroutines or unretrieved task exceptions.
============================================================
```
