## Commit Rule

### commit format

Follow this convention for commit messages:

```
<type>([scope]): <subject>
```

- Types: `feat`, `fix`, `improve`, `refactor`, `test`, `docs`, `style`, `chore`, `revert`, `upgrade`, `log`, `debug`
- Scope is recommended (module name, e.g. `PointClouds`, `OnlineChief`)
- Subject: imperative mood, lowercase start, max 30 chars, no period
- Combine at most two types with `&` if needed: `refactor&feat(RenderMask): ...`

### commit scope rules

- One commit = one logical reason to change
- If a commit description requires "and", split it into multiple commits
- Commits must leave the codebase in a working state — never commit broken intermediate states

## Project
本项目是为了赋予agent有stock信息收集能力和trader操作的能力，本项目不会定义具体的交易风格和方法，仅提供能交易和获取相关信息的技能。并且本项目skill将会在相同IP下为100～1000个agent使用，因此需要考虑流量管控问题。
skills/ 放所有直接可用的skill
ref/ 放submodule，拒绝从零开始，ref是相似功能的参考。

### 测试方法 [TODO]
使用pi agent的测试，需要查看pi agent的官网



