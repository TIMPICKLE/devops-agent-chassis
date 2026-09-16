"""Validate real recipe sources and two Azure instances without business services."""
from __future__ import annotations

import argparse
import json
import os
import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path[:0] = [str(ROOT / "src"), str(ROOT)]

from employee_factory.recipes import CATALOG, RecipeRegistry, json_bytes
from employee_factory.recipe_runtime import environment, verify_instance


def verify_examples(azure_source, sonar_source, output):
    output.mkdir(parents=True, exist_ok=False)
    registry, reports = RecipeRegistry(), []
    for role, example, source in [("azure-codeagent", "hsi", azure_source),
                                  ("azure-codeagent", "portals", azure_source),
                                  ("sonarqube-autoflow", "hsi", sonar_source)]:
        config = json.loads((CATALOG / role / (example + ".example.json")).read_text(encoding="utf-8"))
        instance = output / config["name"]
        report = registry.get(role, config["version"]).instantiate(config, source, instance)
        env, _ = environment(instance)
        # Only import the original application's config, which does no network I/O.
        if role == "azure-codeagent":
            probe = ("from config import Config; import json; print(json.dumps({"
                     "'project':Config.COMMENT_TARGET_PROJECT,'repository':Config.COMMENT_TARGET_REPOSITORY,"
                     "'port':Config.COMMENT_WEBHOOK_PORT,'workspace':Config.GIT_WORKSPACE_ROOT,"
                     "'delivery':Config.COMMENT_DELIVERY_ENABLED,'notifications':Config.COMMENT_NOTIFICATIONS_ENABLED}))")
        else:
            probe = ("from config import Config; import json; print(json.dumps({"
                     "'project':Config.SONARQUBE_PROJECT_KEY,'workspace':Config.GIT_REPO_PATH}))")
        result = subprocess.run([sys.executable, "-c", probe], cwd=instance / "application", env=env,
                                capture_output=True, text=True, timeout=30, check=True)
        observed = json.loads(result.stdout)
        expected_project = config["settings"].get("project", config["settings"].get("project_key"))
        if observed["project"] != expected_project or Path(observed["workspace"]) != instance / "application/workspace":
            raise ValueError("Original application did not consume the instance configuration")
        report["configuration_verified"] = True
        if role == "azure-codeagent":
            if observed["delivery"] or observed["notifications"] or observed["port"] != config["settings"]["port"]:
                raise ValueError("Azure instance deployment settings differ")
            env["CHASSIS_ROOT"] = str(ROOT)
            env["PYTHONPATH"] = os.pathsep.join([str(ROOT / "src"), str(instance / "application")])
            result = subprocess.run([sys.executable, "-m", "pytest", "tests/test_chassis_payload.py", "-q"],
                                    cwd=instance / "application", env=env, capture_output=True, text=True, timeout=60)
            (output / (config["name"] + ".tests.txt")).write_text(result.stdout + result.stderr, encoding="utf-8")
            if result.returncode:
                raise ValueError("Azure offline payload acceptance failed; inspect the saved test log")
            report["offline_payload_tests"] = "passed"
        else:
            report["offline_payload_tests"] = "not_run; configuration and source integrity only"
        verify_instance(instance)
        reports.append(report)
    summary = {"schema_version": "role-recipe-validation/v1", "instances": reports,
               "live_business_verified": False, "model_calls": 0}
    (output / "summary.json").write_bytes(json_bytes(summary))
    return summary


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--azure-source", type=Path, required=True)
    parser.add_argument("--sonar-source", type=Path, required=True)
    parser.add_argument("--output-dir", type=Path, required=True)
    args = parser.parse_args(argv)
    result = verify_examples(args.azure_source.resolve(), args.sonar_source.resolve(), args.output_dir.resolve())
    print(json.dumps(result, ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
