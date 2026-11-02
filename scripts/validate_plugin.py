#!/usr/bin/env python3
"""Standalone structure/metadata validator for astrbot_plugin_respire.

No third-party dependency is required.  When PyYAML or Pillow are present the
validator uses them for stricter checks; otherwise it falls back to a small
top-level YAML reader and skips the image dimension check.
"""

from __future__ import annotations

import ast
import json
import re
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
REQUIRED_FILES = (
    "metadata.yaml",
    "main.py",
    "_conf_schema.json",
    "README.md",
    "LICENSE",
    "requirements.txt",
    "logo.png",
)
TOOL_NAMES = {
    "respire_recall",
    "respire_show",
    "respire_remember",
    "respire_update",
    "respire_merge",
    "respire_attach",
}
COMMANDS = {"help", "status", "recall", "show", "remember", "forget"}
SECRET_PATTERNS = (
    re.compile(r"\bsk-[A-Za-z0-9_-]{16,}\b"),
    re.compile(r"\bgh[pousr]_[A-Za-z0-9]{20,}\b"),
    re.compile(r"\bgithub_pat_[A-Za-z0-9_]{20,}\b"),
    re.compile(r"-----BEGIN [A-Z ]*PRIVATE KEY-----"),
)

failures: list[str] = []
passes: list[str] = []


def check(condition: bool, label: str, detail: str = "") -> None:
    if condition:
        passes.append(label)
    else:
        failures.append(label + (f": {detail}" if detail else ""))


def read_yaml_top_level(path: Path) -> dict[str, object]:
    try:
        import yaml  # type: ignore

        data = yaml.safe_load(path.read_text(encoding="utf-8"))
        return data if isinstance(data, dict) else {}
    except Exception:
        result: dict[str, object] = {}
        for line in path.read_text(encoding="utf-8").splitlines():
            if not line or line[0].isspace() or line.lstrip().startswith("#"):
                continue
            match = re.match(r"^([A-Za-z0-9_]+):\s*(.*)$", line)
            if match:
                result[match.group(1)] = match.group(2).strip().strip("\"'")
        return result


def main() -> int:
    for name in REQUIRED_FILES:
        check((ROOT / name).is_file(), f"required file {name}")

    metadata = read_yaml_top_level(ROOT / "metadata.yaml")
    for key in ("name", "desc", "version", "author"):
        check(bool(str(metadata.get(key) or "").strip()), f"metadata.{key}")
    check(
        str(metadata.get("name", "")).startswith("astrbot_plugin_"),
        "metadata.name prefix",
        "AstrBot recommends astrbot_plugin_",
    )
    check(
        bool(re.fullmatch(r"\d+\.\d+\.\d+(?:[-+][0-9A-Za-z.-]+)?", str(metadata.get("version", "")))),
        "metadata.version semver",
    )
    repo = str(metadata.get("repo") or "")
    check(repo.startswith("https://github.com/"), "metadata.repo url", repo or "missing")

    schema_path = ROOT / "_conf_schema.json"
    try:
        schema = json.loads(schema_path.read_text(encoding="utf-8"))
        check(isinstance(schema, dict) and bool(schema), "config schema object")
    except Exception as exc:
        schema = {}
        failures.append(f"config schema JSON: {exc}")

    allowed_types = {"string", "text", "int", "float", "bool", "object", "list", "dict", "template_list", "file"}
    for key, item in schema.items():
        check(isinstance(item, dict), f"schema {key} object")
        if not isinstance(item, dict):
            continue
        check(item.get("type") in allowed_types, f"schema {key} type", str(item.get("type")))
        check("default" in item, f"schema {key} default")
        if item.get("type") == "int" and not isinstance(item.get("default"), int):
            failures.append(f"schema {key} int default")
        if item.get("type") == "bool" and not isinstance(item.get("default"), bool):
            failures.append(f"schema {key} bool default")

    main_path = ROOT / "main.py"
    source = main_path.read_text(encoding="utf-8")
    try:
        tree = ast.parse(source)
        check(True, "main.py parses")
    except SyntaxError as exc:
        check(False, "main.py parses", str(exc))
        tree = ast.Module(body=[], type_ignores=[])

    defaults: dict[str, object] = {}
    tools: set[str] = set()
    commands: set[str] = set()
    has_llm_hook = False
    has_after_sent = False
    for node in ast.walk(tree):
        if isinstance(node, ast.Assign):
            for target in node.targets:
                if isinstance(target, ast.Name) and target.id == "DEFAULTS":
                    try:
                        value = ast.literal_eval(node.value)
                        if isinstance(value, dict):
                            defaults = value
                    except Exception:
                        pass
        if isinstance(node, ast.AnnAssign):
            target = node.target
            if isinstance(target, ast.Name) and target.id == "DEFAULTS" and node.value is not None:
                try:
                    value = ast.literal_eval(node.value)
                    if isinstance(value, dict):
                        defaults = value
                except Exception:
                    pass
        if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)):
            for decorator in node.decorator_list:
                if not isinstance(decorator, ast.Call) or not isinstance(decorator.func, ast.Attribute):
                    continue
                attr = decorator.func.attr
                if attr == "llm_tool":
                    name = None
                    if decorator.args and isinstance(decorator.args[0], ast.Constant):
                        name = decorator.args[0].value
                    tools.add(str(name or node.name))
                elif attr == "command" and decorator.args and isinstance(decorator.args[0], ast.Constant):
                    commands.add(str(decorator.args[0].value))
                elif attr == "on_llm_request":
                    has_llm_hook = True
                elif attr == "after_message_sent":
                    has_after_sent = True

    check(bool(defaults), "DEFAULTS present")
    check(set(defaults) == set(schema), "schema keys match DEFAULTS", str(sorted(set(defaults) ^ set(schema))))
    check(TOOL_NAMES <= tools, "six LLM tools", str(sorted(TOOL_NAMES - tools)))
    check(COMMANDS <= commands, "command subcommands", str(sorted(COMMANDS - commands)))
    check(has_llm_hook, "on_llm_request hook")
    check(has_after_sent, "after_message_sent hook")
    check("--force" not in re.findall(r'["\']--force["\']', source), "no forced write flag")

    for name in ("README.md", "CHANGELOG.md", "LICENSE"):
        text = (ROOT / name).read_text(encoding="utf-8")
        check(bool(text.strip()), f"{name} non-empty")
    readme = (ROOT / "README.md").read_text(encoding="utf-8")
    for tool in TOOL_NAMES:
        check(tool in readme, f"README documents {tool}")

    secret_hits = []
    for path in ROOT.rglob("*"):
        if not path.is_file() or path.suffix.lower() in {".png", ".jpg", ".jpeg", ".zip", ".pyc"}:
            continue
        # tests/ holds deliberately fake credential-shaped fixtures; this scan
        # exists to keep real secrets out of shipped files.
        if any(part in {".git", "__pycache__", "target", "dist", "tests"} for part in path.parts):
            continue
        try:
            text = path.read_text(encoding="utf-8")
        except Exception:
            continue
        for pattern in SECRET_PATTERNS:
            if pattern.search(text):
                secret_hits.append(str(path.relative_to(ROOT)))
                break
    check(not secret_hits, "no credential-looking literals", ", ".join(secret_hits))

    logo = ROOT / "logo.png"
    if logo.is_file():
        try:
            from PIL import Image  # type: ignore

            width, height = Image.open(logo).size
            check(width == height, "logo square", f"{width}x{height}")
        except Exception:
            passes.append("logo dimension check skipped")

    for line in passes:
        print(f"PASS {line}")
    for line in failures:
        print(f"FAIL {line}")
    print(f"\n{len(passes)} passed, {len(failures)} failed")
    return 0 if not failures else 1


if __name__ == "__main__":
    sys.exit(main())
