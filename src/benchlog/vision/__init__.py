"""Vision integration layer for benchlog. Owner: Person 4.

This module is the thin integration layer between the vision pipeline and the
rest of the system. Heavy lifting (board detection, warp, occupancy scanning)
lives in capture.py and bb830_layout.py; this module converts their output
into the Observation objects that core/ consumes.
"""

from __future__ import annotations

from benchlog.core.models import Observation, ObservationKind, ObservationStatus


def occupancy_diff(
    prev_occupied: list[str],
    curr_occupied: list[str],
    confidence: float = 0.7,
) -> list[Observation]:
    """Diff two occupied-hole lists from ScanResult. Returns vision-only Observations."""
    prev = set(prev_occupied)
    curr = set(curr_occupied)
    obs = []
    for i, hole in enumerate(curr - prev):
        obs.append(Observation(
            id=f"vis_add_{i}",
            kind=ObservationKind.ADDED,
            object_type="wire",
            object_id=f"hole_{hole}",
            before=None,
            after={"hole": hole},
            confidence=confidence,
            status=ObservationStatus.PENDING,
        ))
    for i, hole in enumerate(prev - curr):
        obs.append(Observation(
            id=f"vis_rem_{i}",
            kind=ObservationKind.REMOVED,
            object_type="wire",
            object_id=f"hole_{hole}",
            before={"hole": hole},
            after=None,
            confidence=confidence,
            status=ObservationStatus.PENDING,
        ))
    return obs
