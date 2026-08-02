"""内部 em_get 调用与 sgw 端点政策的静态一致性检查。"""
from __future__ import annotations

import ast
import itertools
import os
from pathlib import Path
from urllib.parse import urlsplit

import pytest

from sgw.proxy import EndpointPolicy, load_config


REPO = Path(__file__).resolve().parents[3]
PACKAGE_ROOT = Path(__file__).resolve().parents[1]
ASGK_SOURCE = Path(os.environ.get(
    "ASGK_SOURCE_DIR",
    REPO / "skills" / "a-stock-data" / "scripts" / "asgk" / "asgk",
))
CONFIG = PACKAGE_ROOT / "sgw" / "config.toml"


def _combine(parts: list[list[str]]) -> list[str]:
    return ["".join(values) for values in itertools.product(*parts)]


def _strings(node: ast.AST, env: dict) -> list[str] | dict[str, list[str]] | None:
    """解析 URL 中可静态确定的部分，运行时标的/日期替换为 ``*``。"""
    if isinstance(node, ast.Constant) and isinstance(node.value, str):
        return [node.value]
    if isinstance(node, ast.Name):
        return env.get(node.id)
    if isinstance(node, ast.JoinedStr):
        parts: list[list[str]] = []
        for value in node.values:
            if isinstance(value, ast.Constant):
                parts.append([str(value.value)])
            elif isinstance(value, ast.FormattedValue):
                resolved = _strings(value.value, env)
                parts.append(resolved if isinstance(resolved, list) else ["*"])
        return _combine(parts)
    if isinstance(node, ast.BinOp) and isinstance(node.op, ast.Add):
        left, right = _strings(node.left, env), _strings(node.right, env)
        if isinstance(left, list) and isinstance(right, list):
            return _combine([left, right])
    if isinstance(node, ast.Dict):
        result: dict[str, list[str]] = {}
        for key_node, value_node in zip(node.keys, node.values):
            key = _strings(key_node, env) if key_node is not None else None
            value = _strings(value_node, env)
            if not isinstance(key, list) or len(key) != 1 or not isinstance(value, list):
                return None
            result[key[0]] = value
        return result
    if isinstance(node, ast.Subscript) and isinstance(node.value, ast.Name):
        mapping = env.get(node.value.id)
        if not isinstance(mapping, dict):
            return None
        index = _strings(node.slice, env)
        if isinstance(index, list) and len(index) == 1 and index[0] in mapping:
            return mapping[index[0]]
        return [value for values in mapping.values() for value in values]
    return None


def _assignments(nodes: list[ast.stmt], base: dict | None = None) -> dict:
    env = dict(base or {})
    # 多轮解析允许常量引用同文件中先定义的其它常量。
    for _ in range(3):
        for node in nodes:
            if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef, ast.ClassDef)):
                continue
            if isinstance(node, ast.Assign) and len(node.targets) == 1:
                target = node.targets[0]
                if isinstance(target, ast.Name):
                    value = _strings(node.value, env)
                    if value is not None:
                        env[target.id] = value
    return env


def _em_get_urls(path: Path) -> list[tuple[int, str]]:
    tree = ast.parse(path.read_text(encoding="utf-8"), filename=str(path))
    module_env = _assignments(tree.body)
    found: list[tuple[int, str]] = []
    for function in (node for node in tree.body if isinstance(node, ast.FunctionDef)):
        local_nodes = [node for node in ast.walk(function) if isinstance(node, ast.stmt)]
        env = _assignments(local_nodes, module_env)
        for node in ast.walk(function):
            if not isinstance(node, ast.Call) or not node.args:
                continue
            if not isinstance(node.func, ast.Name) or node.func.id != "em_get":
                continue
            urls = _strings(node.args[0], env)
            assert isinstance(urls, list) and urls, (
                f"{path}:{node.lineno} em_get URL 无法静态归类"
            )
            found.extend((node.lineno, url) for url in urls)
    return found


@pytest.mark.skipif(
    not ASGK_SOURCE.is_dir(),
    reason="asgk source is not bundled with the standalone sgw package",
)
def test_every_internal_gateway_endpoint_has_one_approved_policy():
    policies = [
        EndpointPolicy.from_config(raw)
        for raw in load_config(CONFIG).get("endpoint", [])
        if raw["review_status"] == "approved"
    ]
    covered: set[str] = set()
    calls = 0

    for source in sorted(ASGK_SOURCE.glob("*.py")):
        for line, pattern in _em_get_urls(source):
            calls += 1
            parsed = urlsplit(pattern.replace("*", "inventory-sample"))
            assert parsed.hostname, f"{source.name}:{line} 无法解析端点: {pattern}"
            matches = [p for p in policies if p.matches(parsed.hostname, parsed.path)]
            assert len(matches) == 1, (
                f"{source.name}:{line} {pattern} 必须唯一匹配已批准政策，"
                f"实际={[p.name for p in matches]}"
            )
            covered.add(matches[0].name)

    assert calls > 0
    approved = {policy.name for policy in policies}
    assert covered == approved, (
        f"sgw 政策与 asgk 库存不一致: "
        f"未被代码使用={sorted(approved - covered)}, "
        f"未分类={sorted(covered - approved)}"
    )
