"""Content-addressable byte store. Raw tool outputs live here; Memory holds the handle."""
import hashlib
import json
from pathlib import Path

from schemas import Artifact


HANDLE_PREFIX = "art:"
HASH_HEX_LEN = 16   # 64 bits — enough for collision-free per-run dedup


class ArtifactStore:
    def __init__(self, root: Path | str = "state/artifacts"):
        self.root = Path(root)
        self.root.mkdir(parents=True, exist_ok=True)

    def _handle_to_paths(self, art_id: str) -> tuple[Path, Path]:
        if not art_id.startswith(HANDLE_PREFIX):
            raise ValueError(f"not an artifact handle: {art_id!r}")
        prefix = art_id[len(HANDLE_PREFIX):]
        return self.root / f"{prefix}.bin", self.root / f"{prefix}.json"

    def put(self, blob: bytes, *, content_type: str, source: str, descriptor: str) -> str:
        digest = hashlib.sha256(blob).hexdigest()[:HASH_HEX_LEN]
        art_id = f"{HANDLE_PREFIX}{digest}"
        bin_path, meta_path = self._handle_to_paths(art_id)
        if not bin_path.exists():
            bin_path.write_bytes(blob)
        meta = Artifact(
            id=art_id,
            content_type=content_type,
            size_bytes=len(blob),
            source=source,
            descriptor=descriptor,
        )
        meta_path.write_text(meta.model_dump_json(indent=2))
        return art_id

    def exists(self, art_id: str) -> bool:
        try:
            bin_path, _ = self._handle_to_paths(art_id)
        except ValueError:
            return False
        return bin_path.exists()

    def get_bytes(self, art_id: str) -> bytes:
        bin_path, _ = self._handle_to_paths(art_id)
        return bin_path.read_bytes()

    def get_meta(self, art_id: str) -> Artifact:
        _, meta_path = self._handle_to_paths(art_id)
        return Artifact.model_validate(json.loads(meta_path.read_text()))
