# EAG V3 Session 7 — Travel Destinations RAG

A retrieval-aware agent built on the Session 7 four-role architecture
(Perception → Decision → Action → Memory). This submission preserves
the base agent, passes all 8 canonical S7 worked queries (A–H, with C
and F running twice for cross-run memory), and adds a 57-PDF
travel-guide corpus, a new `index_pdf` MCP tool for ingesting binary
PDFs, and 5 custom queries that demonstrate semantic recall over the
travel corpus with paired no-corpus comparisons.

## What this submission adds

| Change | File | Why |
| --- | --- | --- |
| `index_pdf` MCP tool | [mcp_server.py:392-454](mcp_server.py#L392-L454) | Ingest PDFs (binary) rather than only `.md` / `.txt`. Reuses `_memory.add_fact` so persistence is identical to `index_document`. |
| Page-aware chunker | [mcp_server.py:401-432](mcp_server.py#L401-L432) | Each chunk records the page range it spans (`page_start`, `page_end`, `pages` label) so Decision can cite *"see Iceland guide pp.5-6"* without an extra tool call. |
| `pypdf` dependency | [pyproject.toml](pyproject.toml) | PDF text extraction. |
| `add_fact` embeds chunk text | [memory.py:362-372](memory.py#L362-L372) | Without this, chunks are embedded only by their ~200-char descriptor (path + 120-char preview) and most chunk content is invisible to retrieval. See [findings](#honest-findings-about-dense-only-retrieval). |
| 57-PDF travel corpus | [sandbox/travel/](sandbox/travel/) | Sourced from Wikivoyage via [build_corpus.py](build_corpus.py). 1,682 pages, ~2,128 chunks, 768-dim embeddings via Ollama / `nomic-embed-text`. |

The S6/S7 agent loop, the four Pydantic role contracts, and
[perception.py](perception.py) are unchanged.

## Architecture gate — tool-blindness in Perception

The Session 7 reference explicitly forbids MCP tool names in Perception's
SYSTEM string. Adding `index_pdf` (12th tool) does not regress this
property. Programmatic check:

```
$ python -c "
import re, perception
src = open('mcp_server.py').read()
tool_names = re.findall(r'@mcp\.tool\(\)\s*\n(?:async\s+)?def\s+(\w+)', src)
hits = [t for t in tool_names if t.lower() in perception.SYSTEM.lower()]
print('tools:', tool_names)
print('hits in perception.SYSTEM:', hits)
"

tools: ['web_search', 'fetch_url', 'get_time', 'currency_convert',
        'read_file', 'list_dir', 'create_file', 'update_file', 'edit_file',
        'index_document', 'index_pdf', 'search_knowledge']
hits in perception.SYSTEM: []
```

Selection guidance lives in the docstrings: `index_pdf`'s docstring
([mcp_server.py:438](mcp_server.py#L438)) directs the model away for
`.md`/`.txt` files (use `index_document`), and `index_document`'s
docstring ([mcp_server.py:344](mcp_server.py#L344)) reciprocally points
PDFs at `index_pdf`. Decision learns the boundary from those docstrings;
Perception never sees a tool name.

## Corpus manifest

63 destinations chosen for thematic spread;The full list of 63 lives in
[build_corpus.py](build_corpus.py); the 57 indexed:

| Theme | Destinations |
| --- | --- |
| Iconic cities | Paris, London, New York City, Rome, Barcelona, Istanbul, Cairo, Bangkok, Singapore, Dubai, Sydney, Rio de Janeiro |
| Beach / island | Bali, Maldives, Santorini, Phuket, Maui, Goa, Mykonos, Boracay |
| Mountain / nature | Banff, Queenstown, Interlaken, Cusco, Reykjavík, Kathmandu, Aspen |
| Desert / unique | Marrakech, Petra, San Pedro de Atacama, Death Valley |
| Cultural / historical | Kyoto, Varanasi, Jerusalem, Athens, Prague, Florence, Lhasa, Hoi An, Bagan |
| Adventure / off-beat | Antarctica, Madagascar, Bhutan, Mongolia, Greenland, Galápagos, Iceland |
| Foodie | Lyon, Lima, Bologna, San Sebastián, New Orleans, Penang, Chengdu |
| Romance / serene | Bruges, Hokkaido, Lofoten |

Corpus stats: **57 PDFs / ~1,580 pages of
extractable text / 2,128 chunks** at 400-word windows with 80-word
overlap. The full FAISS index occupies 4.3 MB on disk; `memory.json`
holds the chunk text and metadata (~50 MB).

## The 8 base S7 worked queries (A–H)

The Session 7 reference defines 8 canonical queries that exercise the
agent's full surface (artifact attach, multi-goal decomposition,
cross-run memory, multi-source synthesis, single- and multi-document
indexing, synonym recall, cross-document compare). All 10 runs (C and F
have two runs each) were executed in canonical order against a freshly
wiped state — see [run_base_queries.py](run_base_queries.py). Each
agent run's stdout is captured under [traces/](traces/) as
`base_<key>.txt`.

| Query | Iter | Trace | Final answer (1-line summary) |
| --- | --- | --- | --- |
| **A** — Shannon Wikipedia (artifact attach) | 3 | [base_A.txt](traces/base_A.txt) | Born 1916-04-30, died 2001-02-24; founder of Information Theory (1948 paper, Source Coding Theorem, Noisy-Channel Coding Theorem). |
| **B** — Tokyo activities + Sat weather (multi-goal) | 6 | [base_B.txt](traces/base_B.txt) | Pulled Sat/Sun forecast (sunny, 27–28 °C), recommended outdoor family activities aligned with the weather. |
| **C run 1** — Save mom's birthday + reminders (durable memory) | 5 | [base_C_run1.txt](traces/base_C_run1.txt) | Saved 15 May 2026 fact; created sandbox reminder files for 1 May (two weeks before) and 15 May. |
| **C run 2** — Recall mom's birthday (cross-run) | 3, **0 tool calls** | [base_C_run2.txt](traces/base_C_run2.txt) | Answered "15 May 2026" purely from memory — the durability demonstration. |
| **D** — asyncio best practices (multi-source synthesis) | 12 | [base_D.txt](traces/base_D.txt) | Numbered list of advice (`asyncio.run`, gather vs as_completed, cancellation, etc.) synthesised from search results. |
| **E** — Index attention.md + extract contributions (single-doc index) | 9 | [base_E.txt](traces/base_E.txt) | Three Transformer contributions: sole reliance on attention, improved parallelization, SOTA quality with less training time. |
| **F run 1** — Index all .md under papers/ + count (multi-doc index) | 8 | [base_F_run1.txt](traces/base_F_run1.txt) | 15 chunks total (attention 3, cot 3, react 3, dpo 3, lora 3). |
| **F run 2** — Cross-run recall on chain-of-thought (FAISS persistence) | 9 | [base_F_run2.txt](traces/base_F_run2.txt) | Pulled CoT-related findings from ReAct + CoT chunks: reasoning traces improve performance + interpretability. |
| **G** — Credit assignment problem (synonym recall — the strongest pedagogical case) | 12 | [base_G.txt](traces/base_G.txt) | Surfaced four papers' takes (Attention/self-attention paths, LoRA low-rank subspace, ReAct reasoning traces, DPO direct optimisation) — and "credit assignment" appears in **none** of the indexed chunks. Pure semantic recall. |
| **H** — Compare ReAct vs CoT on intermediate reasoning (cross-doc) | 4 | [base_H.txt](traces/base_H.txt) | CoT = internal step-by-step reasoning; ReAct = interleaves reasoning with external tool actions (e.g. Wikipedia API). |

Notes:
- **G is the load-bearing query** — the phrase "credit assignment" does
  not occur in any of the 5 paper files (grep returns zero hits), but
  the dense embedding surfaces chunks from four of them that discuss the
  same idea under different vocabulary (backprop through reasoning, low-rank
  adaptation, reasoning trace credit, direct preference optimisation).
  This is the demonstration that vector retrieval beats keyword retrieval.
- **C run 2** uses **zero tool calls**, proving the FAISS-backed memory
  service surfaces the prior-run fact on iter 1's `memory.read`.
- **F run 2** runs in a fresh agent process against a populated `state/`
  directory — disk-backed cross-process persistence demonstrated.

## The 5 custom queries

Methodology: each query runs once against the indexed corpus, then state
is wiped (`memory.json`, `index.faiss`, `index_ids.json`) and the same
query runs against an empty index. Traces saved under
[traces/](traces/). Footnote on residual memory below.

### Q1 — Quiet honeymoon away from crowds *(semantic recall)*

> *"Across the destinations I have indexed, which ones are well-suited
> for a quiet honeymoon away from crowds?"*

Neither "honeymoon" nor "quiet" appears as a Wikivoyage heading. The
chunks that answer talk about *secluded*, *intimate*, *romantic*,
*peaceful* — semantic match without lexical overlap.

| | With corpus ([trace](traces/Q1_with_corpus.txt)) | Without corpus ([trace](traces/Q1_without_corpus.txt)) |
|---|---|---|
| Iterations | 3 | 3 |
| Answer | Bali (Sanur, Jimbaran, Ubud); Phuket (Kata, Karon, Rawai, Nai Yang); explicit "avoid Patong / avoid Kuta" section sourced from the same chunks. | "no indexed quiet honeymoon destinations available in the knowledge base." Agent gives up gracefully. |
| Provenance | Wikivoyage chunks for Bali.pdf and Phuket.pdf | none |

### Q2 — Solo female travelers on a tight budget *(semantic recall)*

> *"Which of my indexed destinations are best for solo female travelers
> on a tight budget?"*

"Solo female" rarely appears verbatim in Wikivoyage; the chunks that
answer talk about *stay safe*, *respectful dress*, *hostels*,
*budget-friendly*.

| | With corpus ([trace](traces/Q2_with_corpus.txt)) | Without corpus ([trace](traces/Q2_without_corpus.txt)) |
|---|---|---|
| Iterations | 3 | 5 |
| Answer | Bangkok, Bali, London, Prague (top picks). Explicit "avoid Petra (safety concerns for female travelers), avoid Greenland (cost)" section pulled from the actual chunks. | Fell back to `web_search` → blog post. Recommended Vietnam, Myanmar, Georgia, Bosnia, Rwanda — **none of which are in the indexed corpus**. The agent silently ignored the "indexed destinations" constraint. |


### Q3 — First-time visitor to Kyoto *(single-PDF deep dive)*

> *"Based on my indexed destinations, what should a first-time visitor
> see and do in Kyoto's traditional districts, temples, and shrines?"*

This query was reworded mid-build — see
[honest findings](#honest-findings-about-dense-only-retrieval).

| | With corpus ([trace](traces/Q3_with_corpus.txt)) | Without corpus ([trace](traces/Q3_without_corpus.txt)) |
|---|---|---|
| Iterations | 3 | 7 |
| Answer | Kinkaku-ji; 14 of 17 UNESCO sites; self-guided walks with bus routes; Zen meditation at Taizo-in and Shunko-in (specific sub-temples that exist in Kyoto.pdf); cherry blossom season. | Fell back to `web_search` → insidekyoto.com. Detailed answer (Fushimi Inari, Kiyomizu-dera, Ryoan-ji, Arashiyama districts, kimono rental, ryokan…) but **all from one external blog**, no corpus provenance. |


### Q4 — Geothermal landscapes *(multi-destination semantic recall)*

> *"Across my indexed destinations, which ones are best for someone who
> loves geothermal landscapes — hot springs, geysers, and volcanic
> terrain?"*

Wikivoyage's Atacama chunks describe *"loud steams and geysers,
shortly after that a nice warm thermal pool"* — semantic match without
the word "geothermal." Iceland.pdf has Strokkur, Mývatn,
Landmannalaugar.

| | With corpus ([trace](traces/Q4_with_corpus.txt)) | Without corpus ([trace](traces/Q4_without_corpus.txt)) |
|---|---|---|
| Iterations | 3 | 4 |
| Answer | Iceland and San Pedro de Atacama. Specifics: Strokkur geyser (5-10 min cycle), Gullfoss, Þingvellir tectonic boundary, Blue Lagoon, Reykjadalur, Lake Mývatn, Landmannalaugar — most cited from the corpus chunks; some web detail leaked through a residual artifact (see [methodology note](#methodology-note-residual-memory-between-without-corpus-runs)). | "no destinations were found in the indexed knowledge base." Agent suggests indexing content like Iceland, Yellowstone, NZ, Japan first. Honest empty-state response. |

### Q5 — Vegetarian-friendly destinations *(semantic recall)*

> *"Across my indexed destinations, which ones can a vegetarian
> comfortably eat at without struggling to find food?"*

The phrase "comfortably eat without struggling" does not appear in any
chunk; the answer requires semantic match against chunks describing
*"vegetarian restaurants"*, *"oyster sauce / lard caveats"*, *"shojin
ryori"*, *"hostel that serves vegetarian and vegan meals"*.

| | With corpus ([trace](traces/Q5_with_corpus.txt)) | Without corpus ([trace](traces/Q5_without_corpus.txt)) |
|---|---|---|
| Iterations | 3 | 3 |
| Answer | **Varanasi** (Aloo Tikia Chat, Madhur Jalpan sweets, specific vegetarian hostels), New York City (hawker-style vegan options), Paris (caveats about French definition of "vegetarian" including fish), Mongolia (limited options, must request in advance), Singapore (warning about hidden oyster sauce and lard in Chinese restaurants). | Singapore (multicultural), Tokyo (shojin ryori — Japanese Buddhist cuisine), **Vietnam**, **Morocco**, **Myanmar**. **Varanasi is missing entirely** despite being the canonical vegetarian destination — agent has no way to know what's in the index, can only generalize from training data. Cites destinations not in the corpus (Vietnam, Morocco). |


## How to reproduce

Prerequisites:
- `uv` (project manager)
- Ollama running locally with `nomic-embed-text` pulled:
  ```
  ollama pull nomic-embed-text
  ```
- The bundled `llm_gatewayV7` running on port 8107 (auto-launched by
  [gateway.py:33](gateway.py#L33) if not up). `GEMINI_API_KEY` set in
  the gateway's env if you want the Gemini fallback (not required while
  Ollama is healthy).

Steps:
```
# 1. Build the corpus (downloads 63 PDFs from Wikivoyage with politeness sleep)
uv run build_corpus.py

# 2. Bulk-index the corpus into FAISS via the same code path the index_pdf MCP tool uses
uv run index_corpus.py

# 3. Run the 5 custom queries (with-corpus + without-corpus)
uv run run_custom_queries.py

# 4. (Optional) Re-run the 8 base S7 queries (A-H) — wipes state, runs A→H sequentially, restores corpus
uv run run_base_queries.py
```

`run_custom_queries.py` backs up the indexed state to
`state_corpus_backup/` before any wipe, and restores it after the
without-corpus pass. Traces land in `traces/` as
`Q<N>_with_corpus.txt` and `Q<N>_without_corpus.txt`.

For interactive use:
```
uv run agent7.py "What should a first-time visitor see and do in Kyoto?"
```

## File layout

```
S7code/
├── agent7.py                 # entrypoint (unchanged from S7 base)
├── perception.py             # tool-blind, grep-verified
├── decision.py, action.py    # unchanged
├── memory.py                 # patched: add_fact embeds chunk text
├── mcp_server.py             # +index_pdf with page-provenance
├── gateway.py                # path to llm_gatewayV7 was parents[2]; fixed to parent
├── llm_gatewayV7/            # the V7 gateway (in-tree)
├── build_corpus.py           # Wikivoyage PDF downloader
├── index_corpus.py           # bulk indexer using the index_pdf code path
├── run_custom_queries.py     # 5-query runner with state backup/wipe/restore
├── run_base_queries.py       # 8 base S7 query runner (A-H, with C/F two-run sequences)
├── sandbox/travel/           # 63 .pdf files, ~30 MB total
├── sandbox/papers/           # 5 reference .md papers used by base queries E-H
├── state/                    # memory.json, index.faiss, index_ids.json
├── state_corpus_backup/      # snapshot taken after indexing
└── traces/                   # base_{A,B,C_run1,C_run2,D,E,F_run1,F_run2,G,H}.txt
                              # Q{1..5}_{with,without}_corpus.txt
```
