"""asgk_server.cli.config — CLI 侧服务端地址解析。

优先级（从高到低）：
    1. 环境变量 ``ASGK_SERVER``（最高，systemd/container envfile 用这个）
    2. 用户配置 ``~/.config/asgk/cli.toml``（service 脚本 install 时自动生成）
    3. 随包默认 ``cli.toml.default``（url = http://127.0.0.1:7701）

环境变量与 .env 机制（``ASGK_ENV``）保持兼容：旧客户端的 ``em_proxy._load_dotenv``
会从 .env 读 ``ASGK_SERVER``，此处同样先填补 ``os.environ``，保证迁移期行为一致。
"""
from __future__ import annotations

import os
import tomllib
from pathlib import Path

# 随包安装的默认配置（cli/cli.toml.default）
HERE = Path(__file__).resolve().parent
DEFAULT_CLI_TOML = HERE / "cli.toml.default"

# 用户配置位置（XDG）
USER_CLI_TOML = Path(
    os.environ.get("XDG_CONFIG_HOME", "~/.config")
).expanduser() / "asgk" / "cli.toml"

DEFAULT_URL = "http://127.0.0.1:7701"


def _load_dotenv() -> None:
    """从 .env 填补未设置的 ASGK_SERVER（兼容旧客户端的 em_proxy 行为）。

    查找顺序：ASGK_ENV 指定 → cwd/.env → 向上最多3级。仅填补未设置的键。
    """
    candidates: list[Path] = []
    env_path = os.environ.get("ASGK_ENV")
    if env_path:
        candidates.append(Path(env_path))
    cwd = Path.cwd()
    candidates.append(cwd / ".env")
    for _ in range(3):
        cwd = cwd.parent
        candidates.append(cwd / ".env")

    for p in candidates:
        if not p.is_file():
            continue
        for line in p.read_text(encoding="utf-8").splitlines():
            line = line.strip()
            if not line or line.startswith("#") or "=" not in line:
                continue
            key, _, val = line.partition("=")
            key, val = key.strip(), val.strip().strip("'\"")
            if key and key not in os.environ:
                os.environ[key] = val
        break  # 只用第一个找到的 .env


def _read_toml_url(path: Path) -> str | None:
    """从 TOML 配置读 [server].url。文件不存在/格式错返回 None。"""
    if not path.is_file():
        return None
    try:
        with path.open("rb") as f:
            cfg = tomllib.load(f)
        return cfg.get("server", {}).get("url")
    except (tomllib.TOMLDecodeError, OSError):
        return None


def resolve_server() -> str:
    """解析 CLI 应连的服务端地址。

    优先级：ASGK_SERVER 环境变量 → ~/.config/asgk/cli.toml → 包内默认。
    """
    _load_dotenv()
    # 1. 环境变量最高
    if env := os.environ.get("ASGK_SERVER"):
        return env.rstrip("/")
    # 2. 用户配置
    if url := _read_toml_url(USER_CLI_TOML):
        return url.rstrip("/")
    # 3. 包内默认
    if url := _read_toml_url(DEFAULT_CLI_TOML):
        return url.rstrip("/")
    # 4. 硬编码兜底
    return DEFAULT_URL
