"""systemd user 服务管理脚本测试。"""
from __future__ import annotations

import os
import subprocess
from pathlib import Path


SCRIPT = Path(__file__).resolve().parents[1] / "scripts" / "sgw-service.sh"


def _write_executable(path: Path, content: str) -> None:
    path.write_text(content, encoding="utf-8")
    path.chmod(0o755)


def test_service_script_syntax():
    subprocess.run(["bash", "-n", str(SCRIPT)], check=True)


def test_service_lifecycle_with_fake_systemd(tmp_path: Path):
    home = tmp_path / "home"
    fake_bin = tmp_path / "bin"
    tool_bin = tmp_path / "tool-bin"
    work_dir = tmp_path / "data" / "sgw"
    log = tmp_path / "calls.log"
    for path in (home, fake_bin, tool_bin):
        path.mkdir(parents=True)

    _write_executable(tool_bin / "sgw-proxy", "#!/usr/bin/env bash\nexit 0\n")
    _write_executable(
        fake_bin / "uv",
        f"""#!/usr/bin/env bash
set -eu
printf 'uv %s\\n' "$*" >>{log!s}
if [[ "$1 $2 ${{3:-}}" == "tool dir --bin" ]]; then
    printf '%s\\n' {tool_bin!s}
elif [[ "$1 $2" == "tool list" ]]; then
    printf 'sgw v0.1.0\\n- sgw-proxy\\n'
fi
""",
    )
    _write_executable(
        fake_bin / "systemctl",
        f"""#!/usr/bin/env bash
set -eu
printf 'systemctl %s\\n' "$*" >>{log!s}
if [[ "$*" == *"is-active"* ]]; then
    printf 'active\\n'
fi
""",
    )
    _write_executable(fake_bin / "uname", "#!/usr/bin/env bash\nprintf 'Linux\\n'\n")

    env = {
        **os.environ,
        "HOME": str(home),
        "PATH": f"{fake_bin}:/usr/bin:/bin",
        "UV_BIN": str(fake_bin / "uv"),
        "XDG_CONFIG_HOME": str(tmp_path / "config"),
        "XDG_DATA_HOME": str(tmp_path / "data"),
        "SGW_PORT": "17700",
    }

    def run(command: str) -> subprocess.CompletedProcess[str]:
        return subprocess.run(
            ["bash", str(SCRIPT), command], env=env, check=True,
            capture_output=True, text=True,
        )

    run("install")
    unit = tmp_path / "config" / "systemd" / "user" / "sgw.service"
    content = unit.read_text(encoding="utf-8")
    assert f"WorkingDirectory={work_dir}" in content
    assert f'ExecStart="{tool_bin}/sgw-proxy"' in content
    assert '--host "127.0.0.1" --port "17700"' in content
    assert f'--fp-dir "{work_dir}/fingerprints"' in content
    assert f'--cache-dir "{work_dir}/cache"' in content
    assert f'--state-dir "{work_dir}/state"' in content
    assert "Restart=on-failure" in content
    assert "WantedBy=default.target" in content

    assert run("run").stdout.strip() == "active"
    run("restart")
    run("stop")
    run("uninstall")
    assert not unit.exists()
    assert work_dir.exists()

    calls = log.read_text(encoding="utf-8")
    assert "tool install --force" in calls
    assert "systemctl --user enable sgw.service" in calls
    assert "systemctl --user start sgw.service" in calls
    assert "systemctl --user restart sgw.service" in calls
    assert "systemctl --user stop sgw.service" in calls
    assert "tool uninstall sgw" in calls
