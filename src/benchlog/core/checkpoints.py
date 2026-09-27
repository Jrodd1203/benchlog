"""Build-guide checkpoints: mark commits as annotated steps someone can follow to reproduce a build.

    .benchlog/checkpoints.json   gitignored: {sha -> {label, note}}
"""

import json
from pathlib import Path

from pydantic import BaseModel


class Checkpoint(BaseModel):
    sha: str
    label: str
    note: str = ""


class CheckpointStore:
    def __init__(self, state_dir: Path) -> None:
        self._path = state_dir / "checkpoints.json"

    def load(self) -> list[Checkpoint]:
        if not self._path.exists():
            return []
        return [Checkpoint(**c) for c in json.loads(self._path.read_text())]

    def by_sha(self) -> dict[str, Checkpoint]:
        return {c.sha: c for c in self.load()}

    def set(self, sha: str, label: str, note: str = "") -> Checkpoint:
        checkpoints = self.by_sha()
        checkpoints[sha] = Checkpoint(sha=sha, label=label, note=note)
        self._save(list(checkpoints.values()))
        return checkpoints[sha]

    def remove(self, sha: str) -> None:
        checkpoints = {s: c for s, c in self.by_sha().items() if s != sha}
        self._save(list(checkpoints.values()))

    def _save(self, checkpoints: list[Checkpoint]) -> None:
        self._path.parent.mkdir(parents=True, exist_ok=True)
        self._path.write_text(
            json.dumps([c.model_dump() for c in checkpoints], indent=2) + "\n"
        )
