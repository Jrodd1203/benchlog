"""Breadboard templates: which holes exist and which are connected internally.

Hole names (board viewed with row 1 at the top, column A on the left):
- Terminal holes: column letter + row, e.g. "A1", "J63". A-E in a row are one strip, F-J another.
- Rail holes: rail name + index, e.g. "L+1", "R-50". L = left rails (next to A), R = right rails (next to J).
"""

import re
from dataclasses import dataclass

LEFT_COLUMNS = "ABCDE"
RIGHT_COLUMNS = "FGHIJ"
RAILS = ("L+", "L-", "R+", "R-")

_HOLE_RE = re.compile(r"^(?:(?P<col>[A-J])(?P<row>\d+)|(?P<rail>[LR][+-])(?P<idx>\d+))$")


@dataclass(frozen=True)
class BreadboardTemplate:
    id: str
    rows: int
    rail_length: int
    # Some boards break each rail in the middle; the halves are then separate nodes.
    rail_split: bool = False

    def is_valid(self, hole: str) -> bool:
        m = _HOLE_RE.match(hole)
        if not m:
            return False
        if m["col"]:
            return 1 <= int(m["row"]) <= self.rows
        return 1 <= int(m["idx"]) <= self.rail_length

    def strip(self, hole: str) -> str:
        """Name of the internally connected strip a hole belongs to, e.g. "21L", "21R", "R+"."""
        m = _HOLE_RE.match(hole)
        if not m or not self.is_valid(hole):
            raise ValueError(f"{hole!r} is not a hole on {self.id}")
        if m["col"]:
            side = "L" if m["col"] in LEFT_COLUMNS else "R"
            return f"{m['row']}{side}"
        if self.rail_split:
            half = "a" if int(m["idx"]) <= self.rail_length // 2 else "b"
            return f"{m['rail']}.{half}"
        return m["rail"]


# Full-size 830-point board: 63 rows x 10 columns + 4 rails of 50 holes.
BB830 = BreadboardTemplate(id="bb830", rows=63, rail_length=50)

TEMPLATES = {t.id: t for t in (BB830,)}
