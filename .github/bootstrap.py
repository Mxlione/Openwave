"""Create this repository's labels, milestones and starter issues.

Run by `.github/workflows/bootstrap.yml`, on a button press, never automatically.

It is written to be safe to run again. Labels are created or updated, milestones and issues are
created only when nothing of that name or title exists, and nothing is ever closed, renamed or
deleted. The worst a second run can do is print that there was nothing to do.
"""

from __future__ import annotations

import json
import os
import re
import subprocess
import sys
from pathlib import Path
from typing import Any

import yaml

HERE = Path(__file__).resolve().parent
REPOSITORY = os.environ["REPOSITORY"]
WHAT = os.environ.get("WHAT", "everything")
DRY_RUN = os.environ.get("DRY_RUN", "true").lower() == "true"

# Links in the seed files are written relative to the file, so that they work when the file is
# read in the repository. An issue body is not a file in a directory, so they have to become
# absolute before they are posted.
BLOB = f"https://github.com/{REPOSITORY}/blob/main/"


def api(path: str, method: str = "GET", **fields: Any) -> Any:
    """Call the GitHub API through gh, which is already authenticated on a runner.

    The body goes in as JSON on standard input rather than as -f pairs, because -f sends every
    value as a string and a milestone number has to be a number. Sending JSON keeps the types
    the API expects without a second flag to remember.
    """
    command = ["gh", "api", "-X", method, path]
    body = json.dumps(fields) if fields else None
    if body is not None:
        command += ["--input", "-"]
    result = subprocess.run(command, input=body, capture_output=True, text=True, check=False)
    if result.returncode != 0:
        raise SystemExit(f"{method} {path} failed:\n{result.stderr.strip()}")
    return json.loads(result.stdout) if result.stdout.strip() else None


def paged(path: str) -> list[Any]:
    """Fetch every page of a listing, one object per line.

    --paginate prints one JSON array per page, so the output of several pages is not valid JSON.
    Asking jq for each element as compact JSON gives a line per object, which does not care how
    many pages there were. Splitting the raw output on brackets instead would come apart on the
    nested arrays that GitHub puts in an issue, such as its labels.
    """
    result = subprocess.run(
        ["gh", "api", "--paginate", "--jq", ".[] | @json", path],
        capture_output=True,
        text=True,
        check=False,
    )
    if result.returncode != 0:
        raise SystemExit(f"GET {path} failed:\n{result.stderr.strip()}")
    return [json.loads(line) for line in result.stdout.splitlines() if line.strip()]


def do_labels() -> None:
    """Create or update every label."""
    wanted = yaml.safe_load((HERE / "labels.yml").read_text())
    existing = {label["name"]: label for label in paged(f"repos/{REPOSITORY}/labels?per_page=100")}
    for label in wanted:
        name, colour, description = label["name"], label["colour"], label["description"]
        current = existing.get(name)
        if current and current["color"] == colour and (current["description"] or "") == description:
            print(f"label  = {name}")
            continue
        verb = "update" if current else "create"
        print(f"label  {verb[0]} {name}")
        if DRY_RUN:
            continue
        if current:
            api(
                f"repos/{REPOSITORY}/labels/{name.replace(' ', '%20')}",
                "PATCH",
                new_name=name,
                color=colour,
                description=description,
            )
        else:
            api(
                f"repos/{REPOSITORY}/labels",
                "POST",
                name=name,
                color=colour,
                description=description,
            )


def do_milestones() -> dict[str, int]:
    """Create any missing milestone and return every milestone's number by title."""
    wanted = yaml.safe_load((HERE / "milestones.yml").read_text())
    existing = {
        milestone["title"]: milestone["number"]
        for milestone in paged(f"repos/{REPOSITORY}/milestones?state=all&per_page=100")
    }
    for milestone in wanted:
        title = milestone["title"]
        if title in existing:
            print(f"stone  = {title}")
            continue
        print(f"stone  + {title}")
        if DRY_RUN:
            continue
        created = api(
            f"repos/{REPOSITORY}/milestones",
            "POST",
            title=title,
            description=milestone.get("description", ""),
        )
        existing[title] = created["number"]
    return existing


def read_seed(path: Path) -> tuple[dict[str, Any], str]:
    """Split one seed file into its frontmatter and its body."""
    text = path.read_text()
    match = re.match(r"^---\n(.*?)\n---\n(.*)$", text, re.S)
    if not match:
        raise SystemExit(f"{path.name} has no frontmatter")
    meta = yaml.safe_load(match.group(1))
    body = match.group(2).strip()
    # ../../something -> an absolute link into the repository.
    body = re.sub(r"\]\(\.\./\.\./([^)]+)\)", lambda m: f"]({BLOB}{m.group(1)})", body)
    return meta, body


def do_issues(milestones: dict[str, int]) -> None:
    """Create any starter issue that is not already open or closed under the same title."""
    existing = {
        issue["title"]
        for issue in paged(f"repos/{REPOSITORY}/issues?state=all&per_page=100")
        if "pull_request" not in issue
    }
    for path in sorted((HERE / "seed-issues").glob("*.md")):
        meta, body = read_seed(path)
        title = meta["title"]
        if title in existing:
            print(f"issue  = {title}")
            continue
        print(f"issue  + {title}")
        if DRY_RUN:
            continue
        fields: dict[str, Any] = {"title": title, "body": body, "labels": meta.get("labels", [])}
        number = milestones.get(meta.get("milestone", ""))
        if number is not None:
            fields["milestone"] = number
        created = api(f"repos/{REPOSITORY}/issues", "POST", **fields)
        print(f"       -> {created['html_url']}")


def main() -> int:
    if DRY_RUN:
        print("DRY RUN: nothing will be created. Run again with dry_run unticked.\n")
    print(f"repository: {REPOSITORY}\n")

    milestones: dict[str, int] = {}
    if WHAT in {"everything", "labels"}:
        do_labels()
    if WHAT in {"everything", "milestones", "issues"}:
        milestones = do_milestones()
    if WHAT in {"everything", "issues"}:
        do_issues(milestones)

    print("\nTopics are not set from here: the token a workflow gets cannot change repository")
    print("settings. Set them by hand under About, or see .github/TOPICS.txt.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
