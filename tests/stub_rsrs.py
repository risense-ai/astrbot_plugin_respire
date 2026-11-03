#!/usr/bin/env python3
"""Stateful `rsrs` stub for plugin tests.

The plugin spawns this file directly (shebang + exec bit).  State lives in
``$RSRS_STUB_STATE/state.json`` — the name starts with ``RSRS_`` and carries no
secret-pattern word, so the plugin's sidecar environment filter forwards it.

Envelope shapes mirror the real CLI (see docs/respire-cli-notes.md): JSON on
stdout, status ok|pending|fail, ``details`` for payloads, ``summary.id`` for
writes.  Every invocation appends its argv to ``state["log"]`` so tests can
assert on flags such as ``--project``.
"""

from __future__ import annotations

import json
import os
import sys
import uuid
from pathlib import Path


def _load(state_dir: Path) -> dict:
    path = state_dir / "state.json"
    if path.exists():
        try:
            return json.loads(path.read_text(encoding="utf-8"))
        except Exception:
            pass
    return {}


def _save(state_dir: Path, state: dict) -> None:
    state_dir.mkdir(parents=True, exist_ok=True)
    (state_dir / "state.json").write_text(
        json.dumps(state, ensure_ascii=False, indent=1), encoding="utf-8"
    )


def _flag(argv: list[str], name: str, default: str = "") -> str:
    for index, token in enumerate(argv):
        if token == name and index + 1 < len(argv):
            return argv[index + 1]
        if token.startswith(name + "="):
            return token[len(name) + 1 :]
    return default


def _has(argv: list[str], name: str) -> bool:
    return name in argv


def _envelope(command: str, status: str, **fields) -> dict:
    doc = {"command": command, "status": status, "summary": {}, "items": [], "actions": [], "errors": []}
    doc.update(fields)
    return doc


def _title_row(entry: dict) -> dict:
    return {
        "id": entry["id"],
        "title": entry["title"],
        "score": entry.get("score", 0.5),
        "superseded_by": entry.get("superseded_by"),
    }


def main(argv: list[str]) -> dict:
    state_dir = Path(os.environ["RSRS_STUB_STATE"])
    state = _load(state_dir)
    state.setdefault("entries", {})
    state.setdefault("log", [])
    state["log"] = (state["log"] + [argv])[-300:]
    state.setdefault("env_log", [])
    state["env_log"] = (
        state["env_log"]
        + [
            {
                "argv": argv,
                "data_dir": os.environ.get("RSRS_DATA_DIR", ""),
                "m3_dir": os.environ.get("RSRS_M3_DIR", ""),
            }
        ]
    )[-300:]
    state.setdefault("recall_rows", [])
    state.setdefault("candidates", {"merge": [], "parent": []})
    state.setdefault("chain_ancestors", [])
    state.setdefault("chain_descendants", [])
    state.setdefault("fail", [])

    command = argv[0] if argv else ""
    entries: dict = state["entries"]

    def persist() -> None:
        _save(state_dir, state)

    if command in state["fail"]:
        return _envelope(command, "fail", errors=[f"stub injected failure for {command}"])

    if command == "space":
        action = argv[1] if len(argv) > 1 else "list"
        spaces = state.get("spaces") or [
            {"name": "main", "dir": str(state_dir), "user": "stub", "current": True}
        ]
        persist()
        if action == "list":
            return _envelope(
                command,
                "ok",
                summary={"action": "list", "count": len(spaces)},
                details={"spaces": spaces, "current_dir": str(state_dir)},
            )
        return _envelope(command, "fail", errors=[f"stub: unsupported space action {action!r}"])

    if command == "status":
        state["summary"] = {
            "data_dir": str(state_dir),
            "unlocked": True,
            "local_total": len(entries),
            "local_alive": True,
            "session": {"user": "stub", "has_token": False},
            "sync_live": {"phase": "idle", "pending": 0, "conflicts": 0},
            "retrieval_index": {"state": "ready"},
        }
        persist()
        return _envelope(command, "ok", summary=state["summary"])

    if command == "recall":
        limit = int(_flag(argv, "--limit", "5") or 5)
        rows = state["recall_rows"][:limit]
        items = [
            {
                "name": str(row.get("id", ""))[:8],
                "status": "ok",
                "value": f"{float(row.get('score') or 0):.3f} {row.get('title', '')}",
            }
            for row in rows
        ]
        persist()
        return _envelope(command, "ok", summary={"count": len(rows)}, items=items, details=rows)

    if command == "show":
        entry = entries.get(argv[1] if len(argv) > 1 else "")
        if not isinstance(entry, dict):
            return _envelope(command, "fail", errors=["entry not found (stub)"])
        persist()
        return _envelope(
            command,
            "ok",
            details={
                "entry": entry,
                "ancestors": state.get("show_ancestors", []),
                "children": state.get("show_children", []),
            },
        )

    if command == "chain":
        entry = entries.get(argv[1] if len(argv) > 1 else "")
        if not isinstance(entry, dict):
            return _envelope(command, "fail", errors=["entry not found (stub)"])
        persist()
        return _envelope(
            command,
            "ok",
            details={
                "entry": entry,
                "ancestors": state["chain_ancestors"],
                "descendants": state["chain_descendants"],
            },
        )

    if command == "candidates":
        persist()
        return _envelope(command, "ok", details=dict(state["candidates"]))

    if command == "remember":
        content = argv[1] if len(argv) > 1 else ""
        title = _flag(argv, "--title") or content[:40]
        importance = _flag(argv, "--importance", "important")
        parent = _flag(argv, "--parent")
        if _flag(argv, "--merge-ids"):
            merge_ids = [item for item in _flag(argv, "--merge-ids").split(",") if item]
            entry_id = str(uuid.uuid4())
            entry = {
                "id": entry_id,
                "title": title,
                "content": content,
                "importance": importance,
                "parent_id": parent or entries.get(merge_ids[0], {}).get("parent_id", ""),
                "updated_at": "2026-11-03T00:00:00Z",
                "merged_from": merge_ids,
            }
            for source in merge_ids:
                if source in entries:
                    entries[source]["superseded_by"] = entry_id
            entries[entry_id] = entry
            persist()
            return _envelope(command, "ok", summary={"id": entry_id, "written": True})
        if importance == "important" and not parent:
            persist()
            return _envelope(
                command,
                "pending",
                summary={"written": False, "state": "candidates"},
                details=dict(state["candidates"]),
            )
        entry_id = str(uuid.uuid4())
        entries[entry_id] = {
            "id": entry_id,
            "title": title,
            "content": content,
            "importance": importance,
            "parent_id": parent,
            "updated_at": "2026-11-03T00:00:00Z",
        }
        persist()
        return _envelope(command, "ok", summary={"id": entry_id, "written": True})

    if command == "update":
        entry = entries.get(argv[1] if len(argv) > 1 else "")
        if not isinstance(entry, dict):
            return _envelope(command, "fail", errors=["entry not found (stub)"])
        if _flag(argv, "--content"):
            entry["content"] = _flag(argv, "--content")
        if _flag(argv, "--title"):
            entry["title"] = _flag(argv, "--title")
        if _flag(argv, "--importance"):
            entry["importance"] = _flag(argv, "--importance")
        entry["updated_at"] = "2026-11-03T00:00:01Z"
        persist()
        return _envelope(command, "ok", summary={"id": entry["id"], "written": True})

    if command == "attach":
        child = entries.get(argv[1] if len(argv) > 1 else "")
        parent_id = _flag(argv, "--parent")
        if not isinstance(child, dict) or parent_id not in entries:
            return _envelope(command, "fail", errors=["attach endpoint not found (stub)"])
        child["parent_id"] = parent_id
        persist()
        return _envelope(command, "ok", summary={"id": child["id"]})

    if command == "forget":
        target = argv[1] if len(argv) > 1 else ""
        if target not in entries:
            return _envelope(command, "fail", errors=["entry not found (stub)"])
        entries.pop(target)
        persist()
        return _envelope(command, "ok", summary={"id": target})

    return _envelope(command, "fail", errors=[f"stub: unknown command {command!r}"])


if __name__ == "__main__":
    print(json.dumps(main(sys.argv[1:]), ensure_ascii=False))
