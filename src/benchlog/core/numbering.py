"""Row numbers as printed on the user's breadboard vs benchlog's internal A1..J63.

Boards don't all label their columns the same way: some start at 0, some leave the end columns
unlabeled (e.g. the 2nd column is labeled 0, so the 1st would be -1). `first_row` is the label the
1st column carries (or would carry). benchlog keeps A1..J63 everywhere inside (circuits, checks, the
API, the UI's geometry) and only translates what people read and type, so the numbers match the
board in front of them. Rails aren't numbered on boards, so they're left alone.
"""

import re

_TERMINAL = re.compile(r"\b([A-J])(-?\d{1,2})\b")
ROWS = 63


def to_printed(text: str, first_row: int) -> str:
    """Rewrite internal hole names in `text` (A1, G30, ...) as printed on the board."""
    offset = first_row - 1
    if offset == 0:
        return text

    def swap(m: re.Match) -> str:
        row = int(m[2])
        return f"{m[1]}{row + offset}" if 1 <= row <= ROWS else m[0]

    return _TERMINAL.sub(swap, text)


def from_printed(hole: str, first_row: int) -> str:
    """A hole name as printed on the board (e.g. G30) to benchlog's internal name."""
    m = _TERMINAL.fullmatch(hole.strip().upper())
    if not m or first_row == 1:
        return hole.strip().upper()
    return f"{m[1]}{int(m[2]) - (first_row - 1)}"


def first_row_for(label: int, column: int) -> int:
    """`first_row` for a board whose `column`-th column (counting from 1) is labeled `label`."""
    return label - column + 1


def describe_numbering(first_row: int) -> str:
    """e.g. "the 2nd column is labeled 0" for first_row -1."""
    if first_row >= 0:
        return f"the 1st column is labeled {first_row}"
    column = 1 - first_row
    suffix = {1: "st", 2: "nd", 3: "rd"}.get(column, "th")
    return f"the {column}{suffix} column is labeled 0"


# Layouts the calibration window's `n` key cycles through.
COMMON = (1, 0, -1)
