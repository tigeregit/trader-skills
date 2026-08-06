# 安装与启动

skill 只含文档，实际取数靠 `asgk` CLI + `asgk-server` 服务端。两者打包在同一个
`asgk-server` 包里，一次 `uv tool install` 同时装出两个二进制。

## 一、克隆仓库（skill 通常只拿到 skills/ 子目录，需 clone 全仓装服务端）

```bash
# 创建临时目录，clone 全仓（含 packages/asgk-server）
mkdir -p /tmp/asgk-setup && cd /tmp/asgk-setup

# GitHub（海外）
git clone https://github.com/tigeregit/trader-skills.git

# 或 Gitee（国内，推荐——出网到数据源本身在国内，clone 也快）
git clone https://gitee.com/suncebf1998/trader-skills.git

cd trader-skills
```

> clone 后可保留这个临时目录，后续 `uninstall`/`restart` 还要用到 service 脚本。

## 二、一键安装并启动（推荐）

```bash
./packages/asgk-server/scripts/asgk-server-service.sh install
```

`install` 做的事：
1. `uv tool install` 装包 → `~/.local/bin/` 出现 `asgk-server` + `asgk` 两个 bin
2. 生成 `~/.config/asgk/cli.toml`（CLI 据此找到服务端）
3. 自动选择后端并启动服务：
   - **Linux + systemd** → 注册为 systemd user service（开机自启、崩溃重启）
   - **macOS / WSL / 容器**（无 systemd）→ nohup 后台进程

装完即可用：
```bash
asgk --list                  # 列出 9 大类 × 子命令
asgk quote realtime 600519   # 茅台实时行情
```

## 三、后端机制：systemd vs background

脚本自动检测，也可用 `ASGK_BACKEND` 强制指定。

### systemd 模式（Linux 默认）

服务交给 systemd 托管，具备开机自启、崩溃自动重启（`Restart=on-failure`）、
`KillMode=control-group`（退出时杀整个进程组，不留孤儿）。

```bash
./packages/asgk-server/scripts/asgk-server-service.sh status   # 状态
./packages/asgk-server/scripts/asgk-server-service.sh restart  # 重启
./packages/asgk-server/scripts/asgk-server-service.sh logs     # 实时日志
# 或直接用 systemctl
systemctl --user status asgk-server.service
journalctl --user-unit asgk-server.service -f
```

### background 模式（无 systemd 时）

用 `setsid nohup` 脱离终端后台运行，日志写到 `~/.local/share/asgk-server/server.log`。

```bash
ASGK_BACKEND=background ./packages/asgk-server/scripts/asgk-server-service.sh install
ASGK_BACKEND=background ./packages/asgk-server/scripts/asgk-server-service.sh status
ASGK_BACKEND=background ./packages/asgk-server/scripts/asgk-server-service.sh logs
```

**单例保障（防止多开）**——background 模式有三重保护确保一台机器只有一个实例：

1. **PID 文件**（`~/.local/share/asgk-server/bg.pid`）：启动前检查上次记录的 PID
   是否仍存活且是 asgk-server 进程（防 PID 复用，读 `/proc/$pid/cmdline` 验证）。
2. **端口探活**：PID 文件可能丢失，启动前额外探测端口是否已被监听。
3. **flock 文件锁**（`bg.lock`）：串行化 start 的检查+启动临界区，防并发启动竞态。
   锁 fd 在启动 server 前显式关闭（`9>&-`），防 server 子进程继承锁导致死锁。

任一保护命中即跳过启动并提示「已在运行」。如确认无实例但锁/PID 残留：
```bash
rm ~/.local/share/asgk-server/bg.pid ~/.local/share/asgk-server/bg.lock
```

## 四、CLI 如何找到服务端

优先级（从高到低）：

1. 环境变量 `export ASGK_SERVER=http://127.0.0.1:7701`（最高，临时覆盖用）
2. `~/.config/asgk/cli.toml`（`install` 时自动生成）
3. 包内默认 `cli.toml.default`（`http://127.0.0.1:7701`）

改端口/地址：编辑 `~/.config/asgk/server.toml` 或 `export ASGK_SERVER=...`。

## 五、只装 CLI（服务端已在别处部署）

若服务端由部署方统一提供（如共享的 asgk-server），本机只需 CLI：

```bash
uv tool install /tmp/asgk-setup/trader-skills/packages/asgk-server
# 或从 git
uv tool install git+https://gitee.com/suncebf1998/trader-skills.git#subdirectory=packages/asgk-server

# 指向远端服务端
export ASGK_SERVER=http://<server-host>:7701
asgk quote realtime 600519
```

## 六、卸载

```bash
./packages/asgk-server/scripts/asgk-server-service.sh uninstall
```

停掉运行中的实例（systemd 或 background）、移除 uv tool（两个 bin）、提示 CLI 配置
残留（`~/.config/asgk/cli.toml`，因可能手动改过，不自动删）。工作目录
（`~/.local/share/asgk-server/`，含缓存/状态）保留。

## 常见问题

- **`asgk` 命令找不到**：确认 `~/.local/bin` 在 `$PATH` 里（`uv tool install` 装到那）。
- **报「服务端不可达」**：先 `asgk-server-service.sh status` 看服务是否在跑。
- **systemd 模式下端口冲突**：`SERVER_PORT=7702 ./...service.sh install` 换端口。
- **xlsx 格式报错**：`uv tool install "asgk-server[xlsx]"` 装可选依赖 pandas/openpyxl。
