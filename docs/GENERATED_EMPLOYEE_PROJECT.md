# 从需求到真正运行的员工项目

用户提供需求、知识和源码输入约定，模型生成一个员工项目。**随后由这个生成物启动实际模型调用，读取源码并修复编译问题；外部工具再次验收。** 当前第一条链路处理 C++ include 引用错误。

## 1. 给出需求和材料

参考输入目录：[employee_requests/build_repair](../employee_requests/build_repair)。`request.json` 包含自然语言 brief、员工名称、源码接口、完成标准和知识文件列表。接口目前支持 `local_cpp_translation_unit`，完成标准为 `compiler_and_include_only`。

缺少必要字段或知识文件时，生成命令返回 `needs_input` 与补充项，退出码 2，不调用模型、不创建半成品项目。当前补问是字段级检查；尚未提供独立聊天界面或对任意业务的自动接口推断。

## 2. 让模型生成项目

安装 `python -m pip install -e ".[llm]"`，在运行环境设置 `BIGMODEL_API_KEY`。然后在本仓库根目录运行：

```bash
python tools/assemble_employee.py --request-dir employee_requests/build_repair --output-dir reports/employee-demo/project
```

模型只收到需求、知识和 SDK 接线合同，通过 `submit_project` 交付以下文件；生成期间没有读取后续源码目录的工具：

| 产物 | 作用 |
|---|---|
| employee.py | AI 生成 `build_employee(kit)`，创建流程节点、接通底盘与共享服务 |
| assembly.json | 节点名称/职责以及每份知识的注入时机与阶段 |
| README.md | AI 生成的项目说明与运行命令 |
| knowledge/ | 冻结的项目规范 |
| request.snapshot.json、project.json | 需求、项目文件内容 ID 和生成版本 |
| generation.*.json | 装配时的真实模型调用、结果与用量 |

底盘公共 API 来自 `src/agent_chassis/`；`employee_factory/` 将其与源码工具、`adapters/` 的真实模型适配器和编译检查组合。模型生成项目接线，不重写编译器或复制模型客户端。首版限定为线性流程，允许 3–8 个节点，包含准备、一个模型修复节点和最终检查；不是通用工作流生成器。

## 3. 运行生成物

```bash
python tools/run_generated_employee.py --project reports/employee-demo/project --repo tests/employee_projects/pricing --unit src/main.cpp --output-dir reports/employee-demo/pricing
python tools/run_generated_employee.py --project reports/employee-demo/project --repo tests/employee_projects/scheduler --unit app/worker.cpp --output-dir reports/employee-demo/scheduler
```

同一个 `employee.py` 在两个新进程中启动，不重新生成。也可把 `--repo` 与 `--unit` 改成自己的本地源码目录和编译单元。

实际过程：读取源码快照 → C++ 编译器复现错误 → 执行生成的节点 → 运行期模型读取文件/参考规范/提交候选 → 编译与修改范围检查 → 保存候选和 patch。原始项目不被修改。

模型用 `read_file` 读取实际项目文件，也可用一次 `read_files(paths)` 调用比较最多 6 个文件，用 `submit_source` 提交候选并得到真实编译反馈。构建失败无需在代码里预先写死正确答案。两个 CI 项目使用不同目录、文件名和函数；第二个还有同名但声明不同的头文件。

### 并行配置与失败定位（T1–T5）

生成和运行 CLI 都支持 `--max-parallel-tools`、`--max-batch-calls`、`--max-tool-calls`，默认分别为 1、8、64；
两阶段独立启动，参数需分别传入。运行时可在上面的命令末尾追加：

```text
--max-parallel-tools 4 --max-batch-calls 8 --max-tool-calls 64
```

4 是示例值，不是硬编码上限。生成阶段只有修改共享候选的 `submit_project`，开启参数也不会让它并行；
运行阶段的快照读取工具已声明并行安全，`submit_source` 等写候选工具仍单独调用。
`read_files(paths)` 本身是一次工具调用，不等于多线程批次。模型是否提出独立批次及实际峰值以 `execution.batches` 为准。

两阶段通过 T1 的 `react_pattern()` 对齐模型侧和执行器限制。已有失败报告会按发生位置携带
T2 的 `diagnostics` / `model_calls[].diagnostic` 或 T5 的 `model_calls[].http_error`；无可用调用记录的早期失败不虚构这些字段。
含义见[并行与工具诊断](PARALLEL_TOOLS.md)、[HTTP 诊断](HTTP_DIAGNOSTICS.md)。HTTP 诊断不会自动改变重试策略。

## 4. 独立验收

```bash
python tools/verify_employee_project.py --project reports/employee-demo/project --report reports/employee-demo/pricing --report reports/employee-demo/scheduler --require-live --summary-dir reports/employee-demo
```

检查器在生成代码之外，重新编译原始输入与候选，确认原来失败、现在通过；检查非 include 代码未变、patch 与候选一致、实际节点匹配生成清单、知识版本与消费记录匹配。生成和运行两个阶段都必须有真实模型调用，模拟 transport 不能通过 `--require-live`。

运行命令还检查原始目录保持不变。归档后的独立核验只使用源码快照重放编译检查，不重新调用模型，也不替代对已不存在的原始目录的检查。

当前共享运行器已自动记录 ReAct 执行配置、批次统计和尝试编号，但以上 CLI 没有自动调用
`bind_manifest(..., production=True)` 或为每个业务检查生成 `record_check()` 回执，仍使用基础 v1 报告与本例编译验收。
需要 T4 严格格式时须按[专门接线说明](PRODUCTION_EVIDENCE.md)补齐元数据和真实回执；仅追加核验参数不能升级旧报告。

## 当前边界与 Actions

- 编译命令固定为 `c++ -std=c++17 -fsyntax-only -I . <unit>`；这是单编译单元语法/头文件检查，不是完整链接、行为测试或任意企业构建系统。
- 支持最多 200 个常见 C++ 源码/头文件，总计 200000 字符；源文件最多 20000 字符，不修改其他业务代码。
- 生成项目需要兼容的本仓库检出和参考支持模块，尚未独立打包成 wheel。`code_ref` 记录源码版本，工具不会自动检出或锁定 SDK。为了可复核，生成文件需与内容 ID 一致；更新既有知识内容并保留人工编辑请使用[版本化更新入口](EMPLOYEE_PROJECT_UPDATES.md)。通用流程变化仍待后续实现。
- 两个 CI 项目公开、可复现，不是企业生产数据或私有保留集；一轮成功不表示任意需求都能生成正确员工。

[employee-project.yml](../.github/workflows/employee-project.yml) 在回归通过后执行完整链路。分支最后一笔提交包含 `[employee-live]`，或工作流可手动运行时选择 `live=true`，即可触发；普通提交只跑回归。生成最多 3 次、每个源码任务最多 6 次，合计最多 15 次模型请求，使用既有 Actions Secret。

每次运行上传整个生成项目、源码快照、候选、patch、双阶段证据和验收结果，保留 14 天。实际链接与结果见[第四阶段记录](../changelog/stage-04.md)。
