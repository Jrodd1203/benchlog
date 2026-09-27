"""Dependencies shared by the route modules (importing them from app.py would be circular).

The server works on one project at a time: the one `benchlog serve` started with
(BENCHLOG_PROJECT), until the UI opens another from the projects folder (BENCHLOG_WORKSPACE).
"""

import os
from pathlib import Path
from typing import Annotated

from fastapi import Depends

from benchlog.core.project import Project

_open_project: Path | None = None


def open_project(root: Path | None) -> None:
    """Switch every route to the project at `root` (None: back to the one serve started with)."""
    global _open_project
    _open_project = root


def get_project() -> Project:
    if _open_project is not None:
        return Project.find(_open_project)
    start = os.environ.get("BENCHLOG_PROJECT")
    return Project.find(Path(start) if start else None)


def workspace() -> Path | None:
    """The projects folder the UI can browse, if one is configured."""
    path = os.environ.get("BENCHLOG_WORKSPACE")
    return Path(path) if path else None


ProjectDep = Annotated[Project, Depends(get_project)]
