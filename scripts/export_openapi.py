"""Write the API's OpenAPI schema to a file.

The Angular client's types are generated from this schema, so it is checked into the repository
rather than fetched from a running server. That means a contributor can build the frontend
without starting the backend, and that a change to the API shows up as a change to this file in
a pull request -- which is what makes a breaking change visible to a reviewer rather than a
surprise to a client.

Run it from the repository root::

    python scripts/export_openapi.py
"""

from __future__ import annotations

import json
import sys
from pathlib import Path

#: Where the schema is written, relative to the repository root.
OUTPUT = Path("frontend/openapi.json")


def main() -> int:
    """Write the schema. Returns a process exit code."""
    try:
        from openwave.api.app import create_app
    except ImportError as error:
        print(
            'The API extra is not installed. Install it with:\n    pip install -e ".[api]"',
            file=sys.stderr,
        )
        print(f"({error})", file=sys.stderr)
        return 1

    schema = create_app().openapi()
    OUTPUT.parent.mkdir(parents=True, exist_ok=True)
    # sort_keys so that regenerating after an unrelated change produces no diff.
    OUTPUT.write_text(json.dumps(schema, indent=2, sort_keys=True) + "\n", encoding="utf-8")

    paths = len(schema.get("paths", {}))
    models = len(schema.get("components", {}).get("schemas", {}))
    print(f"Wrote {OUTPUT}: {paths} paths, {models} models")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
