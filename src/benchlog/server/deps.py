"""Dependencies shared by the route modules (importing them from app.py would be circular).

Which project a request is about: `?project=<id>` (a folder under PROJECTS_ROOT, as listed by
GET /api/projects; the web UI adds it to every call), else the one `benchlog serve` started in.
Every route uses this, so branches, PRs and checks follow the project picked in the UI too.
"""

import os
from pathlib import Path
from typing import Annotated

from fastapi import Depends, Query

from benchlog.core.project import Project, ProjectError

# Where the web UI's "New project" creates folders (and `benchlog demo seed` puts demo projects),
# one benchlog project (git repo) per subdirectory.
PROJECTS_ROOT = Path(os.environ.get("BENCHLOG_PROJECTS_ROOT", Path.home() / "benchlog-projects"))


def get_project(
    project: Annotated[
        str | None,
        Query(description="Project id (a folder slug from GET /api/projects). Omit for the single project `benchlog serve` was started in."),
    ] = None,
) -> Project:
    if project is not None:
        # `project` is a client-supplied query param, not something _slugify has sanitized (that
        # only guards names *we* turn into folders in create_project) — reject anything that could
        # escape PROJECTS_ROOT (path separators, "..") before it ever reaches the filesystem.
        if not project or any(c in project for c in "/\\") or project in (".", ".."):
            raise ProjectError(f"invalid project id {project!r}")
        path = PROJECTS_ROOT / project
        if path.resolve().parent != PROJECTS_ROOT.resolve() or not path.is_dir():
            raise ProjectError(f"no project {project!r} under {PROJECTS_ROOT}")
        return Project.find(path)
    start = os.environ.get("BENCHLOG_PROJECT")
    return Project.find(Path(start) if start else None)


ProjectDep = Annotated[Project, Depends(get_project)]
