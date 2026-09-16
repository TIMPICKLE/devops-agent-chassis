# 岗位配方库 · 2026-09-16

## 用户问题与路线决定

通用底盘与已装配业务员工已有积累；继续推广时，项目配置、依赖和启动方法仍需逐次整理。用户在全局评审后选择 B1 岗位配方库，本轮将已有岗位整理为版本化、可实例化的配方，保持核心五系统不变。

## 实现

- `employee_factory/recipes.py` 提供 `Recipe` / `RecipeRegistry`，声明任务源、编排、工具、验收、知识、失败及可观测组件，支持固定版本注册与配置校验。
- `role_recipes/` 收录 `azure-codeagent@1.0.0`、`sonarqube-autoflow@1.0.0`。绑定源码 SHA，使用本地 Git checkout 中的该版本导出源码，不带入工作副本改动、现有凭据和运行状态。
- `tools/role_recipe.py` 提供 `list/show/plan/create/verify`。每次创建一个实例，普通配置与环境变量引用分开；缺失配置和未知字段明确报错，不覆盖既有目录。
- 实例内含原岗位实现、配置、配方、文件摘要及 `launch.py`。`check` 不连接业务服务；`check --environment` 检查运行要求；`run` 转交原应用 CLI 并保留退出码。Azure 队列/工作区及 Sonar 工作区使用实例路径。
- 原岗位知识随固定版本导出。Sonar 配方限制为其源实现支持的 master 分支，检查 Claude Code/MCP 依赖；外部 MCP 配置仅在显式运行时映射。
- 配方创建是确定性实例化，`model_calls=0`、`runtime_verified=false`；不伪造 AI 生成证据。原有 AI 生成 C++ 参考入口及知识更新接口保持原合同。

## 验证与证据

| 范围 | 结果 |
|---|---|
| 本地完整回归 | 418 passed、1 skipped；跳过项需要本地未安装的 MCP SDK |
| 配方专项 | 16 passed：版本/字段校验、固定源码、秘密不落盘、实例隔离、独立进程启动、退出码、改动检测、失败不覆盖 |
| Azure 实际源实现复用 | 固定 `a92270a231496bd9ec228d1981025f2933fcafd9`，HSI/Portals 两个实例各通过原应用 9 项 `test_chassis_payload.py` 离线测试，使用当前底盘与业务替身 |
| Sonar 实际源实现复用 | 固定 `02f83a17527f2db4256a5908f5d816b9715487c3`，源码/知识/配置映射检查通过，未执行业务修复 |
| 真实企业任务 | 本轮未连接 Azure、Sonar、模型服务或执行 Claude Code；不新增 live 成功声明 |

可复现验证：安装 `.[dev,llm]` 及离线原应用配置所需 `rich python-dotenv`，运行 `python -m pytest tests/ -q`；实际来源复用用 `tools/verify_role_recipe_examples.py`（参数见[使用说明](../docs/ROLE_RECIPES.md)）。

新增 [Role Recipes 工作流](../.github/workflows/role-recipes.yml)：Python 3.9/3.13 配方合同，以及固定源实现的三实例离线验收。产物含源码实例、配置与测试日志，保留 14 天。具体 Actions 链接在提交后补录。

## 限制与下一步

首版复用原应用实现及其依赖/业务验收，Sonar 保留其 MAF 运行时；没有把业务框架重写进核心。创建实例不代表企业运行已验收，摘要不是签名认证或沙箱。

新增岗位通过配方目录或 Python 注册接口接入，需要明确源码、可配置字段及验收方法。自由装配仍由原装配流程完成；本轮不包含任意需求自动生成新配方、批量调度、安装包发布或通用代码合并。N06a 已交付，N06 企业接入/收益测量及 N07 公平对照继续保留。
