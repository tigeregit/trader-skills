# TODO: P3 SKILL.md 路由层

来源：notes/design.md 轴 1 / 落地路线 P3

## 背景

拆分后需要一个精简的路由层把 references 和 scripts 串起来，这是 skill 的入口与触发点。按 ZCode progressive disclosure，路由层目标 < 300 行，只做「要什么数据 → 读哪个文件 / 调哪个脚本」。

## 待办

编写 `skills/a-stock-data/SKILL.md`：

- [ ] YAML frontmatter：`name`、`description`（主动式，写清做什么 + 何时触发，略带推力避免 under-trigger）。
- [ ] 端点路由速查表：移植上游「端点路由速查」表（§ → reference 文件 / asgk 函数 → 拿什么数据 → 数据源）。
- [ ] 数据源优先级总则：能用通达信/腾讯就不碰东财。
- [ ] 风控总则：东财/同花顺请求一律走网关（指向 `gateway-mvp.md` 产物）。
- [ ] 各层的「何时读哪个 reference」指引。

## 验收标准

- SKILL.md body < 300 行，不含任何长实现。
- 触发后 model 能仅凭路由表定位到正确的 reference 文件。
- `description` 字段在典型 A 股提问（如「查 688017 的估值」「北向今天流入了多少」）下能可靠触发。

## 依赖

- 依赖 `references-split.md`（路由表指向各 reference 文件）与 `scripts-library-port.md`。
