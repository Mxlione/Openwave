"""The starter issues must stay true.

An issue that tells a newcomer to look in a file that has been renamed or deleted wastes the
time of the one person the project most needs to keep. These are the cheapest checks that catch
that, and they run with everything else.
"""

from __future__ import annotations

import re
from pathlib import Path
from typing import Final

import pytest
import yaml

ROOT: Final = Path(__file__).resolve().parent.parent
SEEDS: Final = sorted((ROOT / ".github" / "seed-issues").glob("*.md"))
FRONTMATTER: Final = re.compile(r"^---\n(.*?)\n---\n(.*)$", re.S)
REPOSITORY_LINK: Final = re.compile(r"\]\(\.\./\.\./([^)#]+)")


def labels_defined() -> set[str]:
    """Every label name the repository declares."""
    text = (ROOT / ".github" / "labels.yml").read_text()
    return {entry["name"] for entry in yaml.safe_load(text)}


def milestones_defined() -> set[str]:
    """Every milestone title the repository declares."""
    text = (ROOT / ".github" / "milestones.yml").read_text()
    return {entry["title"] for entry in yaml.safe_load(text)}


def test_there_are_seed_issues() -> None:
    """A glob that matches nothing would make every other test here pass by default."""
    assert len(SEEDS) >= 10


@pytest.mark.parametrize("path", SEEDS, ids=lambda path: path.name)
def test_seed_issue_is_well_formed(path: Path) -> None:
    """Each file has frontmatter the bootstrap script can use, and a body."""
    match = FRONTMATTER.match(path.read_text())
    assert match is not None, "no frontmatter"

    meta = yaml.safe_load(match.group(1))
    assert isinstance(meta, dict)
    assert meta.get("title"), "no title"
    assert meta.get("labels"), "no labels"

    unknown = set(meta["labels"]) - labels_defined()
    assert not unknown, f"labels not in labels.yml: {sorted(unknown)}"

    milestone = meta.get("milestone")
    assert milestone is None or milestone in milestones_defined(), (
        f"milestone {milestone!r} is not in milestones.yml"
    )

    assert len(match.group(2).strip()) > 200, "the body is too short to act on"


@pytest.mark.parametrize("path", SEEDS, ids=lambda path: path.name)
def test_seed_issue_links_resolve(path: Path) -> None:
    """Every file an issue points at still exists.

    The links are written relative to the seed file so that they work when the file is read in
    the repository; the bootstrap script rewrites them to absolute URLs before posting.
    """
    for target in REPOSITORY_LINK.findall(path.read_text()):
        assert (ROOT / target).exists(), f"{target} does not exist"
