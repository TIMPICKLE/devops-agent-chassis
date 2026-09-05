# Current State Audit · 修订版 v2

> 审计基线：`bdd2b0a`；复核日期：2026-09-05。
> 范围：本地该提交的源码、示例、测试、装配说明与工作流。不是对所有生产部署、未合并分支或最新远端状态的认证。

> **历史基线文档**：以下观察描述 `bdd2b0a`，不是实现分支的最新状态。`feat/roadmap-showcase-v1` 已修复 F01/F02，并补上下文传递和模型适配；最新测试与未完成项见 [IMPLEMENTATION.md](./IMPLEMENTATION.md)。

## 1. 证据等级与测试口径

| 标记 | 含义 |
|---|---|
| 已实现 / 有测试 | 仓库存在实现与针对性测试，不代表覆盖所有运行条件 |
| 已复现 | 本轮最小程序实际观察到的问题 |
| 边界/覆盖不足 | 从调用路径能确定限制，但不能外推到所有装配或第三方 SDK |
| 尚未验证 | 本次未拿到相应 live、兼容性或生产证据 |
| 规划 | 未来建议，不是当前能力，也不自动表示当前设计有错 |

历史记录：2026-09-04 的工作会话在安装完整 MCP 可选依赖、处理宿主代理配置后报告 `43 passed`；仅基础依赖时为 `41 passed, 1 skipped`。这些是当时的执行记录，不是覆盖率、形式化证明或当前生产指标。

规划复核当时独立运行了下文两个纯内存复现，环境缺少 pytest，未重跑完整套件。后续实现已安装依赖并重跑；不要把这个历史环境描述当成当前阻塞。已有测试通过，仍可能遗漏重要契约行为。

## 2. 正确理解项目职责

依据 [AGENTS.md](../AGENTS.md)、[contracts.py](../src/agent_chassis/contracts.py) 和装配流程：

- Chassis 提供通用工程机制；Payload 提供任务源、完成判据及工具；Assembly 负责选择和接线。
- `decide/planner/solver` 等 callable 本来就是合法接入点，可以调用真实模型，也可以借用外部 Coding Agent。
- 核心不存在统一 ModelProvider，并不能证明“无法接模型”，也不构成必须修复的缺陷。
- 确定性 Demo 有正当用途。问题在于是否错误宣称其证明了真实模型的业务执行质量。
- 基础安装无第三方运行依赖；可选 MCP 实现会延迟导入第三方 SDK。“基础零依赖”不等于所有可选能力都不用第三方包。

准确成熟度描述：

> 已有可组合的装配骨架与离线验证基础；标准运行路径仍存在验收和清理缺陷，真实业务参考接入及复用收益需要独立证据。

## 3. 两个已复现的高优先级缺陷

### F01 · with_payload 的验收器可能不执行

位置：

- [chassis.py](../src/agent_chassis/chassis.py)：`with_payload()` 保存 `_criteria`，`build()` 检查其存在，运行时却直接使用 orchestrator 的结果。
- [orchestration/__init__.py](../src/agent_chassis/orchestration/__init__.py)：各编排器另持有可选 criteria；`_finish()` 在 criteria 缺失时可判为成功。

触发：只在 `with_payload(source, criteria)` 配置一个永远拒绝的判据，编排器省略其可选 criteria。

观察：`outcome=succeeded; criteria_calls=0; verdict=None`。

风险：装配报告列出完成判据，但这次运行没有使用它。两个入口配置不同判据时，也缺少一致性检查。

基线修复要求（B01，当前实现分支已补核心修复）：

- 定义单一权威验收来源，或装配期强制接线并验证一致性。
- 缺失/冲突不能静默成功；处理内置编排器与自定义编排器。
- 避免重复调用有外部读取的判据；统一调用或显式传递验收结果。
- 明确独立使用编排器与通过 Chassis 使用时的兼容行为。

### F02 · 判据返回失败时没有运行 cleanup

位置：[chassis.py](../src/agent_chassis/chassis.py) 的 `_run_task()`，以及 [failure.py](../src/agent_chassis/failure.py) 的 `on_failure()`。

观察：异常路径进入失败策略；但正常返回的 `Outcome.FAILED` 只执行 `remember()`。即使已经注册补偿，也不执行。

触发：一个步骤修改内存状态；判据返回 False；注册了恢复状态的 cleanup。

观察：`outcome=failed; criteria_calls=1; residue=True; cleanups=0`。

基线修复要求（B02，当前实现分支已补修复）：

- 判据否决必须执行约定的终态清理，并保留原始 verdict。
- 清理不等于重试，继续保留“确定性否决不触发整链重试”的现有约定。
- 终态、记账、补偿次数可验证；清理失败独立报告。
- 仅处理任务拥有的资源，不能清除原有工作区改动。

现有 `test_failure_leaves_nothing_behind` 主要覆盖“没有产生变更”的情况，没有证明“产生错误变更后再失败”能被清理。

### 可复现程序：只使用内存，不改文件或访问网络

仅用于在 `bdd2b0a` 的独立检出中复现历史缺陷，**不要在修复分支上将它作为通过门槛**。当前分支运行 `python -m pytest tests/test_roadmap_contracts.py -q`，不应为保持旧断言通过而恢复缺陷。

```bash
PYTHONDONTWRITEBYTECODE=1 PYTHONPATH=src python - <<'PY'
from agent_chassis import Chassis
from agent_chassis.contracts import Task, TaskSource, DoneCriteria, Verdict
from agent_chassis.orchestration import StateMachineOrchestrator, FnStep
from agent_chassis.failure import ZeroSideEffectPolicy

class Source(TaskSource):
    def fetch(self, limit=1):
        return [Task("audit-probe", "review")]

class Reject(DoneCriteria):
    def __init__(self):
        self.calls = 0

    def judge(self, task, ctx):
        self.calls += 1
        return Verdict(False, "always reject")

criteria = Reject()
first = (
    Chassis("criteria-probe")
    .with_orchestrator(StateMachineOrchestrator([]))
    .with_payload(Source(), criteria)
    .build()
)
result = first.run_once()
assert result.outcome.value == "succeeded" and criteria.calls == 0
print("F01", result.outcome.value, criteria.calls, result.verdict)
first.close()

criteria = Reject()
state = {"residue": False, "cleanups": 0}

def mutate(task, ctx):
    state["residue"] = True

def cleanup(task, ctx):
    state["residue"] = False
    state["cleanups"] += 1

policy = ZeroSideEffectPolicy()
policy.register_cleanup("undo-mutation", cleanup)
second = (
    Chassis("cleanup-probe")
    .with_orchestrator(StateMachineOrchestrator(
        [FnStep("mutate", mutate)], criteria
    ))
    .with_payload(Source(), criteria)
    .with_failure_policy(policy)
    .build()
)
result = second.run_once()
assert result.outcome.value == "failed" and criteria.calls == 1
assert state == {"residue": True, "cleanups": 0}
print("F02", result.outcome.value, criteria.calls, state)
second.close()
PY
```

## 4. 需要修订或验证的其他行为

| ID | 证据 | 精确结论 | 后续处理 |
|---|---|---|---|
| F03 | Scheduler 返回文本；默认调用点丢弃返回值 | 默认路径只完成收集与审计，未保证消费者收到文本；自定义 adapter 可以自行消费 | B03 建传递协议和接收断言 |
| F04 | ToolBox.schema 只含 name/description；call 直接执行 callable | 缺少统一参数验证/授权接线；不代表不能在边缘 adapter 校验 | B04 按实际接入路径补验证 |
| F05 | DoneCriteria 收到完整可变 RunContext | “只读 facts”是规范，不是类型或进程安全边界 | B01/B03 收紧输入；独立验证器保护证据 |
| F06 | LLMCompiler 对每个 wave 再逐节点循环 | 能做 DAG 分波，不是实际同波并发；其 docstring 已提示同步演示限制 | 立即准确标注；有性能需求再做 C04 |
| F07 | PlanExecute 调 planner 后直接调用工具；ReAct 到上限只写 note | 不能把循环计数当真实模型调用数；“强制收敛”不等于达标 | B05 修正统计/停止原因，不要求先实现全部模式 |
| F08 | Subgraph 在 _finish 前运行 delivery | 末尾 DoneCriteria 不是自动的交付前门禁；先交付是否合理取决于 payload | B04/B05 分开质量门禁、交付授权与事后确认 |
| F09 | 装配说明含旧路径/旧 Ledger 说明 | 部分说明与当前源码不同，如 reasoning 实际位于 orchestration 下，Ledger 已有 __bool__ | B01 同步能力/装配文档，按实际签名生成 |

F05 的示例细节：`WorkspaceChangedCriteria` 的 done 判断来自 FakeRepo.diff，失败 evidence 却读了 `ctx.model_notes`。不能据此说模型已经控制 done；应说“当前签名和示例没有严格隔离模型说明”。

另外，真正的代码修复判据不能只有“存在 diff”。必须验证目标问题、既有回归、允许变更范围，以及验证规则没有被候选补丁削弱。

## 5. 能力与证据矩阵

| 能力 | 当前有的证据 | 当前不能宣称 |
|---|---|---|
| 链式装配、组件替换 | Chassis / Registry / 多编排测试 | 配置了 criteria 就一定执行，F01 是反例 |
| Payload 分离 | 两个示例和换载荷测试 | 已证明任意新场景零成本接入 |
| 推理模式 | 回调式控制结构、证据变量、DAG 分波 | 各模式必然产生相应次数的真实模型调用 |
| 知识时机 | 六个枚举、确定性路由、注入 Trace | 标准路径已把知识送到执行器 |
| MCP 传输 | stdio / HTTP adapter 与本地真实 Server 测试 | 全协议兼容、全部授权流程已测 |
| 权限 | Capability/PermissionBoundary，显式 check 可拒绝 | 任意 Python 插件或外部 Shell 不能绕过 |
| 失败处理 | 异常清理、有限重试、终态去重 | 所有失败分支均清理、所有外部副作用可撤销 |
| 可观测 | 通用 task/trace/tool_call/health、Connector run 关联 | 默认全面脱敏、抗篡改、生产 SLO 已成立 |
| LLM 装配工作流 | 工作流和验证器代码，生成范围及 Secret 检查 | 静态审阅等于本轮已运行远程工作流 |
| 运行期 AI 接入 | Callable 与外部执行器 seam，Skill 有接入流程 | 当前仓库已证明真实业务模型的质量或全部 adapter 行为 |
| 持久恢复/HITL | 本地 Ledger、同步 interrupt callback | 已支持跨进程挂起/恢复；Ledger 等于 checkpoint |
| 生产统计 | README 指向参赛材料 | 已审计原始执行数、采纳率、净收益 |

## 6. Live Assembly 应如何表述

[assembly_answers.md](../tests/live_assembly/assembly_answers.md) 明确允许确定性 decider 和 mock Connector，目的是验证“LLM 是否能从访谈装配工程”。

因此：

- 这是有价值的装配测试，不是错误设计。
- Provider 字段记录配置，不保证生成员工在业务运行时调用 Provider。
- 工作流代码存在，不等于本轮调用了真实 Provider，也不等于所有历史运行均通过。
- 本次未取得每个生成制品的审计证据，不能一概断言所有历史生成员工都没有模型调用；准确结论是“现有验收不要求、也不证明运行期模型调用”。
- 生成文件范围检查和 Prompt 要求不是 OS 级沙箱；实际写入途径、Shell、子进程与凭据需独立限制。
- 下一步保留确定性 Golden Scenario，另增运行期接入测试，不把前者改成随机分类以求“看起来更 AI”。

## 7. MCP：区分 adapter、SDK 与未覆盖能力

| 层 | 已观察到的内容 | 核查责任 |
|---|---|---|
| Chassis adapter | 创建官方 Client、tools discovery/call、分页、错误传播、静态 headers、Trace | 检查本地接线、身份映射和策略 |
| 官方 SDK | adapter 委托其负责协议与传输行为 | 固定 SDK 版本，检查实际 capability/version 协商及验证行为 |
| 未暴露/未测试路径 | Tasks、elicitation、resources/prompts、动态工具变更、复杂授权等 | 先确定业务需要和 SDK 覆盖，再决定是否开发 |

没有手写协议逻辑不能直接记为缺失，避免重复实现 SDK。也不能因为用了 SDK 就宣称全协议兼容。

工具名关键词 fallback 对只读兼容可能方便；有副作用操作应精确匹配并校验服务端身份/Schema，不能自动猜另一个写工具。

历史 HTTP 集成测试曾因宿主 SOCKS 代理及客户端可选依赖而在初始化失败。这是环境/代理政策测试缺口，不能据此认定 MCP HTTP 协议实现失效；也不应永久要求用户移除企业代理。建议测试显式管理 loopback/proxy 条件，生产遵守组织网络政策。

## 8. 安全边界与项目卫生

### 安全与恢复

- 可信插件的误接线：可用 Schema、集中调用入口和合同测试降低风险。
- 不可信代码/外部执行器：需要进程、文件系统、网络、资源和凭据边界；不能只依赖 Python 方法。
- 验证器：固定测试和验收配置，不能由候选代码修改后自证；哈希仅检查内容一致性。
- Ledger：已有进程内锁；未提供跨进程原子认领、租约与完整 checkpoint。不要说“完全没有锁”。
- 重试：可能重跑编排中的已完成动作；对写入结果不明的情况必须查询、幂等或转人工。
- Observer：记录工具参数但缺少统一默认脱敏策略；普通遥测故障可按策略降级，高风险授权审计失败应停止对应动作。

### 项目卫生

- README 末尾的 MCP 状态落后于真实传输实现。
- 基础包声明 MIT，但该基线未包含独立 LICENSE 文件；应补仓库治理材料。
- Actions 标签和 Claude Code latest 使证据难以完全复现，适合固定版本并记录升级过程。
- 证据报告可以先加版本/哈希和脱敏；不必先建设完整 SBOM、SLSA 与员工注册中心。
- 任何公开生产数字应有时间窗、分母、去重、成功定义、原型与生产系统版本映射；可脱敏，不要求把公司敏感资料公开。

## 9. 审计结论

近期优先级是让现有承诺成立，而不是为“前瞻性”增加更多组件。F01/F02 是已复现的正确性问题；F03–F09 涉及数据路径、语义和覆盖边界；模型 adapter、持久化平台及开放协议应按真实使用需求逐步验证。

本文件列出修复要求，不代表代码已修复。未来合并修复后应更新基线、附回归结果，再调整能力状态。
