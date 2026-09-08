from __future__ import annotations

import importlib.util
import os
import subprocess
import sys
from pathlib import Path

import pytest


ROOT = Path(__file__).resolve().parents[1]
EXAMPLES = sorted((ROOT / "examples").glob("*.py"))
OPTIONAL_IMPORTS = {
    "07_verified_assembly.py": ("jsonschema", "llm"),
}


@pytest.mark.parametrize("script", EXAMPLES, ids=lambda path: path.name)
def test_example_script_runs_to_completion(script: Path):
    requirement = OPTIONAL_IMPORTS.get(script.name)
    if requirement is not None:
        module, extra = requirement
        if importlib.util.find_spec(module) is None:
            pytest.skip(f"{script.name} requires the optional .[{extra}] dependency")

    env = os.environ.copy()
    env["PYTHONUTF8"] = "1"
    completed = subprocess.run(
        [sys.executable, str(script)],
        cwd=ROOT,
        env=env,
        capture_output=True,
        text=True,
        timeout=30,
        check=False,
    )
    assert completed.returncode == 0, (
        f"{script.name} exited with {completed.returncode}\n"
        f"--- stdout ---\n{completed.stdout[-4000:]}\n"
        f"--- stderr ---\n{completed.stderr[-4000:]}"
    )
