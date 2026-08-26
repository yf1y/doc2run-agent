from __future__ import annotations

import ast
import os
import sys
from pathlib import PurePosixPath, PureWindowsPath
from typing import Iterable

from .schemas import CodeValidation, TaskSpec


DISALLOWED_CALLS = {
    "os.remove",
    "os.unlink",
    "os.rmdir",
    "os.removedirs",
    "os.rename",
    "os.replace",
    "os.system",
    "shutil.rmtree",
    "subprocess.call",
    "subprocess.Popen",
    "subprocess.run",
    "Path.unlink",
    "Path.rmdir",
    "pathlib.Path.unlink",
    "pathlib.Path.rmdir",
}


def validate_code(code: str, task_spec: TaskSpec) -> CodeValidation:
    errors: list[str] = []
    if not code.strip():
        return CodeValidation(ok=False, errors=["Generated code is empty"], imports=[])
    try:
        tree = ast.parse(code)
    except SyntaxError as error:
        location = f"line {error.lineno}" if error.lineno else "unknown line"
        return CodeValidation(
            ok=False,
            errors=[f"SyntaxError at {location}: {error.msg}"],
            imports=[],
        )

    imports = sorted(_collect_imports(tree))
    aliases = _collect_aliases(tree)
    allowed = _allowed_import_roots(task_spec)
    for module in imports:
        if module not in allowed:
            errors.append(f"Import '{module}' is not allowed by TaskSpec")

    for node in ast.walk(tree):
        if isinstance(node, ast.Call):
            call_name = _resolve_name(_call_name(node.func), aliases)
            if call_name in DISALLOWED_CALLS:
                errors.append(f"Call '{call_name}' is not allowed")
            absolute_write = _absolute_write_path(node, call_name, aliases)
            if absolute_write:
                errors.append(f"Writing to absolute path '{absolute_write}' is not allowed")

    return CodeValidation(ok=not errors, errors=_deduplicate(errors), imports=imports)


def _collect_imports(tree: ast.AST) -> set[str]:
    modules: set[str] = set()
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            modules.update(alias.name.split(".", 1)[0] for alias in node.names)
        elif isinstance(node, ast.ImportFrom) and node.module:
            modules.add(node.module.split(".", 1)[0])
    return modules


def _collect_aliases(tree: ast.AST) -> dict[str, str]:
    aliases: dict[str, str] = {}
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            for item in node.names:
                aliases[item.asname or item.name.split(".", 1)[0]] = item.name
        elif isinstance(node, ast.ImportFrom) and node.module:
            for item in node.names:
                aliases[item.asname or item.name] = f"{node.module}.{item.name}"
    for node in ast.walk(tree):
        if not isinstance(node, ast.Assign) or len(node.targets) != 1:
            continue
        target = node.targets[0]
        if not isinstance(target, ast.Name):
            continue
        value_name = _resolve_name(_call_name(node.value), aliases)
        if value_name:
            aliases[target.id] = value_name
    return aliases


def _allowed_import_roots(task_spec: TaskSpec) -> set[str]:
    allowed = {"__future__"}
    if "standard-library" in task_spec.allowed_dependencies:
        allowed.update(sys.stdlib_module_names)
    allowed.update(_root_names(task_spec.allowed_dependencies))
    allowed.update(_root_names(task_spec.allowed_apis))
    return allowed


def _root_names(values: Iterable[str]) -> set[str]:
    roots: set[str] = set()
    for value in values:
        normalized = value.strip().replace("-", "_")
        if normalized and normalized != "standard_library":
            roots.add(normalized.split(".", 1)[0])
    return roots


def _call_name(node: ast.expr) -> str:
    if isinstance(node, ast.Name):
        return node.id
    if isinstance(node, ast.Attribute):
        parent = _call_name(node.value)
        return f"{parent}.{node.attr}" if parent else node.attr
    if isinstance(node, ast.Call):
        return _call_name(node.func)
    return ""


def _resolve_name(value: str, aliases: dict[str, str]) -> str:
    if not value:
        return ""
    root, separator, rest = value.partition(".")
    resolved = aliases.get(root, root)
    return f"{resolved}.{rest}" if separator else resolved


def _absolute_write_path(
    node: ast.Call, call_name: str, aliases: dict[str, str]
) -> str:
    if call_name == "open" and node.args:
        path = _constant_string(node.args[0])
        mode = _open_mode(node)
        if path and _is_absolute(path) and any(flag in mode for flag in "wax+"):
            return path

    if call_name.endswith((".write_text", ".write_bytes", ".open")) and isinstance(
        node.func, ast.Attribute
    ):
        path = _path_constructor_value(node.func.value, aliases)
        mode = _path_method_mode(node) if call_name.endswith(".open") else "w"
        if path and _is_absolute(path) and any(flag in mode for flag in "wax+"):
            return path
    return ""


def _open_mode(node: ast.Call) -> str:
    if len(node.args) > 1:
        return _constant_string(node.args[1]) or "r"
    for keyword in node.keywords:
        if keyword.arg == "mode":
            return _constant_string(keyword.value) or "r"
    return "r"


def _path_method_mode(node: ast.Call) -> str:
    if node.args:
        return _constant_string(node.args[0]) or "r"
    for keyword in node.keywords:
        if keyword.arg == "mode":
            return _constant_string(keyword.value) or "r"
    return "r"


def _path_constructor_value(node: ast.expr, aliases: dict[str, str]) -> str:
    constructor = (
        _resolve_name(_call_name(node.func), aliases) if isinstance(node, ast.Call) else ""
    )
    if not isinstance(node, ast.Call) or constructor.split(".")[-1] != "Path" or not node.args:
        return ""
    return _constant_string(node.args[0])


def _constant_string(node: ast.expr) -> str:
    return node.value if isinstance(node, ast.Constant) and isinstance(node.value, str) else ""


def _is_absolute(value: str) -> bool:
    # Generated code may target a different OS than the host running the
    # validator, so check both path syntaxes instead of relying on the host's
    # pathlib/os.path implementation alone.
    return (
        PurePosixPath(value).is_absolute()
        or PureWindowsPath(value).is_absolute()
        or os.path.isabs(value)
    )


def _deduplicate(values: list[str]) -> list[str]:
    return list(dict.fromkeys(values))
