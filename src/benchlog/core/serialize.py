"""Canonical JSON I/O: sorted keys and stable ordering so git diffs stay readable."""

import json
from pathlib import Path

from pydantic import BaseModel


def dump(model: BaseModel, path: Path) -> None:
    data = model.model_dump(mode="json")
    path.write_text(json.dumps(data, indent=2, sort_keys=True) + "\n")
