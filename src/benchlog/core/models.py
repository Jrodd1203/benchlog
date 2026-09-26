"""Circuit schema. DRAFT for the team to edit at kickoff.

Everything else (CLI, server, checks, vision, frontend types) is built on these models.
Hole naming and strip connectivity live in `board.py`.
"""

from enum import Enum
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field, model_validator

from benchlog.core.board import TEMPLATES

# A hole name such as "A12" or "R+15". Validated against the board template on Circuit.
Hole = str


class _Model(BaseModel):
    # Typos in hand-written JSON should fail loudly.
    model_config = ConfigDict(extra="forbid")


class ComponentType(str, Enum):
    ESP32_DEVKIT_V1_30 = "esp32_devkit_v1_30"
    RESISTOR = "resistor"
    LED = "led"
    POTENTIOMETER = "potentiometer"


class Component(_Model):
    id: str = Field(description="Stable across revisions, e.g. 'esp32', 'r1', 'pot1'.")
    type: ComponentType
    model: str | None = Field(default=None, description="Part model, e.g. 'DOIT ESP32 DevKit V1'.")
    value: str | None = Field(default=None, description="Declared value, e.g. '220Ω', '10kΩ', 'red'.")
    pins: dict[str, Hole] = Field(description="Pin name -> hole it is inserted in.")


class Wire(_Model):
    id: str = Field(description="Stable across revisions, e.g. 'w5'.")
    a: Hole
    b: Hole
    color: str | None = None
    label: str | None = Field(default=None, description="Optional human name, e.g. 'pot signal'.")


class Circuit(_Model):
    schema_version: Literal[1] = 1
    board: str = Field(default="bb830", description="Breadboard template id.")
    components: list[Component] = []
    wires: list[Wire] = []

    @model_validator(mode="after")
    def _check_holes(self) -> "Circuit":
        template = TEMPLATES.get(self.board)
        if template is None:
            raise ValueError(f"unknown board template {self.board!r}")

        ids: set[str] = set()
        used: dict[Hole, str] = {}
        things = [(c.id, c.pins.items()) for c in self.components]
        things += [(w.id, [("a", w.a), ("b", w.b)]) for w in self.wires]
        for obj_id, pins in things:
            if obj_id in ids:
                raise ValueError(f"duplicate id {obj_id!r}")
            ids.add(obj_id)
            for pin, hole in pins:
                if not template.is_valid(hole):
                    raise ValueError(f"{obj_id}.{pin}: {hole!r} is not a hole on {self.board}")
                if hole in used:
                    raise ValueError(f"{obj_id}.{pin}: hole {hole} already used by {used[hole]}")
                used[hole] = f"{obj_id}.{pin}"
        return self


class ObservationKind(str, Enum):
    ADDED = "added"
    REMOVED = "removed"
    MOVED = "moved"


class ObservationStatus(str, Enum):
    PENDING = "pending"
    ACCEPTED = "accepted"
    REJECTED = "rejected"


class Observation(_Model):
    """One change a scan proposes relative to the last accepted circuit.

    `before`/`after` use the same shape for wires ({"a": ..., "b": ...}) and
    components (their `pins` map). Added = no `before`; removed = no `after`.
    """

    id: str
    kind: ObservationKind
    object_type: Literal["wire", "component"]
    object_id: str = Field(description="Existing id, or a proposed new id for additions.")
    before: dict[str, Hole] | None = None
    after: dict[str, Hole] | None = None
    confidence: float = Field(ge=0, le=1)
    uncertain_holes: list[Hole] = Field(default=[], description="Holes the user must confirm.")
    status: ObservationStatus = ObservationStatus.PENDING
