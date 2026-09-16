"""Versioned, source-backed role recipes. No business logic enters the core SDK."""
from __future__ import annotations

import ast
import fnmatch
import hashlib
import io
import json
import os
import re
import shutil
import subprocess
import tarfile
import tempfile
from pathlib import Path, PurePosixPath
from urllib.parse import urlsplit

from employee_factory.recipe_runtime import verify_instance

CATALOG = Path(__file__).resolve().parents[1] / "role_recipes"
SCHEMA = "role-recipe/v1"


class RecipeError(ValueError):
    pass


def digest(data):
    return hashlib.sha256(data).hexdigest()


def json_bytes(value):
    return (json.dumps(value, ensure_ascii=False, sort_keys=True, indent=2) + "\n").encode("utf-8")


def safe_path(value):
    if (not isinstance(value, str) or not value or "\\" in value
            or PurePosixPath(value).is_absolute() or any(p in ("..", ".") for p in value.split("/"))
            or ":" in value or "\x00" in value):
        raise RecipeError("Recipe file paths must be portable relative paths")
    return value


def _env_name(value):
    return isinstance(value, str) and re.fullmatch(r"[A-Za-z_][A-Za-z0-9_]*", value)


class Recipe:
    """Public recipe interface: describe, validate configuration, materialize.

    The source implementation owns its task source, tools and acceptance. A
    recipe selects a tested source revision and exposes its deployment settings.
    """

    def __init__(self, definition):
        self.definition = json.loads(json.dumps(definition))
        d = self.definition
        if not isinstance(d, dict):
            raise RecipeError("Recipe definition must be an object")
        if d.get("schema_version") != SCHEMA:
            raise RecipeError("Unsupported recipe schema")
        if not re.fullmatch(r"[a-z][a-z0-9-]*", d.get("id", "")) or not re.fullmatch(r"\d+\.\d+\.\d+", d.get("version", "")):
            raise RecipeError("Recipe requires an id and semantic version")
        if not d.get("title") or not d.get("description"):
            raise RecipeError("Recipe requires a title and description")
        source = d.get("source", {})
        if not re.fullmatch(r"[0-9a-f]{40}", source.get("commit", "")):
            raise RecipeError("Recipe source must pin a full commit SHA")
        if not isinstance(source.get("include"), list) or not source["include"] or not source.get("repository"):
            raise RecipeError("Recipe requires a source repository and include patterns")
        components = d.get("components", {})
        for role in ("task_source", "orchestration", "tools", "done_criteria", "knowledge", "failure", "observability"):
            if not isinstance(components.get(role), list) or not components[role]:
                raise RecipeError(f"Recipe requires component references: {role}")
            for reference in components[role]:
                safe_path(reference)
        runtime = d.get("runtime", {})
        safe_path(runtime.get("entrypoint"))
        safe_path(runtime.get("requirements"))
        minimum = runtime.get("python_min")
        if not isinstance(minimum, list) or len(minimum) != 2 or any(type(x) is not int for x in minimum):
            raise RecipeError("runtime.python_min requires [major, minor]")
        for path, origin in runtime.get("copy_files", {}).items():
            safe_path(path)
            safe_path(origin)
        for path, env in runtime.get("external_files", {}).items():
            safe_path(path)
            if env not in d.get("env_refs", {}):
                raise RecipeError("External file must reference a declared environment variable")
            if d["env_refs"][env].get("kind") != "file" or not d["env_refs"][env].get("required"):
                raise RecipeError("External file references must be required file references")
        for env, path in runtime.get("instance_paths", {}).items():
            if not _env_name(env):
                raise RecipeError("Instance path targets must be environment identifiers")
            safe_path(path)
        seen_env = set()
        for name, field in d.get("settings", {}).items():
            env = field.get("env")
            if not _env_name(env) or env in seen_env:
                raise RecipeError("Setting environment targets must be unique identifiers")
            seen_env.add(env)
            if field.get("type") not in ("text", "url", "integer", "boolean", "argv"):
                raise RecipeError(f"Unsupported setting type: {name}")
        for env in d.get("env_refs", {}):
            if not _env_name(env) or env in seen_env:
                raise RecipeError("Environment references must not overlap ordinary settings")
            if d["env_refs"][env].get("kind") not in ("secret", "file", "directory"):
                raise RecipeError("Environment references must declare secret, file or directory kind")

    @property
    def key(self):
        return self.definition["id"] + "@" + self.definition["version"]

    def describe(self):
        return json.loads(json.dumps(self.definition))

    def validate(self, config):
        d = self.definition
        if not isinstance(config, dict) or set(config) - {"name", "recipe", "version", "settings", "env_refs"}:
            raise RecipeError("Configuration accepts name, recipe, version, settings and env_refs")
        if (config.get("recipe"), config.get("version")) != (d["id"], d["version"]):
            raise RecipeError("Configuration recipe/version does not match the selected recipe")
        if not re.fullmatch(r"[a-zA-Z0-9][a-zA-Z0-9_-]{0,79}", config.get("name", "")):
            raise RecipeError("Instance name must be a portable identifier of at most 80 characters")
        settings, refs = config.get("settings", {}), config.get("env_refs", {})
        if not isinstance(settings, dict) or set(settings) - set(d.get("settings", {})):
            raise RecipeError("Unknown ordinary settings; use show to inspect the recipe")
        if not isinstance(refs, dict) or set(refs) - set(d.get("env_refs", {})):
            raise RecipeError("Unknown environment references; use show to inspect the recipe")
        normalized, bindings, missing = {}, {}, []
        for name, field in d.get("settings", {}).items():
            value = settings.get(name, field.get("default"))
            if value is None:
                if field.get("required"):
                    missing.append("settings." + name)
                continue
            kind = field["type"]
            valid = ((kind in ("text", "url") and isinstance(value, str) and bool(value.strip()) and not any(c in value for c in "\r\n\x00"))
                     or (kind == "integer" and type(value) is int and field.get("min", 0) <= value <= field.get("max", 2**31 - 1))
                     or (kind == "boolean" and type(value) is bool)
                     or (kind == "argv" and isinstance(value, list) and bool(value)
                         and all(isinstance(x, str) and x and "\x00" not in x for x in value)))
            if not valid:
                raise RecipeError(f"Invalid value for setting {name}; expected {kind}")
            if kind == "url":
                url = urlsplit(value)
                if url.scheme not in ("https", "http") or not url.hostname or url.username or url.password or url.query or url.fragment:
                    raise RecipeError(f"Setting {name} requires an HTTP(S) URL without credentials/query/fragment")
            if "choices" in field and value not in field["choices"]:
                raise RecipeError(f"Unsupported choice for {name}")
            normalized[name] = value
        for name, field in d.get("env_refs", {}).items():
            alias = refs.get(name, field.get("default", name))
            if not _env_name(alias):
                raise RecipeError(f"env_refs.{name} must contain an environment variable NAME, not its value")
            bindings[name] = alias
        if missing:
            raise RecipeError("Missing configuration: " + ", ".join(missing))
        return {"name": config["name"], "recipe": d["id"], "version": d["version"],
                "settings": normalized, "env_refs": bindings}

    def instantiate(self, config, source_dir, output):
        return instantiate(self, config, source_dir, output)


class RecipeRegistry:
    def __init__(self, directory=CATALOG):
        self.recipes = {}
        for path in sorted(Path(directory).glob("*/recipe.json")):
            self.register(Recipe(json.loads(path.read_text(encoding="utf-8"))))

    def register(self, recipe):
        if recipe.key in self.recipes:
            raise RecipeError("Duplicate recipe version: " + recipe.key)
        self.recipes[recipe.key] = recipe
        return recipe

    def get(self, name, version):
        try:
            return self.recipes[name + "@" + version]
        except KeyError:
            raise RecipeError("Unknown recipe version: " + name + "@" + version) from None

    def list(self):
        return [r.describe() for _, r in sorted(self.recipes.items())]


def _source_files(recipe, source_dir):
    d = recipe.definition
    source = d["source"]
    result = subprocess.run(["git", "-C", str(source_dir), "archive", "--format=tar", source["commit"]],
                            capture_output=True, timeout=60)
    if result.returncode:
        raise RecipeError("Source checkout does not contain the pinned commit; clone/fetch the recipe repository first")
    files = {}
    with tarfile.open(fileobj=io.BytesIO(result.stdout)) as archive:
        for member in archive:
            if not member.isfile() and not member.issym():
                continue
            name = member.name
            if not any(fnmatch.fnmatchcase(name, pattern) for pattern in source["include"]):
                continue
            safe_path(name)
            if member.issym():
                raise RecipeError("Recipe source must use regular files: " + name)
            if any(p in (".git", ".env", "__pycache__") for p in PurePosixPath(name).parts) or name.endswith((".db", ".pyc")):
                continue
            files[name] = archive.extractfile(member).read()
    expected = {d["runtime"]["entrypoint"], d["runtime"]["requirements"]}
    expected.update(p for references in d["components"].values() for p in references)
    expected.update(d["runtime"].get("copy_files", {}).values())
    if expected - files.keys():
        raise RecipeError("Missing source components: " + ", ".join(sorted(expected - files.keys())))
    for name, data in files.items():
        if name.endswith(".py"):
            ast.parse(data, filename=name)
    return files


def instantiate(recipe, config, source_dir, output):
    config = recipe.validate(config)
    files = _source_files(recipe, source_dir)
    output = Path(output).absolute()
    if output.exists():
        raise RecipeError("Output already exists; create a new instance directory")
    output.parent.mkdir(parents=True, exist_ok=True)
    staging = Path(tempfile.mkdtemp(prefix=".role-recipe-", dir=output.parent))
    try:
        managed = {"application/" + name: data for name, data in files.items()}
        for destination, origin in recipe.definition["runtime"].get("copy_files", {}).items():
            if destination in files:
                raise RecipeError("Runtime copy destination overlaps source: " + destination)
            managed["application/" + destination] = files[origin]
        managed["instance.json"] = json_bytes(config)
        managed["recipe.json"] = json_bytes(recipe.describe())
        managed["recipe_support.py"] = Path(__file__).with_name("recipe_runtime.py").read_bytes()
        managed["launch.py"] = b'from recipe_support import main\n\nif __name__ == "__main__":\n    raise SystemExit(main())\n'
        ignored = [".env", "*.db", "__pycache__/", "application/workspace/", "application/logs/"]
        ignored.extend("application/" + name for name in recipe.definition["runtime"].get("external_files", {}))
        managed[".gitignore"] = ("\n".join(ignored) + "\n").encode("utf-8")
        refs = "\n".join(f"{alias}=" for alias in sorted(set(config["env_refs"].values())))
        managed["environment.example"] = ("# Inject these environment variables at runtime; do not store credentials here.\n" + refs + "\n").encode()
        managed["README.md"] = (f"# {config['name']}\n\n岗位：{recipe.key}\n\n"
            "这是固定版本岗位配方实例，创建过程没有调用模型。业务执行沿用原项目的真实接口。\n\n"
            "1. `python launch.py check` 校验源码、配置及配方完整性（不连接服务）。\n"
            "2. `python -m pip install -r application/requirements.txt` 安装原岗位依赖。\n"
            "3. 根据 environment.example 注入环境变量，参考 recipe.json 中 env_refs 的说明。\n"
            "4. `python launch.py check --environment` 检查运行依赖和环境引用。\n"
            "5. `python launch.py run --help` 查看原岗位 CLI；显式使用 run 启动原业务入口。\n\n"
            f"原项目：{recipe.definition['source']['repository']}\n"
            f"源码版本：{recipe.definition['source']['commit']}\n\n"
            "application/README.md 保留原项目说明。实例互不共享队列、日志或工作区。\n"
            "check 通过表示实例完整，不代表真实任务验收通过；未运行模型或企业接口时 runtime_verified 为 false。\n"
            "配置和源码均已记录摘要；需要更换配置时创建新实例，人工改动会在 check 时被指出。\n").encode("utf-8")
        for name, data in managed.items():
            path = staging / name
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_bytes(data)
        lock = {"schema_version": "role-instance/v1", "recipe": recipe.key,
                "files": {name: digest(data) for name, data in sorted(managed.items())},
                "creation_mode": "recipe-instantiation", "model_calls": 0, "runtime_verified": False}
        (staging / "recipe.lock.json").write_bytes(json_bytes(lock))
        result = verify_instance(staging)
        os.rename(staging, output)
        return {**result, "output": str(output)}
    finally:
        if staging.exists():
            shutil.rmtree(staging)
