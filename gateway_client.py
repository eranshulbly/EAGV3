"""Tiny shim that loads `LLM` from llm_gatewayV3/client.py without putting
that directory on sys.path. Putting the gateway dir on sys.path would shadow
our local `schemas.py` (llm_gatewayV3 has one too)."""
import importlib.util
from pathlib import Path

_CLIENT_PATH = Path(__file__).parent / "llm_gatewayV3" / "client.py"

_spec = importlib.util.spec_from_file_location("llm_gatewayV3_client", _CLIENT_PATH)
_mod = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(_mod)

LLM = _mod.LLM  # type: ignore[attr-defined]
ask = _mod.ask  # type: ignore[attr-defined]
