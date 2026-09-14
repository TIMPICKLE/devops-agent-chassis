# 领导版改为 CodeAgent 实际项目全流程演示

日期：2026-09-14。

## 为什么修改

原 `docs/LEADERS_USE_CASE.md` 主要展示 C++ include 生成器的小型实验。领导与评委需要看到实际业务项目怎样从用户需求变成可运行的数字员工，以及员工怎样进入已有开发流程并交付成果。

本次以 `AzureCodeAgent_Base_Agent_chassis` 为例，依据其贡献分析、装配设计、最终装配报告和 `a92270a231496bd9ec228d1981025f2933fcafd9` 源码重写。

## 改动

- 用两幕八步展示“岗位需求 → 流程确认 → 规范与接口 → CodeAgent 项目 → 部署 → 工作项指令 → 编码与验证 → PR 或本地候选”。每步包含用户交互和实际产物。
- 增加两张流程图、实际文件与状态位置、现场演示顺序，以及同一员工复用和工厂化扩展说明。
- 区分装配时生成的固定流程与任务运行时生成的具体计划，说明业务能力迁移和底盘组件复用的关系。
- 固定 CodeAgent 源码链接版本；对话标为演示还原，健康检查需求标为演示输入，不虚构企业工单、PR 或实测记录。
- 明确本地候选与远端 PR 的完成范围、按文件类型执行的检查、可选项目测试及 LLM 审查的性质；保留当前证据导出与采集的实际边界。
- 同步 README 入口和变更索引。C++ include 生成、更新及技术实验仍保留在专项文档，不把它们的历史 Actions 当作 CodeAgent 企业 PR 交付证据。

## 验证与证据

- 对照 CodeAgent 的装配脚本、载荷、服务适配、CLI、验证器、评论回复、Webhook 任务运行入口及队列接口/配置核对行为。
- 检查本次新增文档的相对链接、固定版本的跨仓库文件路径、引用式链接、代码围栏与表格结构；执行 `git diff --check`。
- 本次仅修改文档，不新增或改动运行代码，不启动企业服务、不调用付费模型、不发送 Azure DevOps 评论或创建业务 PR。
- [本分支 GitHub Actions](https://github.com/TIMPICKLE/devops-agent-chassis/actions?query=branch%3Adocs%2Fcodeagent-leaders-journey)：PR 创建后由现有工作流自动触发，实际状态以 Actions 页面为准。这里的 CI 是文档变更所在版本的仓库回归，不是一次 CodeAgent 企业任务的运行证据。

## 来源

- [新版领导演示](../docs/LEADERS_USE_CASE.md)
- [CodeAgent 贡献分析](https://github.com/TIMPICKLE/AzureCodeAgent_Base_Agent_chassis/blob/a92270a231496bd9ec228d1981025f2933fcafd9/docs/%E5%B7%A5%E7%A8%8B%E5%BA%95%E7%9B%98%E5%AF%B9CodeAgent%E7%9A%84%E8%B4%A1%E7%8C%AE%E5%88%86%E6%9E%90.md)
- [CodeAgent 装配设计](https://github.com/TIMPICKLE/AzureCodeAgent_Base_Agent_chassis/blob/a92270a231496bd9ec228d1981025f2933fcafd9/docs/assembly-design.md)
- [CodeAgent 历史装配报告](https://github.com/TIMPICKLE/AzureCodeAgent_Base_Agent_chassis/blob/a92270a231496bd9ec228d1981025f2933fcafd9/docs/FINAL_ASSEMBLY_REPORT.md)
