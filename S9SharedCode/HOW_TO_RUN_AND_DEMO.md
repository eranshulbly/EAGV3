# How to Run Locally + YouTube Demo Script
### Session 9 — Browser Comparison Agent

This guide has two parts:

- **Part A — Run it locally** (verified, copy-pasteable commands).
- **Part B — A scene-by-scene YouTube demo script** (what to show, what to say).

The task: *compare the top 3 trending **Python** repos on GitHub **this week*** —
driven through the unmodified Session 8 orchestrator, landing on the Browser
skill's **a11y** cascade layer, ending in a comparison table + an 8-item replay
report.

---

# Part A — Run it locally

## A0. Prerequisites

| Need | Why | Check |
|---|---|---|
| **Python 3.11** | best wheel support for the deps | `python3.11 --version` |
| **Network access** | the agent browses live github.com | `curl -I https://github.com/trending` |
| **API keys in `.env`** | the V9 gateway calls Gemini etc. | repo-root `.env` has `GEMINI_API_KEY=...` |
| **~250 MB disk** | Playwright Chromium + venv | — |

The repo root `.env` already contains the provider keys
(`GEMINI_API_KEY`, `GROQ_API_KEY`, `GITHUB_ACCESS_TOKEN`, …). The free-tier
Gemini key is the only one strictly required for this task.

> Paths below assume the repo is at `…/EAGV3/S9SharedCode`. Run everything from
> the repo root unless a step says otherwise.

## A1. One-time setup

```bash
cd .../EAGV3/S9SharedCode

# 1. virtualenv + dependencies (gateway + browser stack)
python3.11 -m venv .venv
source .venv/bin/activate
pip install --upgrade pip
pip install fastapi "uvicorn[standard]" httpx python-dotenv pydantic jsonschema \
            pyyaml playwright trafilatura pillow networkx numpy faiss-cpu

# 2. download the headless Chromium that Playwright drives
python -m playwright install chromium

# 3. gateway.py resolves the gateway dir as EAGV3/llm_gatewayV9 (sibling of code/).
#    In this repo it lives inside S9SharedCode/, so add a symlink at the
#    expected location (one time):
ln -sfn "$PWD/llm_gatewayV9" "$(dirname "$PWD")/llm_gatewayV9"
```

> **Why the symlink?** `code/gateway.py` computes the gateway path as
> `parents[2]/llm_gatewayV9` (i.e. `EAGV3/llm_gatewayV9`). The symlink satisfies
> that without editing any shipped code. If your checkout already has the
> gateway as a sibling of `code/`, skip this step.

## A2. Start the V9 gateway (terminal 1)

The gateway serves `/v1/chat` (a11y layer), `/v1/vision` (vision layer), and
`/v1/cost/by_agent` (the ledger the report reads) on **port 8109**.

```bash
cd .../EAGV3/S9SharedCode
./.venv/bin/python llm_gatewayV9/main.py
# leave this running. Health check from another terminal:
curl -s http://localhost:8109/v1/providers | python3 -m json.tool
```

You should see `gemini, groq, cerebras, …` in the providers list.

> The shipped `llm_gatewayV9/run.sh` uses `uv`. If you don't have `uv`, start it
> with the venv python as shown above instead.

## A3. Run the comparison agent (terminal 2)

```bash
cd .../EAGV3/S9SharedCode/code
../.venv/bin/python assignment_run.py
```

What you'll see stream by:

```
[memory.read] N hit(s) visible to every skill this run
[n:1] planner            complete
[n:2] browser            complete        ← drives github.com/trending (a11y)
[n:3] distiller          complete
[n:5] critic             complete        ← genuine pass/fail verdict
[n:4] formatter          complete
FINAL:
| Repository Name | Star Count | Description |
| NVIDIA / SkillSpector | 6,886 | Security scanner for AI agent skills. |
...
```

Runtime ≈ 1–3 minutes. If the Distiller's first attempt drops a field, the
Critic fails it and the orchestrator re-plans once (a few extra nodes) before
converging — that's the recovery machinery, not an error.

## A4. Build + view the 8-item replay report

```bash
../.venv/bin/python assignment_report.py s9_assignment_gh_trending
```

Open the generated file **in VS Code's Markdown preview** (so the Mermaid DAG
and screenshots render):

```
code/state/sessions/s9_assignment_gh_trending/REPLAY_REPORT.md
```

It contains all eight required items: goal, Planner DAG, browser path,
browser actions, screenshots + a11y legends, extracted data, the comparison
table, and the turn/cost summary.

## A5. (Optional) Direct cascade smoke test

To watch just the Browser skill drive the page (no planner/distiller/formatter):

```bash
../.venv/bin/python _smoke_browser.py
# artifacts → code/out/smoke_gh_trending/
```

## Troubleshooting

| Symptom | Cause | Fix |
|---|---|---|
| `Gateway V9 client unavailable` / planner `NoneType is not callable` | gateway dir not found at expected path | redo the **A1 step 3** symlink |
| `faiss-cpu is required for S7` | memory layer needs faiss | `pip install faiss-cpu` |
| `Execution context was destroyed` | page navigated mid-snapshot | already handled by the retry guard in `browser/dom.py`; ensure you're on the patched file |
| Browser run blocked / `gateway_blocked` | target served a CAPTCHA | expected for some sites; GitHub Trending does not block |
| Critic fails forever / 60-node cap | weak judge + strict prompt | confirm `agent_routing.yaml` has `critic: gemini` and **restart the gateway** (it reads routing once at startup) |
| `port 8109 in use` / routing changes ignored | a stale gateway is still bound | `pkill -9 -f main.py`, then start one fresh gateway |
| Cost ledger shows stale rows | same session id reused across runs (the DB is keyed by session) | use a fresh `SESSION_ID` in `assignment_run.py`, or clear that session's rows from `llm_gatewayV9/gateway_v8.db` |

---

# Part B — YouTube demo script (≈ 6–8 minutes)

A scene-by-scene walkthrough. **Bold** = on-screen action; *italics* = narration.

### Scene 0 — Cold open (20s)
- **Show** the final comparison table in the terminal.
- *"This agent went to GitHub Trending, clicked through real filter dropdowns,
  and built this comparison table of the top 3 Python repos this week — for
  zero cents. Let me show you how, layer by layer."*

### Scene 1 — The problem (40s)
- **Show** `prompts/researcher.md` / mention `fetch_url`.
- *"Session 8's web tools read static HTML. They can't open a dropdown, can't
  sort, can't reach data that only appears after you interact. Session 9 adds
  one skill — Browser — that can."*
- **Show** `agent_config.yaml` lines 87–99 (the `browser:` entry).
- *"It plugs into the catalogue like any other skill. The orchestrator was not
  modified — that's the whole point."*

### Scene 2 — The four-layer cost cascade (50s)
- **Show** `browser/skill.py` `run()` (the cascade) or the diagram in
  `README_SESSION9.md` §3.
- *"The skill tries the cheapest thing first: plain HTTP extract, then
  deterministic selectors, then the accessibility tree with a cheap text model,
  and only as a last resort, vision. Each layer up costs more. We'll see it stop
  at the a11y layer."*

### Scene 3 — Start the gateway (30s)
- **Run** `./.venv/bin/python llm_gatewayV9/main.py` (terminal 1).
- **Run** `curl -s localhost:8109/v1/providers | python3 -m json.tool`.
- *"This is the V9 gateway — it routes every model call and, new in Session 9,
  serves a vision endpoint. It also keeps the cost ledger we'll read at the end."*

### Scene 4 — Run the agent live (90s) — the centerpiece
- **Run** `../.venv/bin/python assignment_run.py` (terminal 2).
- **Narrate the node stream as it appears:**
  - *"Planner builds the DAG and chooses the Browser skill, because the query
    targets a specific site's interactive listing."*
  - *"Browser node — this is it driving a real headless Chromium against
    github.com/trending."*
  - *"Distiller pulls structured fields; the Critic checks them; the Formatter
    renders the table."*
- **Show** the final table printed in the terminal.

### Scene 5 — The replay report (120s) — prove all 8 items
- **Run** `../.venv/bin/python assignment_report.py s9_assignment_gh_trending`.
- **Open** `REPLAY_REPORT.md` in VS Code preview. Scroll through:
  1. **Goal** — *"the original ask."*
  2. **Planner DAG** — *"the executed graph; green nodes ran."* (point at any
     recovery planner / skipped formatter if present)
  3. **Browser path** — *"`a11y` — it never needed vision. That's the cost
     cascade paying off."*
  4. **Browser actions** — *"click the Language dropdown, type Python, pick it,
     open Date range, pick This week — five real actions."*
  5. **Screenshots + legends** — **the money shot.** Scroll the per-turn a11y
     legends: *"Turn 1, `Language: Any` is a dropdown trigger. The model clicks
     it ALONE — that's the dropdown-as-fence rule. Turn 2, look: the popover
     options appear in the legend that weren't in the DOM before. Turn 3,
     `Language: Python`. Turn 4, the date options. Turn 5, `This week`."*
  6. **Extracted data** — *"clean structured JSON."*
  7. **Comparison table** — *"the final answer."*
  8. **Cost** — *"every call on free-tier Gemini Flash-Lite. Total: zero
     dollars. The claim, measured."*

### Scene 6 — The "no orchestrator edits" proof (40s)
- **Show** `ASSIGNMENT_S9.md` §4 table.
- *"flow.py and recovery.py — the orchestrator — were never touched. Everything
  new plugs in as a Browser skill extension, a prompt, or routing config. New
  capability, zero core changes — exactly what Session 8 promised."*

### Scene 7 — Bonus: recovery in action (40s, optional)
- If your captured run had a critic-fail recovery, **show** §2's recovery
  narrative.
- *"Here the Distiller dropped the descriptions the user asked for. The Critic
  caught it and failed the node; the orchestrator re-planned and reused the work
  already done — the recovery-amnesia fix — then the second pass passed. The
  system is robust to a bad intermediate result."*

### Scene 8 — Close (20s)
- *"One skill, four layers, cheapest-first. Real interaction, full replay,
  measurable cost — and the orchestrator stayed exactly as it was. That's
  Session 9."*

### Demo tips
- **Pre-warm** once before recording (run it, delete the session) so deps are
  cached and the first live run is fast: `rm -rf code/state/sessions/s9_assignment_gh_trending`.
- Keep **two terminals** visible: gateway (left), agent (right).
- For a deterministic 5-node take, re-run until you get a clean pass; for a
  richer take, keep one that shows a recovery cycle.
- Have the **VS Code Markdown preview** of `REPLAY_REPORT.md` ready in a tab —
  the Mermaid DAG + screenshots are what make the replay land on camera.
```
