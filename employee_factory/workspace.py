"""Filesystem-backed include repair with compiler checks, no fixed patch oracle."""
from __future__ import annotations

import hashlib
import re
import shutil
import subprocess
import tempfile
from pathlib import Path

from agent_chassis.contracts import DoneCriteria, Task, TaskSource, Verdict
from agent_chassis.evidence import content_id
from agent_chassis.orchestration import ToolBox
from payloads.patch_showcase import Candidate

SUFFIXES = {".cpp", ".cc", ".cxx", ".h", ".hpp", ".hxx"}
INCLUDE = re.compile(r'^\s*#\s*include\s*[<"][^>"\n]+[>"]\s*$')


def load_workspace(repo, unit):
    repo = repo.resolve()
    path = Path(unit)
    if path.is_absolute() or ".." in path.parts or path.suffix not in {".cpp", ".cc", ".cxx"}:
        raise ValueError("unit must be a relative C++ source path")
    files = {}
    for file in sorted(repo.rglob("*")):
        relative = file.relative_to(repo)
        if any(part.startswith(".") or part in ("build", "dist", "node_modules") for part in relative.parts):
            continue
        if file.suffix not in SUFFIXES or not file.is_file():
            continue
        if file.is_symlink():
            raise ValueError("Use regular source files in this reference workspace")
        files[relative.as_posix()] = file.read_text(encoding="utf-8")
        if len(files) > 200 or sum(len(s) for s in files.values()) > 200000:
            raise ValueError("Reference workspace supports at most 200 source files / 200000 characters")
    if path.as_posix() not in files:
        raise ValueError("Compilation unit not found in the source snapshot")
    if len(files[path.as_posix()]) > 20000:
        raise ValueError("Compilation unit exceeds the reference tool's 20000-character limit")
    return {"unit": path.as_posix(), "files": files}


def compile_snapshot(snapshot, candidate=None):
    compiler = shutil.which("c++")
    if compiler is None:
        raise RuntimeError("C++ compiler unavailable; validation was not performed")
    with tempfile.TemporaryDirectory(prefix="chassis-build-check-") as directory:
        root = Path(directory)
        for name, text in snapshot["files"].items():
            destination = root / name
            destination.parent.mkdir(parents=True, exist_ok=True)
            destination.write_text(candidate if name == snapshot["unit"] and candidate is not None else text,
                                   encoding="utf-8")
        result = subprocess.run([compiler, "-std=c++17", "-fsyntax-only", "-I", ".", snapshot["unit"]],
                                cwd=root, capture_output=True, text=True, timeout=20)
        diagnostics = (result.stdout + result.stderr).replace(str(root), "<workspace>")[-8000:]
    return {"exit_code": result.returncode, "diagnostics": diagnostics,
            "command": ["c++", "-std=c++17", "-fsyntax-only", "-I", ".", snapshot["unit"]]}


def body_without_includes(text):
    return [line for line in text.splitlines() if not INCLUDE.fullmatch(line)]


class WorkspaceSource(TaskSource):
    name = "filesystem-cpp-build"

    def __init__(self, snapshot, baseline):
        self.snapshot, self.baseline, self.sent = snapshot, baseline, False
        self.task = Task(content_id(snapshot)[:16], "cpp_include_repair", {
            "target": {"path": snapshot["unit"]}, "source": snapshot["files"][snapshot["unit"]],
            "files": sorted(snapshot["files"]), "build_error": baseline["diagnostics"],
            "goal": "Fix the actual compiler error by changing include directives only. "
                    "Read project headers when needed. To compare multiple files, use one read_files(paths) call; "
                    "never issue multiple tool calls in one response. "
                    "Submit the complete source; preserve every other line.",
        }, self.name)

    def fetch(self, limit=1):
        if self.sent or limit <= 0:
            return []
        self.sent = True
        return [self.task]


class CompilerCriteria(DoneCriteria):
    name = "compiler-and-include-only/v1"

    def __init__(self, snapshot, candidate, baseline):
        self.snapshot, self.candidate, self.baseline = snapshot, candidate, baseline

    def validate(self):
        original = self.snapshot["files"][self.snapshot["unit"]]
        if self.baseline["exit_code"] == 0:
            return Verdict(False, "Original already compiles; no failing build was reproduced")
        if self.candidate.content is None:
            return Verdict(False, "No candidate submitted")
        if body_without_includes(original) != body_without_includes(self.candidate.content):
            return Verdict(False, "Non-include source lines changed")
        if not self.candidate.patch():
            return Verdict(False, "No source change")
        result = compile_snapshot(self.snapshot, self.candidate.content)
        passed = result["exit_code"] == 0
        return Verdict(passed, "Compiler and unchanged-body checks passed" if passed else result["diagnostics"],
                       {"validator": self.name, "workspace_id": content_id(self.snapshot),
                        "compiler_exit": result["exit_code"], **self.candidate.digests()})

    def judge(self, task, ctx):
        verdict = self.validate()
        if verdict.done and ctx.facts.get("verified_candidate") != self.candidate.digests()["candidate_sha256"]:
            return Verdict(False, "Verification node did not check the current candidate")
        return verdict


def workspace_tools(snapshot, candidate, criteria, boundary):
    def read_file(path):
        """Read a source/header file from the actual project snapshot."""
        boundary.check("repo.read")
        if path not in snapshot["files"]:
            return {"found": False, "path": path}
        text = snapshot["files"][path]
        return {"found": True, "path": path, "content": text[:12000], "truncated": len(text) > 12000}

    def submit_source(content):
        """Submit replacement C++ source and receive real compiler feedback."""
        boundary.check("repo.write")
        if not isinstance(content, str) or len(content) > 20000:
            raise ValueError("Candidate must be a string of at most 20000 characters")
        candidate.content = content if content.endswith("\n") else content + "\n"
        verdict = criteria.validate()
        return {"accepted": verdict.done, "reason": verdict.reason,
                "candidate_sha256": candidate.digests()["candidate_sha256"]}

    def read_files(paths):
        """Compare up to 6 source/header files in ONE tool call; prefer this for same-named headers."""
        if not isinstance(paths, list) or not 1 <= len(paths) <= 6 or any(not isinstance(p, str) for p in paths):
            raise ValueError("Read between 1 and 6 file paths")
        return {"files": [read_file(path) for path in paths]}

    return (ToolBox().add("read_file", read_file, parallel_safe=True, input_schema={
        "type": "object", "properties": {"path": {"type": "string"}}, "required": ["path"],
        "additionalProperties": False}).add("read_files", read_files, parallel_safe=True, input_schema={
        "type": "object", "properties": {"paths": {"type": "array", "items": {"type": "string"},
        "minItems": 1, "maxItems": 6}}, "required": ["paths"], "additionalProperties": False
        }).add("submit_source", submit_source, input_schema={
        "type": "object", "properties": {"content": {"type": "string", "maxLength": 20000}},
        "required": ["content"], "additionalProperties": False}))
