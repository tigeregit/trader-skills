"""确保 skill 文档和运行代码不依赖宿主仓库的开发目录。"""
from __future__ import annotations

import re
from pathlib import Path


SKILL_ROOT = Path(__file__).resolve().parents[3]


def _audited_files():
    for path in SKILL_ROOT.rglob("*"):
        if not path.is_file() or path.name == "uv.lock":
            continue
        if any(part in {".venv", ".pytest_cache", "__pycache__", "vendor"}
               for part in path.parts):
            continue
        if path.suffix in {".md", ".py", ".toml", ".yaml", ".yml"}:
            yield path


def test_no_host_repository_references():
    forbidden = (
        ".agents" + "/",
        "packages" + "/sgw",
        "ref" + "/a-stock-data",
        "skills" + "/a-stock-data",
        "gateway-" + "design",
        "asgk-" + "contract.md",
        "test-" + "method.md",
        "trader-" + "skills",
        "/Users" + "/",
    )
    violations = []
    for path in _audited_files():
        text = path.read_text(encoding="utf-8")
        for marker in forbidden:
            if marker in text:
                violations.append(f"{path.relative_to(SKILL_ROOT)}: {marker}")
    assert not violations, "发现宿主仓库依赖：\n" + "\n".join(violations)


def test_markdown_links_stay_inside_skill_and_exist():
    violations = []
    for path in [SKILL_ROOT / "SKILL.md", *(SKILL_ROOT / "references").glob("*.md")]:
        text = path.read_text(encoding="utf-8")
        for target in re.findall(r"\[[^]]+\]\(([^)]+)\)", text):
            target = target.split("#", 1)[0]
            if not target or "://" in target:
                continue
            resolved = (path.parent / target).resolve()
            if SKILL_ROOT != resolved and SKILL_ROOT not in resolved.parents:
                violations.append(f"{path.name}: 链接越出 skill: {target}")
            elif not resolved.exists():
                violations.append(f"{path.name}: 链接不存在: {target}")
    assert not violations, "Markdown 链接无效：\n" + "\n".join(violations)
