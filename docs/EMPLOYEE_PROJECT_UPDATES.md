# 规范变了，如何更新数字员工？

目标：保留已生成的员工项目和团队修改，只更新改变的知识内容，然后重新验证。对应 [Roadmap N05](../roadmap/BACKLOG.md)，实际结果见[第五阶段](../changelog/stage-05.md)。

运行前在仓库根目录安装 `python -m pip install -e ".[llm]"`。预览和应用无需模型 Key；后续真实任务运行需要 `BIGMODEL_API_KEY` 和 `c++` 编译器。下列大写路径是占位符，请替换成自己的目录。

## 输入和输出

- `--project`：未修改的基线项目；可来自[项目生成命令](GENERATED_EMPLOYEE_PROJECT.md)，也可来自上一个更新版本。
- `--working-copy`：团队维护的副本，允许员工代码、说明和自定义文件变化。
- `--request-dir`：与基线相同的 `request.json`，以及更新后的知识文件内容。
- `--plan`：先输出 JSON / Markdown 影响预览，应用时读取同一计划。
- `--output-dir`：新的输出目录；不覆盖输入，基线保留，便于继续运行或回退。

```bash
python tools/update_employee.py --project PROJECT_BASE --working-copy PROJECT_WORKING --request-dir UPDATED_REQUEST --plan reports/employee-change/plan.json --require-live
python tools/update_employee.py --project PROJECT_BASE --working-copy PROJECT_WORKING --request-dir UPDATED_REQUEST --plan reports/employee-change/plan.json --apply --output-dir reports/employee-change/project --require-live
```

不需要 API Key，也不会调用模型。`--require-live` 验证原始项目确实由真实模型生成，不意味着这次更新调用了模型。应用前再次计算计划；预览后材料变化会要求重新预览。

影响清单指出改变的文档、消费知识的模型节点及需要重验候选的检查节点。`employee.py`、README 和自定义文件从工作副本原样保留，不自动生成新的业务函数。知识冲突、流程清单变化或需求字段变化时返回 `blocked`，退出码 2，不覆盖任何输入。

计划必须放在输入目录之外。没有知识内容变化时返回 `no_change`，不创建新版本；即使仅有人工作品变化，也不属于当前更新功能。`project.json`、生成证据等元数据由工具管理，手改会阻止更新。保留修改只保证复制一致，不表示程序语义已验证；请重新验收。

输出包括 `revision.json`、`update.md`、新需求/知识快照、更新的项目记录，以及 `.chassis/parent/` 下的基线。来源链可离线核对；这是可复核记录，不是防篡改认证。复制归档时需保留隐藏的 `.chassis/` 目录。首版最多核对 10 层修订，且忽略 `.git` 与 `__pycache__`。

## 更新之后还必须做什么

使用原运行命令，改为指向新项目；不仅重跑原有任务，还要加入能区分新旧规范的任务。

```bash
python tools/run_generated_employee.py --project reports/employee-change/project --repo SOURCE_DIR --unit app/main.cpp --output-dir reports/employee-change/task
python tools/verify_employee_project.py --project reports/employee-change/project --report reports/employee-change/task --require-live
```

新版本记录仍为 `runtime_verified: false`，表示版本创建本身没有运行任务；独立的运行报告给出具体任务是否验收通过。原始生成调用是继承的，不能重复计算成更新调用。

更新后的员工仍由同一共享运行器执行，适用 T1 的配置检查、T2/T5 的失败诊断及 T4 的自动 ReAct 遥测。
并行参数和具体报告字段见[生成/运行说明](GENERATED_EMPLOYEE_PROJECT.md)；升级底盘不会把旧归档自动补成新格式。
T4 [严格来源与回执检查](PRODUCTION_EVIDENCE.md)仍需显式接线，知识更新命令不会替业务新增这些回执或改写团队的 README。

上述通用验收检查编译、修改范围和证据一致性，**不会自动判断任意新规范是否满足**。例如“必须选 v2 接口”的规则在下述演示外部检查器中另行实现；企业规范需要对应的业务验收代码。

## 比赛可演示的实际链路

1. 用真实模型生成一个 include 修复员工。
2. CI 模拟团队添加启动日志和一份值班说明。
3. 把账单接口规范从 v1 改成 v2，预览并应用局部更新，代码和人工定制不重写。
4. 旧、新员工分别处理**同一个源码输入**：两个头文件都能编译，但应该按各自规范选用不同版本。
5. 新员工再处理 pricing、scheduler 两个旧任务。
6. 外部检查器重新编译、核对实际头文件版本、知识消费、人工文件保留和原目录不变。

工作流：[employee-update.yml](../.github/workflows/employee-update.yml)。提交标记 `[employee-update-live]` 或手动选择 `live=true` 可触发真实链路；普通提交只跑回归。上限为生成 3 次、4 个任务各 6 次，共 27 次模型请求；更新本身为 0。使用既有 Secret，报告和完整项目保留 14 天。

外部业务验收入口是 `python tools/employee_update_demo.py verify --output-dir reports/employee-update`，要求该目录已经包含工作流生成的基线、工作副本、新版本和 4 份运行报告。`verify` 只重放检查，不再次调用模型；它不是适用于任意业务的通用规则编译器。

当前是“既有知识文档内容更新”，不是任意需求变化的自动代码合并器。该公开案例用于验证维护能力，不证明生产采纳率或节省工时。下一步需要真实构建接口和项目验收标准。
