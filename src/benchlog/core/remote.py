"""Publishing a project to GitHub, where the web UI's Explore screen can open it. Owner: Person 1.

Pushing is plain git (`Repo.push`). GitHub only matters for the extras: creating the repository
for the user and tagging it with TOPIC (both through the `gh` CLI, if they have it), and turning
the remote into the `owner/repo` name the site opens. Explore lists every public repo with TOPIC.
"""

import re
import shutil
import subprocess
from pathlib import Path

# https://github.com/owner/repo(.git), git@github.com:owner/repo(.git), ssh://git@github.com/owner/repo
_GITHUB = re.compile(r"github\.com[:/]([A-Za-z0-9-]+)/([A-Za-z0-9._-]+?)(?:\.git)?/?$")

TOPIC = "benchlog-circuit"  # Explore's community list searches GitHub for this topic (web/src/api.ts)
SITE_ENV = "BENCHLOG_SITE"  # the deployed web UI, e.g. https://benchlog.up.railway.app


class RemoteError(RuntimeError):
    pass


def github_slug(url: str) -> str | None:
    """'owner/repo' for a GitHub remote URL, else None."""
    m = _GITHUB.search(url.strip())
    return f"{m[1]}/{m[2]}" if m else None


def site_link(slug: str, site: str | None) -> str | None:
    """The web UI page that opens this GitHub repo in Explore, if the site's address is known."""
    return f"{site.rstrip('/')}/?repo={slug}" if site else None


def has_gh() -> bool:
    return shutil.which("gh") is not None


def create_github_repo(root: Path, name: str) -> None:
    """Create a public GitHub repo called `name` (or 'owner/name') and point `origin` at it."""
    if not has_gh():
        raise RemoteError(
            "creating the repo needs the GitHub CLI (`brew install gh`, then `gh auth login`). "
            "Or create an empty public repo on github.com and run `benchlog push <its URL>`"
        )
    # Public: the web UI reads repos without logging in. The push itself is done by git afterwards.
    _gh(root, "repo", "create", name, "--public", "--source", str(root), "--remote", "origin")


def tag_github_repo(root: Path, slug: str) -> None:
    """Add TOPIC to the GitHub repo `slug`, so it shows up in Explore's community list."""
    if not has_gh():
        raise RemoteError("tagging the repo needs the GitHub CLI")
    _gh(root, "repo", "edit", slug, "--add-topic", TOPIC)


def _gh(root: Path, *args: str) -> None:
    result = subprocess.run(["gh", *args], cwd=root, capture_output=True, text=True, check=False)
    if result.returncode != 0:
        raise RemoteError((result.stderr or result.stdout).strip() or f"gh {' '.join(args[:2])} failed")
