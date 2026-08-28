"""
装配向导 —— 在终端里通过问答完成一台数字员工的装配。

    python assemble.py        # 从仓库根目录启动

向导做三件事：
  1. 把五大系统的可选项逐一问清（编排、知识注入、失败契约、权限、可观测）
  2. 让你选一个现成载荷，或生成一份带 TODO 标记的新载荷骨架
  3. 产出一份可直接运行的装配脚本

与底盘一样零第三方依赖，纯标准库实现。
"""
from __future__ import annotations

import os
import re
import sys
from dataclasses import dataclass, field
from datetime import datetime
from typing import Any, Dict, List, Optional, Sequence, Tuple

from .permissions import STANDARD_CAPABILITIES


# ═══════════════════════════════════════════════════════════
#  终端交互原语
# ═══════════════════════════════════════════════════════════

def _supports_color() -> bool:
    if os.environ.get("NO_COLOR"):
        return False
    if os.name == "nt":
        os.system("")  # 在旧式 cmd 里启用 VT 转义序列
    return hasattr(sys.stdout, "isatty") and sys.stdout.isatty()


_COLOR = _supports_color()


def _c(text: str, code: str) -> str:
    return f"\033[{code}m{text}\033[0m" if _COLOR else text


def bold(t: str) -> str: return _c(t, "1")
def dim(t: str) -> str: return _c(t, "2")
def cyan(t: str) -> str: return _c(t, "36")
def green(t: str) -> str: return _c(t, "32")
def yellow(t: str) -> str: return _c(t, "33")


class WizardAborted(Exception):
    pass


def ask(prompt: str, default: str = "") -> str:
    hint = dim(f"（默认 {default}）") if default else ""
    try:
        val = input(f"  {prompt}{hint}: ").strip().lstrip("\ufeff")
    except (EOFError, KeyboardInterrupt):
        raise WizardAborted()
    return val or default


def ask_int(prompt: str, default: int) -> int:
    while True:
        raw = ask(prompt, str(default))
        try:
            return int(raw)
        except ValueError:
            print(yellow("  请输入整数。"))


def ask_yes(prompt: str, default: bool = True) -> bool:
    hint = "Y/n" if default else "y/N"
    while True:
        raw = ask(f"{prompt} [{hint}]")
        if not raw:
            return default
        if raw.lower() in ("y", "yes", "是"):
            return True
        if raw.lower() in ("n", "no", "否"):
            return False
        print(yellow("  请输入 y 或 n。"))


Option = Tuple[str, str, str]  # (key, 标签, 说明)


def choose(title: str, options: Sequence[Option], default_key: str) -> str:
    print(f"\n  {bold(title)}")
    default_no = 1
    for idx, (key, label, desc) in enumerate(options, 1):
        if key == default_key:
            default_no = idx
        print(f"    {cyan(str(idx))}. {label}" + (f"  {dim('—— ' + desc)}" if desc else ""))
    while True:
        raw = ask("选择编号", str(default_no))
        if raw.isdigit() and 1 <= int(raw) <= len(options):
            return options[int(raw) - 1][0]
        print(yellow(f"  请输入 1-{len(options)} 之间的编号。"))


def choose_multi(title: str, options: Sequence[Option], default_keys: Sequence[str]) -> List[str]:
    print(f"\n  {bold(title)}")
    defaults = [str(i) for i, (key, _, _) in enumerate(options, 1) if key in default_keys]
    for idx, (key, label, desc) in enumerate(options, 1):
        print(f"    {cyan(str(idx))}. {label}" + (f"  {dim('—— ' + desc)}" if desc else ""))
    while True:
        raw = ask("选择编号（逗号分隔可多选）", ",".join(defaults))
        picks = [p.strip() for p in raw.replace("，", ",").split(",") if p.strip()]
        if picks and all(p.isdigit() and 1 <= int(p) <= len(options) for p in picks):
            seen: List[str] = []
            for p in picks:
                key = options[int(p) - 1][0]
                if key not in seen:
                    seen.append(key)
            return seen
        print(yellow(f"  请输入 1-{len(options)} 之间的编号，逗号分隔。"))


def section(no: str, title: str) -> None:
    print(f"\n{bold(cyan(f'── {no} {title} ' + '─' * max(4, 46 - len(title) * 2)))}")


# ═══════════════════════════════════════════════════════════
#  可选项目录
# ═══════════════════════════════════════════════════════════

PAYLOADS: Dict[str, Dict[str, Any]] = {
    "code_quality": {
        "label": "代码质量治理（现成载荷）",
        "desc": "任务来自静态扫描器异味，做完 = 工作区有实际变更",
        "needs": ("decider", "planner"),
        "critic": True,
        "executor_tools": ["apply_fix"],
        "default_name": "代码质量治理数字员工",
    },
    "pr_mention": {
        "label": "PR 评论区 @Agent（现成载荷）",
        "desc": "任务来自评论区 @Agent 的指令，做完 = 回帖已发出",
        "needs": ("decider",),
        "critic": False,
        "executor_tools": ["draft_change"],
        "default_name": "PR 评论助手",
    },
    "new": {
        "label": "生成新载荷骨架",
        "desc": "回答几个问题，生成带 TODO 标记的 TaskSource / DoneCriteria / 工具集",
        "needs": ("decider", "planner"),
        "critic": True,
        "executor_tools": ["apply_change"],
        "default_name": "我的数字员工",
    },
}

OUTER_OPTIONS: List[Option] = [
    ("nested", "Nested（推荐）", "确定性外层骨架 + 单一下放点，换推理模式不动骨架"),
    ("state_machine", "StateMachine", "线性状态机，下放点是其中一个 AgentStep"),
    ("single_agent", "SingleAgent", "无外层骨架，整个任务交给推理模式（最激进）"),
]

PATTERN_OPTIONS: List[Option] = [
    ("react", "ReAct", "边想边做，每步观察后再决策；轮次不可预测（需 decider）"),
    ("plan_execute", "Plan-and-Execute", "先出完整计划再执行，失败可重规划（需 planner）"),
    ("plan_and_solve", "Plan-and-Solve", "一次调用出计划即答案，执行期不再问（需 planner）"),
    ("rewoo", "ReWOO", "计划带证据变量，固定两次模型调用，token 最省（需 planner）"),
]

PATTERN_NEEDS = {
    "react": "decider",
    "plan_execute": "planner",
    "plan_and_solve": "planner",
    "rewoo": "planner",
}

INJECTION_OPTIONS: List[Option] = [
    ("TASK_ADMITTED", "TASK_ADMITTED", "任务准入之后，补充任务级背景"),
    ("BEFORE_TOOL", "BEFORE_TOOL", "每次工具调用之前，约束单次调用"),
    ("BEFORE_EXECUTOR", "BEFORE_EXECUTOR", "调外部执行器前的最后一步（生产推荐）"),
    ("BEFORE_VERDICT", "BEFORE_VERDICT", "裁定之前，补充判定口径"),
    ("AGENT_BOOT", "AGENT_BOOT", "决策层 system prompt。底盘刻意留空——慎选"),
]


# ═══════════════════════════════════════════════════════════
#  向导配置
# ═══════════════════════════════════════════════════════════

@dataclass
class WizardConfig:
    root: str = "."
    name: str = ""
    slug: str = ""
    payload: str = "code_quality"
    # 新载荷骨架
    new_module: str = ""
    new_display: str = ""
    new_source_desc: str = ""
    new_criteria_desc: str = ""
    # ① 编排
    outer: str = "nested"
    pattern: str = "react"
    max_iterations: int = 8
    reflexion: bool = False
    reflexion_attempts: int = 2
    # ③ 知识注入
    use_skills: bool = True
    skill_points: List[str] = field(default_factory=lambda: ["BEFORE_EXECUTOR"])
    static_text: str = ""
    static_points: List[str] = field(default_factory=lambda: ["TASK_ADMITTED"])
    use_retry_feedback: bool = True
    # ④ 失败契约
    failure: str = "zero"          # zero / retry
    max_retries: int = 1
    ledger_path: str = ""          # 空 = 仅内存
    # 权限边界
    granted: List[str] = field(default_factory=lambda: ["repo.read", "repo.write"])
    # ⑤ 可观测
    console_observer: bool = True
    recording: bool = True
    recording_mode: str = "cron"
    # 运行
    rounds: int = 2
    out_path: str = ""


# ═══════════════════════════════════════════════════════════
#  问答流程
# ═══════════════════════════════════════════════════════════

def run_wizard(root: str) -> WizardConfig:
    cfg = WizardConfig(root=root)
    print(bold("\n╔══════════════════════════════════════════════════╗"))
    print(bold("║   Agent Chassis 装配向导 · 问答式组装一台数字员工   ║"))
    print(bold("╚══════════════════════════════════════════════════╝"))
    print(dim("  回车接受默认值；Ctrl+C 随时退出，不写任何文件。"))

    # ── 载荷 ────────────────────────────────────────────
    section("载荷", "任务从哪来、怎么算做完")
    cfg.payload = choose(
        "选择载荷",
        [(k, v["label"], v["desc"]) for k, v in PAYLOADS.items()],
        "code_quality",
    )
    meta = PAYLOADS[cfg.payload]

    if cfg.payload == "new":
        while True:
            mod = ask("新载荷模块名（snake_case，将写入 payloads/<模块名>.py）", "my_payload")
            if re.fullmatch(r"[a-z][a-z0-9_]*", mod):
                cfg.new_module = mod
                break
            print(yellow("  模块名需为小写字母/数字/下划线，且以字母开头。"))
        cfg.new_display = ask("载荷显示名", "我的业务场景")
        cfg.new_source_desc = ask("任务源一句话描述（任务从哪来）", "外部系统待处理事项")
        cfg.new_criteria_desc = ask("完成判据一句话描述（读什么客观事实）", "外部系统已确认交付")

    cfg.name = ask("这台数字员工叫什么", meta["default_name"])
    default_slug = cfg.new_module or cfg.payload
    while True:
        slug = ask("输出文件名（不含 .py）", default_slug)
        if re.fullmatch(r"[A-Za-z0-9_\-]+", slug):
            cfg.slug = slug
            break
        print(yellow("  文件名仅限字母、数字、下划线、连字符。"))

    # ── ① 编排契约 ──────────────────────────────────────
    section("①", "编排契约 · 外层流程 × 内层推理")
    cfg.outer = choose("外层流程（任务被推进的骨架）", OUTER_OPTIONS, "nested")

    available = [opt for opt in PATTERN_OPTIONS if PATTERN_NEEDS[opt[0]] in meta["needs"]]
    if len(available) < len(PATTERN_OPTIONS):
        print(dim("  （该载荷只提供 decider，未提供 planner，规划类模式不可选）"))
    cfg.pattern = choose("内层推理模式（下放点内部，模型怎么想）", available, available[0][0])
    if cfg.pattern == "react":
        cfg.max_iterations = ask_int("ReAct 最大迭代轮数", 8)

    if meta["critic"]:
        cfg.reflexion = ask_yes("外面再包一层 Reflexion（外部评估器判定 + 情景记忆重试）？", False)
        if cfg.reflexion:
            cfg.reflexion_attempts = ask_int("Reflexion 最大尝试次数", 2)

    # ── ③ 知识注入 ──────────────────────────────────────
    section("③", "知识注入 · 时机比内容更重要")
    cfg.use_skills = ask_yes("启用 Skill 规范库（按文件路径两级路由注入领域规范）？", True)
    if cfg.use_skills:
        cfg.skill_points = choose_multi("Skill 注入时机", INJECTION_OPTIONS, ["BEFORE_EXECUTOR"])
        if "AGENT_BOOT" in cfg.skill_points:
            print(yellow("  提醒：注入 AGENT_BOOT 会让决策层绑定技术栈，底盘默认刻意留空。"))
    cfg.static_text = ask("固定背景知识文本（StaticKnowledge，留空跳过）", "")
    if cfg.static_text:
        cfg.static_points = choose_multi("固定知识注入时机", INJECTION_OPTIONS, ["TASK_ADMITTED"])
    cfg.use_retry_feedback = ask_yes("启用 RetryFeedback（重试前回灌上次失败原因）？", True)

    # ── ④ 失败契约 ──────────────────────────────────────
    section("④", "失败契约 · 失败之后系统留下什么")
    cfg.failure = choose(
        "失败策略",
        [
            ("zero", "ZeroSideEffect", "干净退出、落库去重、不重试（默认）"),
            ("retry", "RetryThenGiveUp", "有限重试，重试前触发 ON_RETRY 回灌"),
        ],
        "zero",
    )
    if cfg.failure == "retry":
        cfg.max_retries = ask_int("最大重试次数", 1)
    if ask_yes("去重账本 Ledger 落盘（跨进程防重复处理）？", False):
        cfg.ledger_path = ask("账本文件相对路径", f"state/{cfg.slug}.ledger.json")

    # ── 权限边界 ────────────────────────────────────────
    section("⑤", "权限边界 · 能力借来，权限不借")
    cap_options: List[Option] = [
        (key, key, cap.description + ("（不可逆）" if cap.irreversible else ""))
        for key, cap in sorted(STANDARD_CAPABILITIES.items())
    ]
    cfg.granted = choose_multi("授予执行器哪些能力（未选中的调用会抛 PermissionDenied）",
                               cap_options, ["repo.read", "repo.write"])
    irreversible = [k for k in cfg.granted if STANDARD_CAPABILITIES[k].irreversible]
    if irreversible:
        print(yellow(f"  注意：已授予不可逆能力 {irreversible}，生产上通常由确定性代码而非执行器持有。"))

    # ── ⑤ 可观测 ────────────────────────────────────────
    section("⑥", "可观测 · 把执行过程变成可追问的数据")
    cfg.console_observer = ask_yes("启用 ConsoleObserver（终端实时打印执行轨迹）？", True)
    cfg.recording = ask_yes("启用 RecordingObserver（累积健康度统计）？", True)
    if cfg.recording:
        cfg.recording_mode = choose(
            "触发模式标注（仅记录用途）",
            [("cron", "cron", "定时轮询触发"), ("webhook", "webhook", "事件回调触发")],
            "cron",
        )

    # ── 运行参数 ────────────────────────────────────────
    section("⑦", "运行")
    cfg.rounds = ask_int("生成脚本默认跑几轮任务", 2)
    cfg.out_path = os.path.join("generated", f"{cfg.slug}.py")
    return cfg


# ═══════════════════════════════════════════════════════════
#  代码生成 · 装配脚本
# ═══════════════════════════════════════════════════════════

def _points_expr(points: Sequence[str]) -> str:
    return "[" + ", ".join(f"InjectionPoint.{p}" for p in points) + "]"


def gen_assembly(cfg: WizardConfig) -> str:
    meta = PAYLOADS[cfg.payload]
    module = cfg.new_module if cfg.payload == "new" else cfg.payload
    out_dir = os.path.dirname(cfg.out_path) or "."
    rel_to_root = os.path.relpath(".", out_dir).replace("\\", "/")
    single = cfg.outer == "single_agent"

    pattern_cls = {
        "react": "ReActPattern",
        "plan_execute": "PlanExecutePattern",
        "plan_and_solve": "PlanAndSolvePattern",
        "rewoo": "ReWOOPattern",
    }[cfg.pattern]

    orch_imports = {"nested": ["AgentStep", "FnStep", "NestedOrchestrator"],
                    "state_machine": ["AgentStep", "FnStep", "StateMachineOrchestrator"],
                    "single_agent": ["SingleAgentOrchestrator"]}[cfg.outer]
    orch_imports.append(pattern_cls)
    if cfg.reflexion:
        orch_imports.append("ReflexionPattern")

    chassis_names = ["Chassis", "InjectionPoint"]
    if cfg.console_observer:
        chassis_names.append("ConsoleObserver")
    if cfg.recording:
        chassis_names.append("RecordingObserver")

    knowledge_names: List[str] = []
    if cfg.use_skills:
        knowledge_names += ["SkillLibrary", "SkillProvider", "by_extension", "by_filename_markers"]
    if cfg.static_text:
        knowledge_names.append("StaticKnowledge")
    if cfg.use_retry_feedback:
        knowledge_names.append("RetryFeedback")

    failure_cls = "RetryThenGiveUpPolicy" if cfg.failure == "retry" else "ZeroSideEffectPolicy"

    L: List[str] = []
    w = L.append
    w('"""')
    w(f"装配脚本 —— {cfg.name}")
    w(f"由装配向导于 {datetime.now():%Y-%m-%d %H:%M} 生成。")
    w("")
    w(f"    python {cfg.out_path.replace(os.sep, '/')}")
    w("")
    w("五大系统均为底盘原件；本脚本只做「选」和「接线」。")
    w('"""')
    w("from __future__ import annotations")
    w("")
    w("import os")
    w("import sys")
    w("")
    w("_HERE = os.path.dirname(os.path.abspath(__file__))")
    w(f"_ROOT = os.path.abspath(os.path.join(_HERE, {rel_to_root!r}))")
    w('sys.path.insert(0, os.path.join(_ROOT, "src"))')
    w("sys.path.insert(0, _ROOT)")
    w("")
    w(f"from agent_chassis import {', '.join(sorted(set(chassis_names)))}")
    w(f"from agent_chassis.failure import Ledger, {failure_cls}")
    if knowledge_names:
        w(f"from agent_chassis.knowledge import {', '.join(sorted(set(knowledge_names)))}")
    w(f"from agent_chassis.orchestration import {', '.join(sorted(set(orch_imports)))}")
    w("from agent_chassis.permissions import PermissionBoundary")
    w("")
    w(f"from payloads import {module} as payload")
    w("")

    # ── 载荷设置 ──
    w("# ═══ 载荷：任务源 + 完成判据 + 领域工具集 ═══")
    granted = "{" + ", ".join(repr(g) for g in sorted(cfg.granted)) + "}"
    w(f'boundary = PermissionBoundary(subject="executor@{cfg.name}", granted={granted})')
    if cfg.payload == "code_quality":
        w("repo = payload.FakeRepo()  # TODO: 换成真实 VCS 客户端")
        w("box = payload.build_toolbox(repo, boundary, seed=11)")
        w("source = payload.ScannerTaskSource()")
        w("criteria = payload.WorkspaceChangedCriteria(repo)")
        w('EXECUTOR_TOOLS = ["apply_fix"]')
        if not single:
            w("")
            w("")
            w("def prepare(task, ctx):")
            w("    repo.reset_hard()  # 开工前假设上一轮留了残骸")
            w('    repo.checkout_new(f"fix/{task.key}")')
            w("")
            w("")
            w("def deliver(task, ctx):")
            w("    if repo.diff():")
            w('        ctx.facts["commit"] = repo.commit(f"fix: {task.key}")')
            w('        ctx.facts["pr"] = f"PR-{task.key[-4:]}"')
    elif cfg.payload == "pr_mention":
        w("thread = payload.Thread()  # TODO: 换成真实评论区客户端")
        w("box = payload.build_toolbox(thread, boundary)")
        w("source = payload.MentionTaskSource()")
        w("criteria = payload.RepliedCriteria(thread)")
        w('EXECUTOR_TOOLS = ["draft_change"]')
        if not single:
            w("")
            w("")
            w("def prepare(task, ctx):")
            w('    ctx.facts["acked"] = True')
            w("")
            w("")
            w("def deliver(task, ctx):")
            w('    ctx.facts["replies"] = len(thread.replies_for(task.key))')
    else:
        camel = "".join(p.title() for p in cfg.new_module.split("_"))
        w("system = payload.ExternalSystem()  # TODO: 换成真实外部系统客户端")
        w("box = payload.build_toolbox(system, boundary)")
        w(f"source = payload.{camel}TaskSource()")
        w(f"criteria = payload.{camel}DoneCriteria(system)")
        w('EXECUTOR_TOOLS = ["apply_change"]')
        if not single:
            w("")
            w("")
            w("def prepare(task, ctx):")
            w('    ctx.facts["prepared"] = True  # TODO: 开工前清理 / 建工作分支')
            w("")
            w("")
            w("def deliver(task, ctx):")
            w('    ctx.facts["delivered"] = task.key in system.delivered  # TODO: 交付收尾')
    w("")

    # ── 推理模式 ──
    w("# ═══ ① 编排契约：内层推理模式 ═══")
    decider_expr = {"code_quality": "payload.make_decider()",
                    "pr_mention": "payload.decide",
                    "new": "payload.make_decider()"}[cfg.payload]
    if cfg.pattern == "react":
        w(f"pattern = ReActPattern({decider_expr}, max_iterations={cfg.max_iterations},")
        w("                       executor_tools=EXECUTOR_TOOLS)")
    else:
        w(f"pattern = {pattern_cls}(payload.make_planner(), executor_tools=EXECUTOR_TOOLS)")
    if cfg.reflexion:
        critic_arg = "repo" if cfg.payload == "code_quality" else "system"
        w(f"pattern = ReflexionPattern(inner=pattern, critic=payload.make_critic({critic_arg}),")
        w(f"                           max_attempts={cfg.reflexion_attempts})")
    w("")

    # ── 编排器 ──
    w("# ═══ ① 编排契约：外层流程 ═══")
    if cfg.outer == "nested":
        w("steps = [")
        w('    FnStep("workspace_setup", prepare),')
        w('    AgentStep("agent_work", lambda t, c: None),  # 唯一的决策下放点')
        w('    FnStep("delivery", deliver),')
        w("]")
        w('orchestrator = NestedOrchestrator(steps, box, pattern, "agent_work", criteria)')
    elif cfg.outer == "state_machine":
        w("steps = [")
        w('    FnStep("workspace_setup", prepare),')
        w('    AgentStep("agent_work", pattern=pattern, toolbox=box),  # 唯一的决策下放点')
        w('    FnStep("delivery", deliver),')
        w("]")
        w("orchestrator = StateMachineOrchestrator(steps, criteria)")
    else:
        w("orchestrator = SingleAgentOrchestrator(box, pattern, criteria)")
    w("")

    # ── 知识注入 ──
    w("# ═══ ③ 知识注入：时机 → provider ═══")
    w("providers = []")
    if cfg.use_skills:
        w("skills = SkillLibrary(")
        w('    root="",  # TODO: 指向真实规范目录（<skill>.md），inline 仅为演示')
        w("    rules=[")
        w('        by_extension({".cs": "abp-net-backend"}),')
        w('        by_filename_markers([".ts", ".html"], ["component", "service", "module"],')
        w('                            hit="angular-frontend", miss="typescript-common"),')
        w("    ],")
        w("    inline={")
        w('        "abp-net-backend": "ABP 应用服务需继承 ApplicationService；异步方法优先。",')
        w('        "angular-frontend": "组件用 OnPush；订阅在 ngOnDestroy 释放。",')
        w('        "typescript-common": "禁止 any；公共函数必须有显式返回类型。",')
        w("    },")
        w(")")
        w(f"providers.append(SkillProvider(skills, points={_points_expr(cfg.skill_points)}))")
    if cfg.static_text:
        w(f"providers.append(StaticKnowledge({cfg.static_text!r},")
        w(f"                 points={_points_expr(cfg.static_points)}))")
    if cfg.use_retry_feedback:
        w("providers.append(RetryFeedback())")
    w("")

    # ── 失败契约 ──
    w("# ═══ ④ 失败契约 ═══")
    if cfg.ledger_path:
        ledger_rel = cfg.ledger_path.replace("\\", "/")
        w(f'ledger = Ledger(path=os.path.join(_ROOT, {ledger_rel!r}))')
    else:
        w("ledger = Ledger()  # 仅内存；传 path= 可落盘去重")
    if cfg.failure == "retry":
        w(f"policy = RetryThenGiveUpPolicy(ledger, max_retries={cfg.max_retries})")
    else:
        w("policy = ZeroSideEffectPolicy(ledger)")
    w("")

    # ── 可观测 ──
    w("# ═══ ⑤ 可观测 ═══")
    w("observers = []")
    if cfg.console_observer:
        w("observers.append(ConsoleObserver())")
    if cfg.recording:
        w(f'recorder = RecordingObserver(subject={cfg.name!r}, mode={cfg.recording_mode!r})')
        w("observers.append(recorder)")
    w("")

    # ── 装配 ──
    w("# ═══ 装配 ═══")
    w("chassis = (")
    w(f"    Chassis({cfg.name!r})")
    w("    .with_orchestrator(orchestrator)")
    w("    .with_knowledge(*providers)")
    w("    .with_failure_policy(policy)")
    w("    .with_boundary(boundary)")
    w("    .observe(*observers)")
    w("    .with_payload(source, criteria)")
    w("    .build()")
    w(")")
    w("")
    w('if __name__ == "__main__":')
    w("    print(chassis.report().render())")
    w(f"    for _ in range({cfg.rounds}):")
    w("        if chassis.run_once() is None:")
    w('            print("任务源已无待处理任务")')
    w("            break")
    if cfg.recording:
        w("    h = recorder.health()")
        w('    print(f"\\n健康度：runs={h.total_runs} 成功={h.succeeded} '
          '失败={h.failed} 成功率={h.success_rate}%")')
    w("")
    return "\n".join(L)


# ═══════════════════════════════════════════════════════════
#  代码生成 · 新载荷骨架
# ═══════════════════════════════════════════════════════════

def gen_payload_skeleton(cfg: WizardConfig) -> str:
    camel = "".join(p.title() for p in cfg.new_module.split("_"))
    head = f'''"""
载荷骨架 —— {cfg.new_display}
由装配向导于 {datetime.now():%Y-%m-%d %H:%M} 生成。

搜索 "TODO" 填入真实业务逻辑。骨架自带演示任务与外部系统替身，
生成后即可离线运行；接入真实系统时逐段替换。

要变成真数字员工，最关键的一步：把 make_decider / make_planner
里的规则模拟换成真实 LLM 的 function calling。
"""
from __future__ import annotations

from typing import Any, Dict, List, Optional

from agent_chassis import DoneCriteria, RunContext, Task, TaskSource, Verdict
from agent_chassis.orchestration import ToolBox
from agent_chassis.permissions import PermissionBoundary


class ExternalSystem:
    """外部系统替身。TODO: 换成真实客户端，或改走 Chassis.mount() 的连接器。"""

    def __init__(self) -> None:
        self.delivered: List[str] = []

    def deliver(self, key: str) -> None:
        if key not in self.delivered:
            self.delivered.append(key)


# TODO: 换成真实任务来源（扫描器告警 / PR 评论 / 构建失败 / 工单 …）
DEMO_TASKS: List[Dict[str, Any]] = [
    {{"key": "demo-0001", "summary": "演示任务一", "path": "src/example/a.cs"}},
    {{"key": "demo-0002", "summary": "演示任务二", "path": "src/example/b.component.ts"}},
]
'''
    body = f'''

class {camel}TaskSource(TaskSource):
    """{cfg.new_source_desc}"""

    name = "{cfg.new_source_desc}"

    def __init__(self, items: Optional[List[Dict[str, Any]]] = None) -> None:
        self.items = list(items if items is not None else DEMO_TASKS)
        self._cursor = 0

    def fetch(self, limit: int = 1) -> List[Task]:
        # TODO: 改为调用真实外部系统；底盘负责去重，这里不必记账
        out: List[Task] = []
        while self._cursor < len(self.items) and len(out) < limit:
            item = self.items[self._cursor]
            self._cursor += 1
            out.append(Task(
                key=item["key"],
                kind="{cfg.new_module}",
                source=self.name,
                # target.path 供 SkillProvider 做两级路由，建议保留
                payload={{**item, "target": {{"path": item.get("path", "")}}}},
            ))
        return out


class {camel}DoneCriteria(DoneCriteria):
    """{cfg.new_criteria_desc}

    硬约束：只读外部客观事实，不读 ctx.model_notes——
    模型说"我做完了"不算数。
    """

    name = "{cfg.new_criteria_desc}"

    def __init__(self, system: ExternalSystem) -> None:
        self.system = system

    def judge(self, task: Task, ctx: RunContext) -> Verdict:
        # TODO: 换成读真实外部状态（VCS diff / 工单状态 / 构建结果 …）
        if task.key in self.system.delivered:
            return Verdict(True, "外部系统已确认交付", {{"delivered": task.key}})
        return Verdict(False, "未检测到交付事实", {{"delivered": None}})


def build_toolbox(system: ExternalSystem, boundary: PermissionBoundary) -> ToolBox:
    """Agent 在决策点能调的领域工具。TODO: 逐个换成真实实现。"""
    box = ToolBox()

    def inspect(path: str = "") -> Dict[str, Any]:
        """取证：读取任务相关上下文。"""
        boundary.check("repo.read")
        return {{"path": path, "summary": "（演示数据）"}}

    def apply_change(path: str = "", note: str = "") -> Dict[str, Any]:
        """调用外部执行器产生变更。执行器只有被授予的能力，越界会抛异常。"""
        boundary.check("repo.write")
        return {{"ok": True, "path": path}}

    def deliver(key: str = "") -> Dict[str, Any]:
        """交付动作：把结果写回外部系统，完成判据读的就是它。"""
        system.deliver(key)
        return {{"delivered": key}}

    for fn in (inspect, apply_change, deliver):
        box.add(fn.__name__, fn)
    return box


def make_decider():
    """ReAct 决策函数。当前是规则模拟。

    TODO（接真模型）：把 box.schema() 与 ctx.facts 喂给 LLM，
    让它返回 ("call", 工具名, 参数) 或 ("stop", 理由, None)。
    """

    def decide(task: Task, ctx: RunContext, box: ToolBox) -> tuple:
        seen = ctx.facts.setdefault("tool_results", {{}})
        p = task.payload
        if "inspect" not in seen:
            return ("call", "inspect", {{"path": p.get("path", "")}})
        if "apply_change" not in seen:
            return ("call", "apply_change",
                    {{"path": p.get("path", ""), "note": p.get("summary", "")}})
        if "deliver" not in seen:
            return ("call", "deliver", {{"key": task.key}})
        ctx.note("已交付，收敛")
        return ("stop", "已交付", None)

    return decide


def make_planner():
    """线性规划函数（Plan-and-Execute / Plan-and-Solve / ReWOO 共用）。

    TODO（接真模型）：改为一次 LLM 调用产出完整计划。
    """

    def planner(task: Task, ctx: RunContext, box: ToolBox):
        p = task.payload
        return [
            ("inspect", {{"path": p.get("path", "")}}),
            ("apply_change", {{"path": p.get("path", ""), "note": p.get("summary", "")}}),
            ("deliver", {{"key": task.key}}),
        ]

    return planner


def make_critic(system: ExternalSystem):
    """Reflexion 外部评估器：读客观事实，不读模型自述。"""

    def critic(task: Task, ctx: RunContext) -> Optional[str]:
        if task.key in system.delivered:
            return None
        return "外部系统未确认交付，本轮判定未生效"

    return critic
'''
    return head + body


# ═══════════════════════════════════════════════════════════
#  写盘与总结
# ═══════════════════════════════════════════════════════════

def _summary(cfg: WizardConfig) -> str:
    meta = PAYLOADS[cfg.payload]
    pattern_label = dict((k, l) for k, l, _ in PATTERN_OPTIONS)[cfg.pattern]
    if cfg.reflexion:
        pattern_label = f"Reflexion({pattern_label}) × {cfg.reflexion_attempts} 次"
    knowledge = []
    if cfg.use_skills:
        knowledge.append(f"skills@{'/'.join(cfg.skill_points)}")
    if cfg.static_text:
        knowledge.append(f"static@{'/'.join(cfg.static_points)}")
    if cfg.use_retry_feedback:
        knowledge.append("retry_feedback@ON_RETRY")
    rows = [
        ("名称", cfg.name),
        ("载荷", meta["label"] + (f" → payloads/{cfg.new_module}.py" if cfg.payload == "new" else "")),
        ("① 编排", f"{cfg.outer} + {pattern_label}"),
        ("③ 知识", "、".join(knowledge) or "（无）"),
        ("④ 失败", ("retry×%d" % cfg.max_retries if cfg.failure == "retry" else "zero-side-effect")
                  + ("，账本落盘 " + cfg.ledger_path if cfg.ledger_path else "")),
        ("权限", "、".join(cfg.granted)),
        ("⑤ 可观测", "、".join(filter(None, [
            "console" if cfg.console_observer else "",
            f"recording({cfg.recording_mode})" if cfg.recording else ""])) or "（无）"),
        ("输出", cfg.out_path),
    ]
    width = max(len(k) for k, _ in rows)
    return "\n".join(f"    {k.ljust(width)}  {v}" for k, v in rows)


def write_outputs(cfg: WizardConfig) -> List[str]:
    written: List[str] = []
    if cfg.payload == "new":
        payload_path = os.path.join(cfg.root, "payloads", f"{cfg.new_module}.py")
        if os.path.exists(payload_path):
            if not ask_yes(f"payloads/{cfg.new_module}.py 已存在，覆盖？", False):
                raise WizardAborted()
        with open(payload_path, "w", encoding="utf-8") as fh:
            fh.write(gen_payload_skeleton(cfg))
        written.append(payload_path)

    out_abs = os.path.join(cfg.root, cfg.out_path)
    os.makedirs(os.path.dirname(out_abs) or ".", exist_ok=True)
    if os.path.exists(out_abs):
        if not ask_yes(f"{cfg.out_path} 已存在，覆盖？", False):
            raise WizardAborted()
    with open(out_abs, "w", encoding="utf-8") as fh:
        fh.write(gen_assembly(cfg))
    written.append(out_abs)
    return written


def main(root: Optional[str] = None) -> int:
    root = root or os.getcwd()
    try:
        cfg = run_wizard(root)
        print(f"\n{bold('── 装配摘要 ' + '─' * 40)}")
        print(_summary(cfg))
        if not ask_yes("\n确认生成？", True):
            raise WizardAborted()
        written = write_outputs(cfg)
    except WizardAborted:
        print(dim("\n已退出，未写任何文件。"))
        return 1

    print(green("\n✔ 生成完成："))
    for path in written:
        print(f"    {os.path.relpath(path, root)}")
    print(f"\n  运行：{bold('python ' + cfg.out_path.replace(os.sep, '/'))}")
    if cfg.payload == "new":
        print(dim(f"  下一步：打开 payloads/{cfg.new_module}.py，搜索 TODO 替换为真实业务逻辑；"))
        print(dim("  再把 make_decider / make_planner 换成真实 LLM function calling。"))
    return 0


if __name__ == "__main__":  # pragma: no cover
    sys.exit(main())
