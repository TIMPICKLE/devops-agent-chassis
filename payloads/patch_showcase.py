"""Two deliberately narrow, public patch tasks with different objective criteria.

Live mode never uses fixture_solution(). Python candidates are checked by AST,
not executed. The C++ case allows only a known header reference to change, then
asks the real compiler to check it. This is not a general repository sandbox.
"""
from __future__ import annotations

import ast
import difflib
import hashlib
import re
import shutil
import subprocess
import tempfile
from dataclasses import dataclass
from pathlib import Path
from typing import Optional

from agent_chassis.contracts import DoneCriteria, Task, TaskSource, Verdict
from agent_chassis.orchestration import ToolBox


@dataclass
class Candidate:
    original: str
    path: str
    content: Optional[str] = None

    def patch(self) -> str:
        if self.content is None:
            return ""
        return "".join(difflib.unified_diff(
            self.original.splitlines(keepends=True), self.content.splitlines(keepends=True),
            fromfile="a/" + self.path, tofile="b/" + self.path,
        ))

    def cleanup(self, task, ctx):
        self.content = None

    def digests(self):
        return {"candidate_sha256": hashlib.sha256((self.content or "").encode("utf-8")).hexdigest(),
                "patch_sha256": hashlib.sha256(self.patch().encode("utf-8")).hexdigest()}


class PythonQualitySource(TaskSource):
    name = "public-python-quality-fixture"

    def __init__(self):
        self.task = Task("python-none-comparison", "python_quality", {
            "target": {"path": "missing.py"},
            "source": "def is_missing(value):\n    return value == None\n",
            "goal": "Replace equality comparison to None with identity comparison. Preserve everything else.",
        }, self.name)
        self.sent = False

    def fetch(self, limit=1):
        if self.sent or limit <= 0:
            return []
        self.sent = True
        return [self.task]


class HeaderBuildSource(TaskSource):
    name = "public-header-build-fixture"

    def __init__(self):
        self.task = Task("cpp-missing-header", "header_build", {
            "target": {"path": "probe.cpp"},
            "source": '#include "ProcessMacros.h"\nint main() { return PROCESS_OK; }\n',
            "headers": {"include/ProcessMacros.h": "#define PROCESS_OK 0\n"},
            "build_error": "fatal error: ProcessMacros.h: No such file or directory",
            "goal": "Fix only the first include line using an existing relative header path. Preserve the rest.",
        }, self.name)
        self.sent = False

    def fetch(self, limit=1):
        if self.sent or limit <= 0:
            return []
        self.sent = True
        return [self.task]


class PythonNoneCriteria(DoneCriteria):
    name = "python-ast-target-and-invariants/v1"

    def __init__(self, candidate):
        self.candidate = candidate

    def validate(self, task):
        if self.candidate.content is None:
            return Verdict(False, "No candidate submitted")

        class Expected(ast.NodeTransformer):
            def visit_Compare(self, node):
                self.generic_visit(node)
                if len(node.ops) == 1 and isinstance(node.ops[0], ast.Eq) and any(
                    isinstance(x, ast.Constant) and x.value is None for x in [node.left, *node.comparators]
                ):
                    node.ops = [ast.Is()]
                return node

        expected = Expected().visit(ast.parse(self.candidate.original))
        try:
            actual = ast.parse(self.candidate.content)
        except SyntaxError:
            return Verdict(False, "Candidate has a Python syntax error")
        accepted = ast.dump(actual) == ast.dump(expected) and bool(self.candidate.patch())
        return Verdict(accepted, "AST target and unchanged-structure checks passed" if accepted
                       else "Target not fixed or unrelated code changed",
                       {"validator": self.name, "ast_match": accepted, "executes_candidate": False,
                        **self.candidate.digests()})

    def judge(self, task, ctx):
        return self.validate(task)


class HeaderBuildCriteria(DoneCriteria):
    name = "header-reference-and-compiler/v1"

    def __init__(self, candidate):
        self.candidate = candidate

    def validate(self, task):
        content = self.candidate.content
        if content is None:
            return Verdict(False, "No candidate submitted")
        lines, original = content.splitlines(), self.candidate.original.splitlines()
        if not lines or lines[1:] != original[1:]:
            return Verdict(False, "Only the include line may change")
        match = re.fullmatch(r'\s*#\s*include\s+"([^"\n]+)"\s*', lines[0])
        headers = task.payload["headers"]
        if match is None or match.group(1) not in headers:
            return Verdict(False, "Include does not resolve to an existing header")
        compiler = shutil.which("c++")
        if compiler is None:
            return Verdict(False, "C++ compiler unavailable; build verification NOT performed")
        with tempfile.TemporaryDirectory(prefix="chassis-header-check-") as directory:
            root = Path(directory)
            for name, text in headers.items():
                dest = root / name
                if Path(name).is_absolute() or ".." in Path(name).parts:
                    return Verdict(False, "Invalid fixture header path")
                dest.parent.mkdir(parents=True, exist_ok=True)
                dest.write_text(text, encoding="utf-8")
            (root / "probe.cpp").write_text(content, encoding="utf-8")
            try:
                result = subprocess.run([compiler, "-std=c++17", "-fsyntax-only", "probe.cpp"],
                                        cwd=root, capture_output=True, text=True, timeout=10,
                                        env={"PATH": "/usr/local/bin:/usr/bin:/bin"})
            except (OSError, subprocess.TimeoutExpired):
                return Verdict(False, "Compiler could not complete the bounded check")
        accepted = result.returncode == 0 and bool(self.candidate.patch())
        return Verdict(accepted, "Header resolves and C++ compilation passed" if accepted else "C++ compilation failed",
                       {"validator": self.name, "compiler_exit": result.returncode,
                        "header": match.group(1), "executes_candidate": False, **self.candidate.digests()})

    def judge(self, task, ctx):
        return self.validate(task)


def patch_tools(candidate, criteria, task, boundary):
    def submit_source(content: str):
        """Submit complete replacement source; return objective validation feedback."""
        boundary.check("repo.read")
        boundary.check("repo.write")
        if not isinstance(content, str) or len(content) > 20000:
            raise ValueError("Candidate must be a string of at most 20000 characters")
        candidate.content = content if content.endswith("\n") else content + "\n"
        verdict = criteria.validate(task)
        return {"accepted": verdict.done, "reason": verdict.reason,
                "candidate_sha256": candidate.digests()["candidate_sha256"]}

    return ToolBox().add("submit_source", submit_source, input_schema={
        "type": "object", "properties": {"content": {"type": "string", "maxLength": 20000}},
        "required": ["content"], "additionalProperties": False,
    })


def submission_ready(candidate, ctx):
    """A current artifact passed tool-side checks; final DoneCriteria still runs."""
    result = ctx.facts.get("tool_results", {}).get("submit_source", {})
    if (candidate.content is not None and result.get("accepted") is True and
            result.get("candidate_sha256") == candidate.digests()["candidate_sha256"]):
        return "Submitted candidate passed objective checks"
    return None


def fixture_solution(task):
    """OFFLINE CONTRACT REPLAY ONLY: known solutions, never called by live mode."""
    if task.kind == "python_quality":
        return task.payload["source"].replace("== None", "is None")
    return task.payload["source"].replace('"ProcessMacros.h"', '"include/ProcessMacros.h"')
