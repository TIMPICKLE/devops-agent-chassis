# 给 AI 助手的仓库指引

这是一个零第三方依赖的 Python 工程底盘（framework）：底盘五大系统
（编排 / 接入 / 知识注入 / 失败契约 / 可观测）一行不改，业务方只写
**载荷**（TaskSource + DoneCriteria + 工具集）和**装配脚本**。

## 用户要装配一台数字员工时

按 [.claude/skills/assemble-digital-employee/SKILL.md](.claude/skills/assemble-digital-employee/SKILL.md)
的访谈流程执行：逐组询问用户（载荷 → 编排 → 知识注入 → 失败契约 → 权限 → 可观测），
生成载荷与装配脚本，然后**实际运行验证**。全部形态都支持：
SubgraphOrchestrator 多支路、LLMCompiler DAG、按支路挂不同推理模式、人工介入点。

## 硬约束（任何改动都不能破坏）

- `DoneCriteria.judge` 只读 `ctx.facts` 与外部系统状态，绝不读 `ctx.model_notes`
- 底盘包 `src/agent_chassis/` 不 import 第三方库、不出现业务概念
- 执行器权限默认只授 `repo.read`/`repo.write`，不可逆能力由确定性代码持有
- `InjectionPoint.AGENT_BOOT` 刻意留空

## 常用命令

```bash
python -m pytest tests/ -q      # 回归测试
python examples/04_swap_payload.py   # 换载荷演示
```
