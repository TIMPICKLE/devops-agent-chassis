# 生产格式证据检查（T04）

基础 v1 报告继续可用。生产格式检查是显式 opt-in：检查报告是否完整且内部一致，**不认证模型响应真伪、不重跑业务验收、不自动证明可上线**。
本版有效执行配置/并发统计覆盖 ReAct（可嵌入不同外层流程），并未声称所有其他推理模式都具备相同遥测。

## 装配前后怎么接线

```python
from agent_chassis.evidence import EvidenceObserver, assembly_manifest
from agent_chassis.production import source_revision

# chassis、config 是已经装配的对象；observer 必须已通过 chassis.observe() 注册。
manifest = assembly_manifest(chassis.report(), runtime={
    "mode": observer.mode,  # 来自实际 decider.execution_mode，而不是手工伪造 live
    "source": source_revision(repository_path),
    "executors": {"react": config.react_options()},
    "required_checks": ["your_objective_check"],
})
observer.bind_manifest(manifest, production=True)
```

`source_revision()` 返回完整 Git commit、dirty 状态；有修改时还返回 tracked/untracked（排除 ignored）变更的指纹，不输出差异内容或文件名。
从子目录调用也按仓库根目录计算。它不是原子快照或签名，不涵盖 ignored 文件、外部服务、依赖版本和所有子模块内容；执行期间不要另行修改装配源码。
`bind_manifest()` 在任务运行前检查必要元数据、模式和版本一致性，不对业务标准做判断。多个 ReAct 执行器应设置不同 `execution_key`，在 `executors` 中分别声明各自限制。

## 验证回执

准备阶段先记录预期检查尚未执行，真正完成确定性检查后再更新：

```python
ctx.record_check("your_objective_check", "not_run")
# check_passed 和 result_digest 来自实际检查，不来自模型自述。
ctx.record_check("your_objective_check", "passed" if check_passed else "failed",
                 evidence_refs={"result_sha256": result_digest})
```

回执保留尝试编号。最终尝试中，同名检查的最后一条回执用于报告一致性检查；上次尝试的通过不能覆盖本次失败或未执行。
生产报告需有所有 `required_checks` 的当前尝试回执；`passed` 必须附非空证据引用，成功结果不得包含必需检查的 `failed` / `not_run`。
失败任务可以合法记录未执行检查。DoneCriteria 仍独立裁定任务成功与否，回执本身不会把任务标成成功。引用由载荷提供，不能含凭据；通用检查器不自动下载或验证引用目标的内容。

## 实际并发统计

每次进入 ReAct 记录最终执行限制和当时的并行安全工具集合。每个已进入调度的批次记录：

| 字段 | 含义 |
|---|---|
| `requested` | 批次请求的动作总数，不保证都能启动 |
| `started` | 实际进入工具工作函数的动作数 |
| `succeeded` / `failed` | 已结束动作结果，排除嵌套 Connector 重复记账 |
| `peak_in_flight` | 执行器侧实际最大同时在途工具数 |
| `parallel_observed` | 是否有批次实际峰值大于 1，由实测计数推导 |

模型请求或全批预检失败不会虚构一个已执行批次；线程池部分提交失败仍记录真正运行的动作。
配置为 4、仅执行两个单调用时，`parallel_observed` 仍为 false。工作函数可能在等待串行的 Connector，因此峰值不能证明远端服务内部并发。
计数、调用 ID、批次/执行器关联、配置上限和汇总标志会被交叉检查，但这不是针对恶意伪造者的防篡改日志。

## 验证入口

```bash
python tools/verify_production_evidence.py run.manifest.json run.evidence.json
# 同时要求真实模型模式（不能仅靠标签证明请求真实性）：
python tools/verify_production_evidence.py run.manifest.json run.evidence.json --require-live
```

程序接口：`verify_documents(manifest, evidence, require_production=True)`；与 `require_live=True` 独立组合。
原 `verify_roadmap_evidence.py` 的目录/patch 校验流程保留，也可增加 `--require-production`。非补丁业务使用上面的通用入口。

完整、无需凭据的可运行配方：[examples/07_verified_assembly.py](../examples/07_verified_assembly.py)。示例明确使用 test-decider，验证生产**格式**而非生产模型效果。
新 schema 接受旧 v1 报告；旧版严格 schema 的消费者需更新后才能读取新增可选字段。旧报告缺乏必要元数据时仍可通过基础检查，但不能通过生产格式检查。
