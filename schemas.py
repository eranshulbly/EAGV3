"""Typed boundary contracts between the four roles."""
from datetime import datetime, timezone
from typing import Any, Literal, Optional

from pydantic import BaseModel, ConfigDict, Field, model_validator


# ---------- Internal canonical models (carried by agent6 loop) ----------

class MemoryItem(BaseModel):
    """One row in the Memory service. Persisted to state/memory.json."""
    id: str
    kind: Literal["fact", "preference", "tool_outcome", "scratchpad"]
    keywords: list[str] = Field(default_factory=list)
    descriptor: str
    value: dict[str, Any] = Field(default_factory=dict)
    artifact_id: Optional[str] = None
    source: str
    run_id: str
    goal_id: Optional[str] = None
    confidence: float = 1.0
    created_at: datetime = Field(default_factory=lambda: datetime.now(timezone.utc))


class Artifact(BaseModel):
    """Metadata for one byte blob in the artifact store."""
    id: str                    # "art:<sha256-prefix>"
    content_type: str
    size_bytes: int
    source: str
    descriptor: str


class Goal(BaseModel):
    """One bounded sub-task. Stable id; carried across Perception iterations."""
    id: str
    text: str
    done: bool = False
    attach_artifact_id: Optional[str] = None


class Observation(BaseModel):
    """What Perception emits each iteration."""
    goals: list[Goal] = Field(default_factory=list)

    @property
    def all_done(self) -> bool:
        return len(self.goals) > 0 and all(g.done for g in self.goals)

    def next_unfinished(self) -> Optional[Goal]:
        for g in self.goals:
            if not g.done:
                return g
        return None


class ToolCall(BaseModel):
    """One MCP tool invocation."""
    name: str
    arguments: dict[str, Any] = Field(default_factory=dict)


class DecisionOutput(BaseModel):
    """Decision returns EXACTLY ONE of answer or tool_call."""
    answer: Optional[str] = None
    tool_call: Optional[ToolCall] = None

    @model_validator(mode="after")
    def _exactly_one(self):
        has_answer = self.answer is not None and self.answer.strip() != ""
        has_tool = self.tool_call is not None
        if has_answer == has_tool:
            raise ValueError("DecisionOutput must populate exactly one of {answer, tool_call}")
        return self

    @property
    def is_answer(self) -> bool:
        return self.answer is not None and self.answer.strip() != ""

    @property
    def is_tool_call(self) -> bool:
        return self.tool_call is not None


# ---------- LLM-wire schemas (sent as JSON Schema in response_format) ----------
# These are deliberately NOT the internal shapes:
#   - Perception goals have no `id` field — positional identity prevents
#     the model from inventing stale identifiers.
#   - artifact_index is an int into MEMORY HITS, not a free-form `art:` string,
#     so the model can only attach an artifact it actually saw.

class PerceptionGoal(BaseModel):
    model_config = ConfigDict(extra="forbid")
    text: str
    done: bool
    artifact_index: Optional[int] = None


class PerceptionOutput(BaseModel):
    model_config = ConfigDict(extra="forbid")
    goals: list[PerceptionGoal]


class MemoryClassification(BaseModel):
    """Output of the memory.remember() classification LLM call."""
    model_config = ConfigDict(extra="forbid")
    kind: Literal["fact", "preference", "tool_outcome", "scratchpad"]
    keywords: list[str]
    descriptor: str
    value: dict[str, Any]
