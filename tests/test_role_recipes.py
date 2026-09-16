"""Recipe contract tests use an actual local Git repository, never live services."""
import json
import os
import subprocess
import sys
from pathlib import Path

import pytest

from employee_factory.recipes import CATALOG, Recipe, RecipeError, RecipeRegistry
from employee_factory.recipe_runtime import environment, run_instance, verify_instance
from tools.role_recipe import main


def write_json(path, data):
    path.write_text(json.dumps(data), encoding="utf-8")


@pytest.fixture
def source_recipe(tmp_path):
    repo = tmp_path / "source"
    repo.mkdir()
    script = '''import json, os, sys
from pathlib import Path
print(json.dumps({"project": os.environ["PROJECT"], "secret_present": bool(os.environ.get("APP_KEY")),
                  "workspace": os.environ["WORKSPACE"], "args": sys.argv[1:]}))
raise SystemExit(7 if "fail" in sys.argv else 0)
'''
    (repo / "cli.py").write_text(script)
    (repo / "requirements.txt").write_text("")
    (repo / "knowledge.md").write_text("Read project facts before claiming success.\n")
    (repo / "mcp.example.json").write_text('{"token":"${APP_KEY}"}')
    subprocess.run(["git", "init", "-q", str(repo)], check=True)
    subprocess.run(["git", "-C", str(repo), "add", "."], check=True)
    subprocess.run(["git", "-C", str(repo), "-c", "user.name=Recipe Test", "-c", "user.email=recipe@example.invalid",
                    "commit", "-qm", "fixture"], check=True)
    sha = subprocess.check_output(["git", "-C", str(repo), "rev-parse", "HEAD"], text=True).strip()
    definition = {"schema_version": "role-recipe/v1", "id": "test-worker", "version": "1.0.0",
        "title": "Test worker", "description": "Test-only application transport, not a model",
        "source": {"repository": "https://example.invalid/worker", "commit": sha, "include": ["*.py", "*.txt", "*.md", "*.json"]},
        "components": {role: ["cli.py"] for role in
                       ("task_source", "orchestration", "tools", "done_criteria", "knowledge", "failure", "observability")},
        "runtime": {"python_min": [3, 9], "entrypoint": "cli.py", "requirements": "requirements.txt",
                    "instance_paths": {"WORKSPACE": "workspace"}, "copy_files": {"mcp.json": "mcp.example.json"}},
        "settings": {"project": {"type": "text", "env": "PROJECT", "required": True}},
        "env_refs": {"APP_KEY": {"kind": "secret", "required": True}}}
    return repo, Recipe(definition)


def config(name="one"):
    return {"name": name, "recipe": "test-worker", "version": "1.0.0", "settings": {"project": name},
            "env_refs": {"APP_KEY": name.upper() + "_KEY"}}


def test_catalog_configs_validate_and_are_versioned():
    registry = RecipeRegistry()
    assert {r["id"] for r in registry.list()} == {"azure-codeagent", "sonarqube-autoflow"}
    for path in CATALOG.glob("*/*.example.json"):
        data = json.loads(path.read_text())
        normalized = registry.get(data["recipe"], data["version"]).validate(data)
        assert normalized["name"] == data["name"]
    with pytest.raises(RecipeError, match="Unknown recipe"):
        registry.get("azure-codeagent", "2.0.0")
    with pytest.raises(RecipeError, match="Duplicate"):
        registry.register(registry.get("azure-codeagent", "1.0.0"))


@pytest.mark.parametrize("change,match", [
    (lambda c: c["settings"].clear(), "Missing configuration"),
    (lambda c: c["settings"].update(unknown="ignored?"), "Unknown ordinary"),
    (lambda c: c["env_refs"].update(APP_KEY="token-with-dashes"), "NAME"),
    (lambda c: c.update(version="2.0.0"), "does not match"),
    (lambda c: c.update(name="../escape"), "portable identifier"),
])
def test_invalid_config_never_creates_an_output(source_recipe, tmp_path, change, match):
    source, recipe = source_recipe
    data = config()
    change(data)
    with pytest.raises(RecipeError, match=match):
        recipe.instantiate(data, source, tmp_path / "output")
    assert not (tmp_path / "output").exists()


def test_pinned_source_ignores_dirty_working_copy_and_secret_files(source_recipe, tmp_path, monkeypatch):
    source, recipe = source_recipe
    (source / "cli.py").write_text("raise RuntimeError('dirty edit')")
    (source / ".env").write_text("APP_KEY=do-not-copy")
    monkeypatch.setenv("ONE_KEY", "do-not-copy")
    instance = tmp_path / "employee"
    report = recipe.instantiate(config(), source, instance)
    assert report["valid"] and report["model_calls"] == 0 and not report["runtime_verified"]
    assert "dirty edit" not in (instance / "application/cli.py").read_text()
    assert not (instance / "application/.env").exists()
    assert all(b"do-not-copy" not in f.read_bytes() for f in instance.rglob("*") if f.is_file())
    assert (instance / "application/mcp.json").read_text() == '{"token":"${APP_KEY}"}'
    check = subprocess.run([sys.executable, str(instance / "launch.py"), "check"], cwd=tmp_path,
                           capture_output=True, text=True)
    assert check.returncode == 0, check.stderr
    assert json.loads(check.stdout)["recipe"] == "test-worker@1.0.0"


def test_two_instances_use_isolated_config_and_forward_exit_code(source_recipe, tmp_path, monkeypatch):
    source, recipe = source_recipe
    for name in ("one", "two"):
        recipe.instantiate(config(name), source, tmp_path / name)
        monkeypatch.setenv(name.upper() + "_KEY", "fake-runtime-credential")
    outputs = []
    for name in ("one", "two"):
        result = subprocess.run([sys.executable, str(tmp_path / name / "launch.py"), "run", name], cwd=tmp_path,
                                capture_output=True, text=True)
        assert result.returncode == 0, result.stdout + result.stderr
        outputs.append(json.loads(result.stdout))
    assert [r["project"] for r in outputs] == ["one", "two"]
    assert outputs[0]["workspace"] != outputs[1]["workspace"]
    assert all(r["secret_present"] for r in outputs)
    assert run_instance(tmp_path / "one", ["fail"]) == 7


def test_missing_secret_fails_before_application_execution(source_recipe, tmp_path, monkeypatch):
    source, recipe = source_recipe
    recipe.instantiate(config(), source, tmp_path / "one")
    monkeypatch.delenv("ONE_KEY", raising=False)
    monkeypatch.setenv("APP_KEY", "other-employees-key")
    env, missing = environment(tmp_path / "one", check=True)
    assert "APP_KEY" not in env and "ONE_KEY" in missing
    with pytest.raises(ValueError, match="ONE_KEY"):
        run_instance(tmp_path / "one", [])


@pytest.mark.parametrize("name", ["application/cli.py", "instance.json", "recipe.json", "recipe_support.py"])
def test_verify_rejects_modified_managed_files(source_recipe, tmp_path, name):
    source, recipe = source_recipe
    recipe.instantiate(config(), source, tmp_path / "one")
    (tmp_path / "one" / name).write_text("modified")
    with pytest.raises(ValueError, match="changed or missing"):
        verify_instance(tmp_path / "one")


def test_create_does_not_overwrite_or_leave_partial_output(source_recipe, tmp_path):
    source, recipe = source_recipe
    output = tmp_path / "one"
    recipe.instantiate(config(), source, output)
    with pytest.raises(RecipeError, match="already exists"):
        recipe.instantiate(config("two"), source, output)
    assert verify_instance(output)["name"] == "one"
    broken = recipe.describe()
    broken["components"]["tools"] = ["missing.py"]
    with pytest.raises(RecipeError, match="Missing source"):
        Recipe(broken).instantiate(config(), source, tmp_path / "bad")
    assert not (tmp_path / "bad").exists()


def test_external_config_is_not_overwritten_and_is_removed_on_exit(source_recipe, tmp_path, monkeypatch):
    source, original = source_recipe
    d = original.describe()
    d["runtime"]["external_files"] = {"runtime/mcp.json": "MCP_FILE"}
    d["env_refs"]["MCP_FILE"] = {"kind": "file", "required": True}
    recipe = Recipe(d)
    instance = tmp_path / "one"
    recipe.instantiate(config(), source, instance)
    external = tmp_path / "external.json"
    external.write_text('{"fake-token":"only-at-runtime"}')
    monkeypatch.setenv("ONE_KEY", "test-key")
    monkeypatch.setenv("MCP_FILE", str(external))
    assert run_instance(instance, []) == 0
    assert not (instance / "application/runtime/mcp.json").exists()
    (instance / "application/runtime/mcp.json").write_text("existing")
    with pytest.raises(FileExistsError):
        run_instance(instance, [])
    assert (instance / "application/runtime/mcp.json").read_text() == "existing"


def test_plan_cli_does_not_require_source_or_environment(tmp_path, capsys):
    example = CATALOG / "azure-codeagent/hsi.example.json"
    assert main(["plan", "--config", str(example)]) == 0
    plan = json.loads(capsys.readouterr().out)
    assert plan["valid"] and not plan["runtime_verified"]
    assert plan["config"]["settings"]["delivery_enabled"] is False
