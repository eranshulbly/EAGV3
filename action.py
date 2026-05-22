"""Action — pure MCP dispatch. No LLM call. Threshold-based artifact store push.

Three behaviours:
  1. Refuse `art:` strings as paths/urls (TINY models occasionally hallucinate
     these into tool args). Return an error string instead of dispatching.
  2. Dispatch via `session.call_tool(name, arguments=...)`; collapse text blocks.
  3. If result is larger than ARTIFACT_THRESHOLD_BYTES, push to artifact store
     and return a short descriptor; otherwise return the raw text.
"""
from typing import Any, Optional

from mcp import ClientSession

from artifacts import ArtifactStore
from schemas import ToolCall


ARTIFACT_THRESHOLD_BYTES = 4096


def _has_artifact_handle(arguments: dict[str, Any]) -> Optional[str]:
    """Return the offending key if any string-valued argument starts with 'art:'."""
    for k, v in arguments.items():
        if isinstance(v, str) and v.startswith("art:"):
            return k
        if isinstance(v, list):
            for item in v:
                if isinstance(item, str) and item.startswith("art:"):
                    return k
    return None


async def execute(
    session: ClientSession,
    tool_call: ToolCall,
    artifacts: ArtifactStore,
) -> tuple[str, Optional[str]]:
    """Dispatch one MCP tool call. Returns (descriptor, artifact_id_or_None)."""
    bad = _has_artifact_handle(tool_call.arguments)
    if bad is not None:
        return (
            f"ERROR: argument {bad!r} starts with 'art:' but tools require real "
            f"paths/URLs. Artifact handles are internal references; the bytes "
            f"are provided to Decision via ATTACHED ARTIFACTS, not as tool args.",
            None,
        )

    try:
        result = await session.call_tool(tool_call.name, arguments=tool_call.arguments)
    except Exception as e:
        return (f"ERROR calling {tool_call.name}: {e}", None)

    text_parts: list[str] = []
    for block in (result.content or []):
        t = getattr(block, "text", None)
        if t:
            text_parts.append(t)
    text = "\n".join(text_parts) if text_parts else ""
    if getattr(result, "isError", False):
        text = f"ERROR: {text}"

    blob = text.encode("utf-8")
    if len(blob) > ARTIFACT_THRESHOLD_BYTES:
        art_id = artifacts.put(
            blob,
            content_type="text/plain",
            source=tool_call.name,
            descriptor=f"{tool_call.name} result ({len(blob)} bytes)",
        )
        preview = text[:240].replace("\n", " ")
        return (
            f"[artifact {art_id}, {len(blob)} bytes] preview: {preview}",
            art_id,
        )

    return (text, None)
