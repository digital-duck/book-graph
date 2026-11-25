"""`python3 -I -m bookgraph.tool STEP`: one recipe step. JSON object on stdin, one JSON value on stdout."""
from __future__ import annotations

import json
import sys

from .extract import STEPS as _EXTRACT_STEPS
from .publish import STEPS as _PUBLISH_STEPS
from .steps import STEPS as _STEPS

STEPS = {**_STEPS, **_PUBLISH_STEPS, **_EXTRACT_STEPS}


def main(argv: list[str] | None = None) -> int:
    argv = sys.argv[1:] if argv is None else argv
    if len(argv) != 1 or argv[0] not in STEPS:
        print(f"usage: python3 -m bookgraph.tool {{{'|'.join(STEPS)}}}", file=sys.stderr)
        return 2
    raw = sys.stdin.read()
    inputs = json.loads(raw) if raw.strip() else {}
    print(json.dumps(STEPS[argv[0]](inputs), ensure_ascii=False))
    return 0


if __name__ == "__main__":
    sys.exit(main())
