import importlib.util
import json
from pathlib import Path
import re

import pytest

pytest.importorskip("jsonschema")

from agent_chassis import Outcome
from tools.verify_roadmap_evidence import verify_documents


def recipe():
    path = Path(__file__).resolve().parents[1] / "examples/07_verified_assembly.py"
    spec = importlib.util.spec_from_file_location("verified_assembly_recipe", path)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


@pytest.mark.parametrize("parallel", [1, 2])
@pytest.mark.parametrize("reject", [False, True])
def test_documented_assembly_runs_and_uses_independent_verdict(parallel, reject):
    result, manifest, evidence = recipe().run_example(parallel=parallel, reject=reject,
        source={"commit": "a" * 40, "dirty": False})
    assert result.outcome is (Outcome.FAILED if reject else Outcome.SUCCEEDED)
    run = evidence["runs"][0]
    assert run["execution"]["parallel_observed"] is (parallel > 1)
    assert run["execution"]["runs"][0]["limits"] == manifest["runtime"]["executors"]["react"]
    assert run["checks"][-1]["status"] == ("failed" if reject else "passed")
    assert run["steps"] == ["prepare", "work", "reasoning[ReAct]", "verify"]
    assert verify_documents(manifest, json.loads(json.dumps(evidence)), require_production=True) == 1
    with pytest.raises(ValueError, match="Not live"):
        verify_documents(manifest, json.loads(json.dumps(evidence)), require_live=True)


def test_skill_and_recipe_references_resolve():
    root = Path(__file__).resolve().parents[1]
    skill = root / ".claude/skills/assemble-digital-employee"
    for path in [skill / "SKILL.md", skill / "references/runtime-assembly.md"]:
        for target in re.findall(r"\]\(([^)]+)\)", path.read_text(encoding="utf-8")):
            if "://" not in target and not target.startswith("#"):
                assert (path.parent / target.split("#")[0]).exists(), target
