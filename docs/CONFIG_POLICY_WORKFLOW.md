# 从项目规范到可验收的部署配置

用户说：**“请按 Atlas 项目的生产规范修复 deployment.json。峰值 219 RPS，其余配置保持原样。”**

下面是已经实现的工作流，入口为 [`tools/run_policy_workflow.py`](../tools/run_policy_workflow.py)。仅 `repair_config` 节点使用模型。

| 节点 | 负责什么 | 逻辑来源 |
|---|---|---|
| prepare | 进入准备阶段，使用任务提供的项目、环境、负载和原始文件 | 装配函数中的确定性 Python |
| select_context | 设置 repair 阶段，使匹配规范可被注入 | 显式写入 `ctx.facts["phase"]` |
| repair_config | 读取规范、计算配置、提交候选；依据反馈修正，合格后提前停止 | 共用模型适配器与 ReAct |
| verify_config | 清空修复阶段知识快照，重新检查候选并记录 hash | 独立判据；最终 DoneCriteria 再检查当前产物 |

成功后输出完整 JSON、差异 patch、装配清单和运行证据。**不连接集群，不实际发布。** `trial-NNN.candidate.json` 的内容就是修复后的 `deployment.json`。

## 知识怎样进入正确过程

装配中为每份规范声明 `ScopedKnowledge(text, name=..., version=..., task_scope=..., fact_scope=...)`：

- 项目规范匹配 `task_scope={"project": "atlas"}`，提供每副本容量、CPU 和端口。
- 环境规范匹配 `task_scope={"environment": "production"}`，提供冗余副本、最低副本、超时和滚动批次。
- 两者都设置 `fact_scope={"phase": "repair"}`，默认只在 `BEFORE_EXECUTOR` 注入。

模型适配器每次请求前收集，再通过 `context_for` 消费；两份文档分别保留版本、内容 hash、消费与遗漏记录。阶段由装配显式声明，不是底盘根据节点名字猜测。当前使用属性匹配，不是向量检索。

本例项目容量为 73 RPS/副本；生产环境保留 2 个副本且至少 4 个。模型应算出 `max(4, ceil(219/73)+2) = 5`，并采用同一规范中的 CPU、端口等参数。任务原文只有负载和通用公式，没有这些项目专属值。

判据从冻结规范独立计算期望配置，检查 JSON 重复键、类型、原字段保留和产物 hash。模型说“完成”不会让任务成功；工具不会把正确配置返回给模型。

## 怎样验证知识的作用

[`benchmarks/config_policy/v1.json`](../benchmarks/config_policy/v1.json) 冻结四个公开合成案例：两个项目 × 两个环境。对每个案例保持模型、提示词、工具、判据和调用上限一致，只改变上下文：

| 策略 | 注入的规范 |
|---|---|
| routed | 匹配项目和环境的两份文档 |
| full | 全部四份文档，正文与 routed 完全相同，保留适用范围 |
| none | 不注入文档 |

默认每种组合重复两次，共 24 次运行，固定种子打乱顺序。首次请求前冻结完整计划、数据快照、模型参数与源码版本；逐次保存证据，未完成试次保留在分母。报告单列执行异常，不能把网络或服务异常当成知识的作用。

独立核验器不调用模型，会重算输入身份、规范选择、上下文预算、配置判据、差异、节点轨迹与 token 合计。离线回放正确配置，**只验证合同，不能比较 AI 能力**。

```bash
python -m pip install -e ".[dev,llm]"
python tools/run_policy_workflow.py --mode offline --repeats 1 --max-calls 2 --output-dir reports/config-offline
python tools/verify_policy_workflow.py reports/config-offline

# 先在运行环境设置 BIGMODEL_API_KEY；真实模式不退回离线模式。
python tools/run_policy_workflow.py --mode live --repeats 2 --max-calls 2 --output-dir reports/config-live
python tools/verify_policy_workflow.py reports/config-live --require-live
```

支持 `--protocol openai` 和已有模型、预算参数。每次实验固定一个协议，不把不同条件合并为因果结论。

Actions 先运行 Python 3.9 / 3.13 回归，再执行 24 个真实模型试次，最多 48 次请求。工作流与产物链接见[第三阶段记录](../changelog/stage-03.md)。基线未通过验收属于有效测量；routed 未全通过、执行异常或独立核验失败会使工作流失败。

四个案例是公开小样本，重复不增加独立任务数，没有企业私有保留集。完全结构化规则也能由确定性脚本实现；本例验证知识路由与模型流程的结合，不证明 AI 优于脚本。生产配置格式和企业真实规则仍需业务方接入。
