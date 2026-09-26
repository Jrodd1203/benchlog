"""Canonical JSON I/O: sorted keys and stable ordering so git diffs stay readable."""

import json
from pathlib import Path

from pydantic import BaseModel

from benchlog.core.models import Circuit


def dump(model: BaseModel, path: Path) -> None:
    if isinstance(model, Circuit):
        model = model.model_copy(
            update={
                "components": sorted(model.components, key=lambda c: c.id),
                "wires": sorted(model.wires, key=lambda w: w.id),
            }
        )
    data = model.model_dump(mode="json", exclude_none=True)
    path.write_text(json.dumps(data, indent=2, sort_keys=True, ensure_ascii=False) + "\n")


def load_circuit(path: Path) -> Circuit:
    return Circuit.model_validate_json(path.read_text())
