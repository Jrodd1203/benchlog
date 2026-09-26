"""JSON Schema for the types shared with the web UI, so TypeScript types can be generated from it."""

from pydantic.json_schema import models_json_schema

from benchlog.core.diff import CircuitDiff
from benchlog.core.models import Circuit, Observation
from benchlog.core.netlist import Netlist

SHARED_MODELS = {"circuit": Circuit, "observation": Observation, "diff": CircuitDiff, "netlist": Netlist}


def json_schema() -> dict:
    """One schema whose root references every shared model.

    Generators like json-schema-to-typescript only emit types reachable from the root,
    so the root is an object with one property per model.
    """
    refs, schema = models_json_schema(
        [(m, "serialization") for m in SHARED_MODELS.values()], ref_template="#/$defs/{model}"
    )
    # Field titles make generators emit an alias type per field (Id1, A1, ...); inline them instead.
    for definition in schema["$defs"].values():
        for prop in definition.get("properties", {}).values():
            prop.pop("title", None)
    return {
        "title": "Benchlog",
        "type": "object",
        "properties": {name: refs[(m, "serialization")] for name, m in SHARED_MODELS.items()},
        "required": list(SHARED_MODELS),
        "additionalProperties": False,
        **schema,
    }
