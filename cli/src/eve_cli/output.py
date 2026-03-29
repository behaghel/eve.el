from __future__ import annotations

import json
import sys
from typing import Any


def emit_success(args: Any, payload: dict[str, Any]) -> None:
    if getattr(args, "json", False):
        print(json.dumps({"ok": True, **payload}, indent=2, sort_keys=True))
        return
    message = payload.get("message")
    if isinstance(message, str) and message:
        print(message)


def emit_failure(args: Any, message: str, *, exit_code: int) -> None:
    if getattr(args, "json", False):
        print(
            json.dumps(
                {
                    "ok": False,
                    "exit_code": exit_code,
                    "command": getattr(args, "command", None),
                    "message": message,
                },
                indent=2,
                sort_keys=True,
            )
        )
        return
    print(message, file=sys.stderr)
