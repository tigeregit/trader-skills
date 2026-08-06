#!/usr/bin/env bash
set -Eeuo pipefail

SCRIPT_DIR=$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")" && pwd)
PROJECT_DIR=$(cd -- "$SCRIPT_DIR/.." && pwd)
UNIT_NAME="sgw.service"
UNIT_DIR="${XDG_CONFIG_HOME:-$HOME/.config}/systemd/user"
UNIT_PATH="$UNIT_DIR/$UNIT_NAME"
SERVICE_HOME="${XDG_DATA_HOME:-$HOME/.local/share}/sgw"

SGW_WORK_DIR="${SGW_WORK_DIR:-$SERVICE_HOME}"
SGW_HOST="${SGW_HOST:-127.0.0.1}"
SGW_PORT="${SGW_PORT:-7700}"
SGW_FP_DIR="${SGW_FP_DIR:-$SGW_WORK_DIR/fingerprints}"
SGW_CACHE_DIR="${SGW_CACHE_DIR:-$SGW_WORK_DIR/cache}"
SGW_STATE_DIR="${SGW_STATE_DIR:-$SGW_WORK_DIR/state}"

usage() {
    cat <<'EOF'
Usage: sgw-service.sh <command>

Commands:
  install    Install sgw globally with uv tool, then write and enable the unit
  run        Start the systemd user service
  stop       Stop the systemd user service
  restart    Restart the systemd user service
  status     Show service status
  uninstall  Remove the unit and uv tool; keep the service work directory

Optional environment variables used by install:
  UV_BIN, SGW_WORK_DIR
  SGW_HOST, SGW_PORT, SGW_FP_DIR, SGW_CACHE_DIR, SGW_STATE_DIR

Manual management after install:
  systemctl --user start|stop|restart|status sgw.service
  journalctl --user-unit sgw.service -f
EOF
}

die() {
    printf 'sgw-service: %s\n' "$*" >&2
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
    [[ "$SGW_PORT" =~ ^[0-9]+$ ]] || die "SGW_PORT must be an integer"
    ((SGW_PORT >= 1 && SGW_PORT <= 65535)) ||
        die "SGW_PORT must be between 1 and 65535"
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
    local proxy_bin="$1"
    local tmp
    mkdir -p "$UNIT_DIR" "$SGW_WORK_DIR" "$SGW_FP_DIR" "$SGW_CACHE_DIR" "$SGW_STATE_DIR"
    tmp=$(mktemp "$UNIT_PATH.tmp.XXXXXX")
    {
        printf '%s\n' '[Unit]'
        printf '%s\n' 'Description=A-share shared traffic gateway'
        printf '%s\n' 'Wants=network-online.target'
        printf '%s\n' 'After=network-online.target'
        printf '\n%s\n' '[Service]'
        printf '%s\n' 'Type=simple'
        printf 'WorkingDirectory=%s\n' "$(systemd_path "$SGW_WORK_DIR")"
        printf 'ExecStart=%s --host %s --port %s --fp-dir %s --cache-dir %s --state-dir %s\n' \
            "$(systemd_quote "$proxy_bin")" \
            "$(systemd_quote "$SGW_HOST")" \
            "$(systemd_quote "$SGW_PORT")" \
            "$(systemd_quote "$SGW_FP_DIR")" \
            "$(systemd_quote "$SGW_CACHE_DIR")" \
            "$(systemd_quote "$SGW_STATE_DIR")"
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
    local tool_bin_dir proxy_bin
    validate_config
    resolve_uv
    [[ "$SGW_WORK_DIR" == /* ]] || die "SGW_WORK_DIR must be an absolute path"
    # --no-cache：绕过 uv wheel 缓存。版本号未变时 --force 仍可能复用旧 wheel，
    # 导致改动不生效（实测踩坑）；双保护 = --no-cache + pyproject 版本号 bump。
    "$UV_BIN" tool install --force --no-cache "$PROJECT_DIR"
    tool_bin_dir=$("$UV_BIN" tool dir --bin)
    proxy_bin="$tool_bin_dir/sgw-proxy"
    [[ -x "$proxy_bin" ]] || die "uv installed sgw but sgw-proxy was not found at $proxy_bin"
    write_unit "$proxy_bin"
    systemctl --user daemon-reload
    systemctl --user enable "$UNIT_NAME"
    printf 'Installed and enabled %s.\n' "$UNIT_NAME"
    printf 'Service work directory: %s\n' "$SGW_WORK_DIR"
    printf 'Start it with: %s run\n' "$0"
    printf 'Manual command: systemctl --user start %s\n' "$UNIT_NAME"
}

uninstall_service() {
    resolve_uv
    if [[ -f "$UNIT_PATH" ]]; then
        systemctl --user disable --now "$UNIT_NAME" >/dev/null 2>&1 || true
        rm -f "$UNIT_PATH"
        systemctl --user daemon-reload
        systemctl --user reset-failed "$UNIT_NAME" >/dev/null 2>&1 || true
    fi
    if "$UV_BIN" tool list | grep -q '^sgw '; then
        "$UV_BIN" tool uninstall sgw
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
