#!/usr/bin/env bash
# asgk-server systemd 用户服务管理脚本（从 sgw/scripts/sgw-service.sh 改造）。
#
# 取代 sgw-service.sh：asgk-server 是能力代理服务端，吞噬了 sgw 的全部流量内核
# （限流/熔断/缓存/singleflight），并暴露语义能力接口（POST /v1/<capability>）。
# sgw 已标 DEPRECATED（见 packages/sgw/README.md），新部署一律用 asgk-server。
#
# 与 sgw-service.sh 的差异：
#   - unit 名 asgk-server.service（非 sgw.service）
#   - 服务目录 ~/.local/share/asgk-server（非 ~/.local/share/sgw）
#   - 端口 7701（非 7700，避免与仍在跑的 sgw 冲突，渐进切换）
#   - 二进制 asgk-server（非 sgw-proxy）
#   - 描述「A股能力代理服务端」（非「流量网关」）
set -Eeuo pipefail

SCRIPT_DIR=$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")" && pwd)
PROJECT_DIR=$(cd -- "$SCRIPT_DIR/.." && pwd)
UNIT_NAME="asgk-server.service"
UNIT_DIR="${XDG_CONFIG_HOME:-$HOME/.config}/systemd/user"
UNIT_PATH="$UNIT_DIR/$UNIT_NAME"
SERVICE_HOME="${XDG_DATA_HOME:-$HOME/.local/share}/asgk-server"

SERVER_WORK_DIR="${SERVER_WORK_DIR:-$SERVICE_HOME}"
SERVER_HOST="${SERVER_HOST:-127.0.0.1}"
SERVER_PORT="${SERVER_PORT:-7701}"
SERVER_FP_DIR="${SERVER_FP_DIR:-$SERVER_WORK_DIR/fingerprints}"
SERVER_CACHE_DIR="${SERVER_CACHE_DIR:-$SERVER_WORK_DIR/cache}"
SERVER_STATE_DIR="${SERVER_STATE_DIR:-$SERVER_WORK_DIR/state}"

usage() {
    cat <<'EOF'
Usage: asgk-server-service.sh <command>

Commands:
  install    Install asgk-server globally with uv tool, then write and enable the unit
  run        Start the systemd user service
  stop       Stop the systemd user service
  restart    Restart the systemd user service
  status     Show service status
  uninstall  Remove the unit and uv tool; keep the service work directory

Optional environment variables used by install:
  UV_BIN, SERVER_WORK_DIR
  SERVER_HOST, SERVER_PORT, SERVER_FP_DIR, SERVER_CACHE_DIR, SERVER_STATE_DIR

Manual management after install:
  systemctl --user start|stop|restart|status asgk-server.service
  journalctl --user-unit asgk-server.service -f

Client configuration (point asgk at this server):
  export ASGK_SERVER=http://127.0.0.1:7701
EOF
}

die() {
    printf 'asgk-server-service: %s\n' "$*" >&2
    exit 1
}

resolve_uv() {
    if [[ -n "${UV_BIN:-}" ]]; then
        [[ -x "$UV_BIN" ]] || die "UV_BIN is not executable: $UV_BIN"
        return
    fi
    if command -v uv >/dev/null 2>&1; then
        UV_BIN=$(command -v uv)
    elif [[ -x "$HOME/.local/bin/uv" ]]; then
        UV_BIN="$HOME/.local/bin/uv"
    else
        die "uv was not found; install uv or set UV_BIN=/absolute/path/to/uv"
    fi
}

require_systemd_user() {
    [[ "$(uname -s)" == "Linux" ]] ||
        die "systemd user services require Linux; current OS is $(uname -s)"
    command -v systemctl >/dev/null 2>&1 || die "systemctl was not found"
    systemctl --user show-environment >/dev/null 2>&1 ||
        die "the systemd user manager is unavailable for $USER"
}

validate_config() {
    [[ "$SERVER_PORT" =~ ^[0-9]+$ ]] || die "SERVER_PORT must be an integer"
    ((SERVER_PORT >= 1 && SERVER_PORT <= 65535)) ||
        die "SERVER_PORT must be between 1 and 65535"
}

systemd_quote() {
    local value="$1"
    value=${value//\\/\\\\}
    value=${value//\"/\\\"}
    value=${value//%/%%}
    printf '"%s"' "$value"
}

systemd_path() {
    local value="$1"
    value=${value//\\/\\x5c}
    value=${value// /\\x20}
    value=${value//$'\t'/\\x09}
    value=${value//$'\n'/\\x0a}
    value=${value//%/%%}
    printf '%s' "$value"
}

write_unit() {
    local server_bin="$1"
    local tmp
    mkdir -p "$UNIT_DIR" "$SERVER_WORK_DIR" "$SERVER_FP_DIR" "$SERVER_CACHE_DIR" "$SERVER_STATE_DIR"
    tmp=$(mktemp "$UNIT_PATH.tmp.XXXXXX")
    {
        printf '%s\n' '[Unit]'
        printf '%s\n' 'Description=A-share capability proxy server'
        printf '%s\n' 'Wants=network-online.target'
        printf '%s\n' 'After=network-online.target'
        printf '\n%s\n' '[Service]'
        printf '%s\n' 'Type=simple'
        printf 'WorkingDirectory=%s\n' "$(systemd_path "$SERVER_WORK_DIR")"
        printf 'ExecStart=%s --host %s --port %s --fp-dir %s --cache-dir %s --state-dir %s\n' \
            "$(systemd_quote "$server_bin")" \
            "$(systemd_quote "$SERVER_HOST")" \
            "$(systemd_quote "$SERVER_PORT")" \
            "$(systemd_quote "$SERVER_FP_DIR")" \
            "$(systemd_quote "$SERVER_CACHE_DIR")" \
            "$(systemd_quote "$SERVER_STATE_DIR")"
        printf '%s\n' 'Environment=PYTHONUNBUFFERED=1'
        printf '%s\n' 'Restart=on-failure'
        printf '%s\n' 'RestartSec=5s'
        printf '%s\n' 'TimeoutStopSec=30s'
        printf '%s\n' 'NoNewPrivileges=yes'
        printf '%s\n' 'PrivateTmp=yes'
        printf '\n%s\n' '[Install]'
        printf '%s\n' 'WantedBy=default.target'
    } >"$tmp"
    chmod 0644 "$tmp"
    mv "$tmp" "$UNIT_PATH"
}

install_service() {
    local tool_bin_dir server_bin
    validate_config
    resolve_uv
    [[ "$SERVER_WORK_DIR" == /* ]] || die "SERVER_WORK_DIR must be an absolute path"
    # --no-cache：绕过 uv wheel 缓存。版本号未变时 --force 仍可能复用旧 wheel，
    # 导致改动不生效（实测踩坑）；双保护 = --no-cache + pyproject 版本号 bump。
    "$UV_BIN" tool install --force --no-cache "$PROJECT_DIR"
    tool_bin_dir=$("$UV_BIN" tool dir --bin)
    server_bin="$tool_bin_dir/asgk-server"
    [[ -x "$server_bin" ]] || die "uv installed asgk-server but binary not found at $server_bin"
    write_unit "$server_bin"
    systemctl --user daemon-reload
    systemctl --user enable "$UNIT_NAME"
    printf 'Installed and enabled %s.\n' "$UNIT_NAME"
    printf 'Service work directory: %s\n' "$SERVER_WORK_DIR"
    printf 'Start it with: %s run\n' "$0"
    printf 'Manual command: systemctl --user start %s\n' "$UNIT_NAME"
    printf '\nPoint asgk clients at it:\n  export ASGK_SERVER=http://%s:%s\n' "$SERVER_HOST" "$SERVER_PORT"
}

uninstall_service() {
    resolve_uv
    if [[ -f "$UNIT_PATH" ]]; then
        systemctl --user disable --now "$UNIT_NAME" >/dev/null 2>&1 || true
        rm -f "$UNIT_PATH"
        systemctl --user daemon-reload
        systemctl --user reset-failed "$UNIT_NAME" >/dev/null 2>&1 || true
    fi
    if "$UV_BIN" tool list | grep -q '^asgk-server '; then
        "$UV_BIN" tool uninstall asgk-server
    fi
    printf 'Uninstalled %s and its uv tool; service work directory was preserved.\n' "$UNIT_NAME"
}

command_name="${1:-}"
case "$command_name" in
    -h|--help|help)
        usage
        ;;
    install)
        require_systemd_user
        install_service
        ;;
    run)
        require_systemd_user
        systemctl --user start "$UNIT_NAME"
        systemctl --user is-active "$UNIT_NAME"
        ;;
    stop)
        require_systemd_user
        systemctl --user stop "$UNIT_NAME"
        ;;
    restart)
        require_systemd_user
        systemctl --user restart "$UNIT_NAME"
        systemctl --user is-active "$UNIT_NAME"
        ;;
    status)
        require_systemd_user
        systemctl --user status "$UNIT_NAME" --no-pager
        ;;
    uninstall)
        require_systemd_user
        uninstall_service
        ;;
    *)
        usage >&2
        exit 2
        ;;
esac
