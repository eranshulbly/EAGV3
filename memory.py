"""Typed memory service. Reads are pure keyword search (no LLM); writes
classify via one Gemini call (remember) or are deterministic (record_outcome)."""
import json
import re
import sys
import uuid
from datetime import datetime, timezone
from pathlib import Path
from typing import Optional

from gateway_client import LLM
from schemas import MemoryClassification, MemoryItem, ToolCall


# Small built-in stopword list. Enough for the 4 target queries.
STOPWORDS = {
    "a", "an", "and", "are", "as", "at", "be", "by", "for", "from", "has", "have",
    "he", "her", "him", "his", "i", "in", "is", "it", "its", "me", "my", "of",
    "on", "or", "she", "that", "the", "their", "them", "they", "this", "to",
    "was", "we", "were", "with", "you", "your", "what", "when", "where", "who",
    "how", "do", "does", "did", "can", "could", "would", "should", "will",
    "near", "also", "but", "so", "if", "then", "than", "into", "out", "up",
    "down", "off", "over", "under", "about", "after", "before", "any", "all",
    "some", "no", "not", "now", "just", "only", "very", "too", "more", "most",
    "less", "much", "many", "tell", "give", "make", "want", "need", "please",
    "hey", "hi", "assistant", "remind", "remember", "find", "check", "search",
    "read", "fetch",
}


_TOKEN_RE = re.compile(r"[a-z0-9]+")


def _tokenize(text: str) -> set[str]:
    if not text:
        return set()
    toks = _TOKEN_RE.findall(text.lower())
    return {t for t in toks if t not in STOPWORDS and len(t) > 1}


class Memory:
    def __init__(self, path: Path | str = "state/memory.json"):
        self.path = Path(path)
        self.path.parent.mkdir(parents=True, exist_ok=True)
        self._items: list[MemoryItem] = []
        self._loaded = False
        self._llm = LLM()

    # ---- persistence ----
    def _load(self) -> None:
        if self._loaded:
            return
        if self.path.exists():
            raw = json.loads(self.path.read_text() or "[]")
            self._items = [MemoryItem.model_validate(r) for r in raw]
        else:
            self._items = []
        self._loaded = True

    def _save(self) -> None:
        rows = [m.model_dump(mode="json") for m in self._items]
        self.path.write_text(json.dumps(rows, indent=2, default=str))

    # ---- reads (no LLM) ----
    def read(
        self,
        query: str,
        history: Optional[list[dict]] = None,
        kinds: Optional[list[str]] = None,
        top_k: int = 8,
    ) -> list[MemoryItem]:
        """Keyword overlap across item.keywords + tokens(descriptor)."""
        self._load()
        q_tokens = _tokenize(query)
        # Mix in tokens from the most recent few history entries — keeps
        # the relevance window moving with the conversation.
        if history:
            for ev in history[-3:]:
                text = ev.get("text") or ev.get("result_descriptor") or ""
                q_tokens |= _tokenize(text)

        scored: list[tuple[float, MemoryItem]] = []
        for m in self._items:
            if kinds and m.kind not in kinds:
                continue
            item_tokens = set(t.lower() for t in m.keywords) | _tokenize(m.descriptor)
            overlap = len(q_tokens & item_tokens)
            if overlap == 0:
                continue
            # Recency tiebreaker — newer items rank slightly higher.
            recency = m.created_at.timestamp() / 1e12
            scored.append((overlap + recency, m))

        scored.sort(key=lambda x: -x[0])
        return [m for _, m in scored[:top_k]]

    def filter(
        self,
        kinds: Optional[list[str]] = None,
        goal_id: Optional[str] = None,
        recent: Optional[int] = None,
    ) -> list[MemoryItem]:
        self._load()
        out = list(self._items)
        if kinds:
            out = [m for m in out if m.kind in kinds]
        if goal_id is not None:
            out = [m for m in out if m.goal_id == goal_id]
        out.sort(key=lambda m: m.created_at, reverse=True)
        if recent:
            out = out[:recent]
        return out

    # ---- writes ----
    def remember(
        self,
        raw_text: str,
        *,
        source: str,
        run_id: str,
        goal_id: Optional[str] = None,
    ) -> Optional[MemoryItem]:
        """Classify free-form text via ONE Gemini call and persist.

        Returns the persisted MemoryItem, or None if classification failed
        (we never block the agent loop on a memory classification error)."""
        self._load()
        system = (
            "You classify a user statement into a single durable memory item. "
            "Return JSON matching the schema. Choose kind:\n"
            "- 'fact' for objective truths about entities (dates, addresses, "
            "names). Example: 'Mom's birthday is 15 May 2026' -> "
            "{kind:'fact', value:{entity:'mom', attribute:'birthday', "
            "value:'2026-05-15'}}\n"
            "- 'preference' for the user's stated likes/dislikes/habits.\n"
            "- 'scratchpad' for transient questions or requests that are not "
            "themselves durable knowledge (e.g. 'find me 3 restaurants').\n"
            "- 'tool_outcome' is reserved for tool results; do NOT pick it here.\n"
            "keywords: lowercase tokens a future user query is likely to match. "
            "Include the canonical entity, attribute, and key values "
            "(e.g. ['mom','birthday','may','2026']).\n"
            "descriptor: ONE short sentence stating the fact/preference."
        )
        cls: Optional[MemoryClassification] = None
        # Try Gemini first (explicit provider). If it 5xxs (free-tier quota,
        # transient), retry without the override so the worker-pool failover
        # can pick a different provider.
        for attempt in ("provider_g", "auto_only"):
            kwargs = dict(
                messages=[{"role": "user", "content": raw_text}],
                system=system,
                temperature=1.0,
                max_tokens=400,
                response_format={
                    "type": "json_schema",
                    "schema": MemoryClassification.model_json_schema(),
                    "name": "classify",
                    "strict": True,
                },
            )
            if attempt == "provider_g":
                kwargs["provider"] = "g"
            else:
                kwargs["auto_route"] = "memory"
            try:
                result = self._llm.chat(**kwargs)
                parsed = result.get("parsed")
                if parsed:
                    cls = MemoryClassification.model_validate(parsed)
                    break
            except Exception as e:
                print(
                    f"[memory.remember] attempt={attempt} failed: {e}",
                    file=sys.stderr,
                )
                continue
        if cls is None:
            print("[memory.remember] all attempts failed; skipping write", file=sys.stderr)
            return None

        # Scratchpad classifications for transient asks are not worth keeping
        # across runs — drop them to keep the durable store clean.
        if cls.kind == "scratchpad":
            return None

        item = MemoryItem(
            id=uuid.uuid4().hex[:12],
            kind=cls.kind,
            keywords=[k.lower() for k in cls.keywords],
            descriptor=cls.descriptor,
            value=cls.value,
            artifact_id=None,
            source=source,
            run_id=run_id,
            goal_id=goal_id,
            confidence=1.0,
            created_at=datetime.now(timezone.utc),
        )
        self._items.append(item)
        self._save()
        return item

    def record_outcome(
        self,
        *,
        tool_call: ToolCall,
        result_text: str,
        artifact_id: Optional[str],
        run_id: str,
        goal_id: Optional[str],
    ) -> MemoryItem:
        """Persist one tool dispatch as kind='tool_outcome'. No LLM."""
        self._load()
        kw: set[str] = {tool_call.name.lower()}
        for v in tool_call.arguments.values():
            if isinstance(v, str):
                kw |= _tokenize(v)
        kw |= _tokenize(result_text[:300])
        descriptor = f"{tool_call.name}(...) -> {result_text[:120]}"

        item = MemoryItem(
            id=uuid.uuid4().hex[:12],
            kind="tool_outcome",
            keywords=sorted(kw),
            descriptor=descriptor,
            value={
                "tool": tool_call.name,
                "arguments": tool_call.arguments,
                "result_preview": result_text[:300],
            },
            artifact_id=artifact_id,
            source="action.execute",
            run_id=run_id,
            goal_id=goal_id,
            confidence=1.0,
            created_at=datetime.now(timezone.utc),
        )
        self._items.append(item)
        self._save()
        return item
