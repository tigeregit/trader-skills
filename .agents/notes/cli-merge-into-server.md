# CLI 并入 asgk-server 包的设计决策

**日期**：2026-08-07
**状态**：已实施（refactor/capability-proxy 分支）
**背景**：原 `asgk` CLI 在 `skills/a-stock-data/scripts/asgk/`（独立包，自带客户端库 + 35 个 legacy 回退函数），与 `packages/asgk-server` 是两个分离的 uv 包。本次 refactor 把 CLI 并入 asgk-server 包。

## 核心决策

### 1. CLI 形态：纯 HTTP 客户端（不搬业务库）

CLI **不依赖**任何业务函数库，直接 `POST /v1/<capability>` 调服务端。理由：
- 旧客户端库的 legacy 回退（`em_get`/`ASGK_GW`/sgw）已实质失效（sgw 未部署、ASGK_GW 未配置 → 回退必报错），保留只会产生误导（测试时 13 个 FAIL 全是 `ASGK_GW 未设置`，混淆真故障）。
- 纯 HTTP 客户端让 asgk-server 包依赖不膨胀（不加 mootdx/pandas/mini-racer）。

### 2. 命令结构：9 大类 × 子命令两层

`asgk <大类> <子命令> [参数]`，共 9 大类 64 子命令。将原 18 个细域合并：
行情/基本面/研报/资金/信号/事件/风控/资讯/衍生。

子命令→能力的映射是**手工维护的映射表**（`cli/commands.py` 的 `COMMANDS`），而非自动从服务端 registry 生成。理由：服务端能力是粗粒度（如 `mootdx` 含 5 种），CLI 要细粒度子命令，必须有一份「子命令 → 能力 + 固定参数」映射。放 CLI 侧是因为这是**用户交互层**的关切，不应污染服务端 CapabilityMeta。

有测试 `test_mapped_capabilities_exist_in_server` 防止映射表与服务端 registry 漂移。

### 3. 端口配置：独立 cli.toml + 环境变量优先

优先级：`ASGK_SERVER` 环境变量 > `~/.config/asgk/cli.toml` > 包内默认 `cli.toml.default`（7701）。
service 脚本 `install` 时自动生成 `~/.config/asgk/cli.toml`，实现「装完即用，零配置」。

### 4. skill 纯文档化

`skills/a-stock-data/scripts/` 整个删除（客户端库 + 35 legacy + 19 测试 + .venv）。
skill 只剩 `SKILL.md` + `references/`，纯文档描述「要什么数据 → 敲哪个 CLI」。

## 关键文件

- `packages/asgk-server/asgk_server/cli/`：CLI 全部实现（commands/config/client/format/local/__init__）
- `packages/asgk-server/pyproject.toml`：`[project.scripts]` 注册 `asgk-server` + `asgk` 两个 bin
- `packages/asgk-server/tests/test_cli.py`：34 个测试覆盖命令发现/绑定/调用/格式化

## 不变量

- 服务端 21 个能力实现**完全不动**（single source of truth）
- 服务端 registry 结构**不加 CLI 字段**（CapabilityMeta 保持纯净）
- `packages/sgw` 保留（DEPRECATED，仅作历史参考）
