# Session 8 — DAG Orchestration: Assignment Results

A DAG-based multi-agent agent built on the Session 8 growing-graph
orchestrator. This README documents all **five** assignment parts with the
exact commands, the resulting DAG shapes, and links to the captured logs
under [`logs/`](logs/) and the persisted sessions under
[`state/sessions/`](state/sessions/).

> **Architectural compliance.** `flow.py` (the Executor / Graph) is
> **byte-identical to the starting code** — every part below was achieved by
> editing only YAML + prompt files, plus adding MCP tools and tool schemas
> (the sanctioned extension path). The recovery classifier's unit tests still
> pass: `python -m pytest tests/test_recovery.py -q` → **22 passed**.

---

## 0. Running it

`uv` is not required. The V8 gateway is launched directly from its own venv:

```bash
# 1) start the V8 gateway (port 8108)
cd ../gateway && ./.venv/bin/python main.py &        # or: uv run main.py

# 2) run any query through the orchestrator
cd ../code
export EAGV3_GATEWAY_DIR="$(cd ../gateway && pwd)"
./.venv/bin/python flow.py "Say hello in one short sentence."

# resume a killed run
./.venv/bin/python flow.py --resume <session_id>
```

Every skill is pinned to a provider in `../gateway/agent_routing.yaml`, so a run
logs **zero** router-classification calls. Per-skill, per-session token spend is
queryable on V8:

```bash
curl "http://localhost:8108/v1/cost/by_agent?session=<sid>"
```
---

## Part 1 — The five base queries pass

| Query | Command (verbatim) | Nodes | Wall-clock | Log / Session |
|-------|--------------------|:-----:|:----------:|---------------|
| **hello** | `Say hello in one short sentence.` | 2 (planner→formatter) | ~5 s | [logs/query_hello.log](logs/query_hello.log) |
| **A** Shannon | `Fetch https://en.wikipedia.org/wiki/Claude_Shannon and tell me his birth date, death date, and three key contributions to information theory.` | 4 (planner→researcher→distiller→formatter) | ~30 s | [logs/queryA_shannon.log](logs/queryA_shannon.log) |
| **I** populations | `Find the populations of London, Paris, Berlin and tell me which two are closest in size.` | **7** (planner→3×researcher→coder→formatter + sandbox_executor) | ~57 s | session `s8-e9c07a84` |
| **J** graceful | `Read /nonexistent/path.txt and tell me what's in it.` | 2 (planner→formatter, no tool dispatched) | ~8 s | [logs/queryJ_graceful.log](logs/queryJ_graceful.log) |
| **K** resumable | `For Lagos, Cairo, and Kinshasa, find current populations and growth rates and tell me which is growing fastest.` | 7 | ~66 s | [logs/queryK_full.log](logs/queryK_full.log) |

**Query I** reproduces the canonical 7-node DAG including the trust-and-verify
diamond (Coder fans out to Formatter **and** SandboxExecutor):

```
[n:1] planner          complete (2.4s)
[n:2] researcher       complete (24.2s)   ┐
[n:3] researcher       complete (36.3s)   ├ parallel
[n:4] researcher       complete (32.5s)   ┘
[n:5] coder            complete (15.9s)
[n:6] formatter        complete (1.4s)
[n:7] sandbox_executor complete (0.0s)
FINAL: …the two cities closest in size are Berlin and Paris, with a
       population difference of approximately 1.66 million.
```

The sandbox **executed** the Coder's Python and verified the arithmetic:
`PAIR: ('Paris', 'Berlin')  diff 1.66` (see Part 4).

**Query K (resume)** — the run was `SIGKILL`-ed mid-researcher-layer, then
resumed from disk:

* Part 1 (killed): planner `complete`, 3 researchers `running`, coder/formatter
  `pending` — see [logs/queryK_resume_part1.log](logs/queryK_resume_part1.log).
* Part 2 (`flow.py --resume s8_K_resume_demo`): the 3 `running` researchers were
  reset to `pending` and re-run; the **planner was not re-run** (already
  `complete`); coder, formatter, sandbox then finished — see
  [logs/queryK_resume_part2.log](logs/queryK_resume_part2.log).

---

## Part 2 — Parallel fan-out (wall-clock = max, not sum)

**Query (designed):**
> Find the land areas in square kilometers of France, Spain, and Germany, and
> tell me how much larger the biggest is than the smallest.

Three **independent** sub-tasks → three concurrent `researcher` nodes, then a
`coder` computes the difference. Session `s8_part2_fanout`, log
[logs/part2_fanout.log](logs/part2_fanout.log).

Per-node timing pulled from the persisted `started_at` / `completed_at`:

```
node  skill            start(rel) finish(rel) elapsed
n:2   researcher           6.75s     51.49s    44.74s ┐
n:3   researcher          28.36s     51.49s    23.13s ├ all finish at the
n:4   researcher           6.46s     51.49s    45.04s ┘ gather barrier 51.49s

researchers: 3 branches
sum-of-elapsed (if serial): 112.90s
max single branch          :  45.04s
actual parallel-layer wall :  45.04s   ← equals MAX, not SUM
overlap proof: latest start 28.36s < earliest finish 51.49s  → True
speedup vs serial          :  2.51x
```

The three branches **overlap** (latest start < earliest finish) and all resolve
at the same millisecond — that is the `asyncio.gather` barrier. The layer costs
the slowest branch (45 s), not the sum (113 s).

**Answer:** France 640,427 km² (largest), Germany 348,672 km² (smallest),
difference **291,755 km²** — grounded by the sandbox.

---

## Part 3 — A Critic verdict the Critic can actually verify (Critic-with-tools)

The Session 8 brief shows the generic LLM Critic **rubber-stamping** a syllable
constraint because it cannot count. The honest fix (the Session 9 forward
pointer) is to **give the Critic tools**. I implemented exactly that:

* New MCP tools `count_chars`, `count_words`, `count_syllables` (deterministic).
* `critic` now declares `tools_allowed: [count_chars, count_words, count_syllables]`
  and `critic.md` instructs it to **measure with a tool, then compare** — the
  tool supplies the number, the model supplies the judgement.
* A new `writer` skill produces the constrained text; the planner wires
  `writer → critic → formatter` (the formatter depends on the critic for gating
  but reads the writer for content).

**Query:**
> Write a short tagline for a privacy-first email app. It must be EXACTLY 30
> characters long, including spaces.

**Fail → recovery → corrected answer** (session `s8_part3_exactchars`, log
[logs/part3_exactchars.log](logs/part3_exactchars.log)):

```
n:2  writer  "Your private inbox, secured now"  len=31
n:3  critic  FAIL  "measured 31 characters … required exactly 30"
  ↪ critic-fail recovery: planner node n:5 for n:2
n:6  writer  "Your email, kept truly private."  len=31
n:7  critic  FAIL  "measured 31 … required exactly 30"
  ↪ critic-fail recovery: planner node n:9 for n:6
n:10 writer  "Your email, kept truly secret."   len=30
n:11 critic  PASS  "measured character count is 30, matches exactly"
n:12 formatter → FINAL: "Your email, kept truly secret."   (exactly 30 chars)
```

**Pass on a clean first attempt** (session `s8_part3_critic_run1`, log
[logs/part3_run1.log](logs/part3_run1.log)) — same skill shape, "≤35 chars":

```
n:2 writer "Your email, kept strictly private."   (34 chars)
n:3 critic PASS  "tagline is 34 characters … within the 35-character limit"
```

So across two runs the Critic produces **both a pass and a fail**, the fail
**splices a Planner recovery**, and the recovery **produces a corrected answer**.

**Bonus — proof the Critic is no longer a rubber stamp.** The brief's exact
forcing query (`…MUST be exactly 4-6-4 syllables — count them`,
[logs/part3_haiku.log](logs/part3_haiku.log)) now **fails every attempt with an
accurate measurement** (`measured 5-7-6 … constraint was 4-6-4`, `measured 4-7-4
… line 2 has 7`) instead of approving. Exact 4-6-4 is genuinely beyond the
writer, so the recovery loop is bounded by `MAX_NODES` — a faithful illustration
of the brief's *"the wiring is mechanism; the verdict quality is policy"* point,
now with the verdict quality actually fixed.

---

## Part 4 — The Coder skill

`prompts/coder.md` was a stub; it now emits
`{"code", "summary", "rationale"}`. The Coder extracts the concrete numbers from
the upstream researchers, **hard-codes** them, emits a stdlib-only program that
prints the answer, and the orchestrator hands it to the SandboxExecutor
automatically (`internal_successors: [sandbox_executor]`).

Demonstrated on **Query I** (computation the Formatter cannot do reliably from
text). The emitted program and its **real** sandbox execution (session
`s8-e9c07a84`):

```python
london_pop = 9.1; paris_pop = 2.04; berlin_pop = 3.7
cities = {"London": london_pop, "Paris": paris_pop, "Berlin": berlin_pop}
import itertools
min_diff = float('inf'); pair = None
for (n1,p1),(n2,p2) in itertools.combinations(cities.items(), 2):
    if abs(p1-p2) < min_diff: min_diff = abs(p1-p2); pair = (n1,n2)
print("ANSWER:", min_diff); print("PAIR:", pair)
```
```
sandbox: exit 0  timed_out False
ANSWER: 1.6600000000000001
PAIR: ('Paris', 'Berlin')
```

The Formatter quotes the Coder's `summary`; the SandboxExecutor independently
**verifies** the figure. Trust + verify, in parallel, off one Coder node.

---

## Part 5 — A new skill: `translator`

A skill the catalogue did not cover (research/distil/summarise/render/code is
covered; translation is not). Added with **only** a YAML entry + a prompt file —
**the orchestrator needed no modification**.

* `agent_config.yaml` → `translator:` entry (plain LLM skill, no tools).
* `prompts/translator.md` → translate `INPUTS` text into the language named in
  `metadata.question`.
* `../gateway/agent_routing.yaml` → `translator: gemini`.

**Query:**
> Translate the sentence 'Knowledge is power, but privacy is freedom.' into French.

Session `s8_part5_translator`, log [logs/part5_translator.log](logs/part5_translator.log):

```
[n:1] planner    complete   (routes to the new skill purely from its yaml description)
[n:2] translator complete   {"target_language":"French",
                             "translation":"Le savoir est le pouvoir, mais la vie privée est la liberté."}
[n:3] formatter  complete
FINAL: … 'Le savoir est le pouvoir, mais la vie privée est la liberté.'
```

> A second new skill, **`writer`** (Part 3), was likewise added with only YAML +
> a prompt. Two new skills, zero Executor edits — the strongest possible
> evidence that *"a skill is its yaml entry plus its prompt file."*

---

## V8 observability (cost-by-agent)

Per-skill, per-session token spend — V7 cannot answer this; V8's `agent` column
can. Example (`s8_part3_exactchars`): `critic` 6 calls (3 verdicts × {tool-call
turn, verdict turn}), `planner` 3 calls (initial + 2 recoveries), `writer` 3
calls. Pulled live with `curl .../v1/cost/by_agent?session=<sid>`.
