"""Standalone runtime copied into each recipe instance (stdlib only)."""
from __future__ import annotations

import argparse
import hashlib
import importlib.util
import json
import os
import shutil
import subprocess
import sys
from pathlib import Path, PurePosixPath


def _path(root, name):
    if (not isinstance(name, str) or not name or "\\" in name or ":" in name
            or PurePosixPath(name).is_absolute() or any(p in ("..", ".") for p in name.split("/"))):
        raise ValueError("Invalid instance file path")
    path = root / name
    if root.resolve() not in path.resolve().parents:
        raise ValueError("Instance file escapes its directory")
    if path.is_symlink() or any(p.is_symlink() for p in path.parents if p != root):
        raise ValueError("Managed instance files cannot use symlinks")
    return path


def verify_instance(root):
    root = Path(root).resolve()
    lock = json.loads((root / "recipe.lock.json").read_text(encoding="utf-8"))
    if lock.get("schema_version") != "role-instance/v1" or not isinstance(lock.get("files"), dict):
        raise ValueError("Unsupported instance lock")
    required = {"instance.json", "recipe.json", "launch.py", "recipe_support.py"}
    if not required.issubset(lock["files"]):
        raise ValueError("Incomplete instance lock")
    for name, expected in lock["files"].items():
        path = _path(root, name)
        if not path.is_file() or hashlib.sha256(path.read_bytes()).hexdigest() != expected:
            raise ValueError("Managed instance file changed or missing: " + name)
    recipe = json.loads((root / "recipe.json").read_text(encoding="utf-8"))
    config = json.loads((root / "instance.json").read_text(encoding="utf-8"))
    required_source = {recipe["runtime"]["entrypoint"], recipe["runtime"]["requirements"]}
    required_source.update(p for paths in recipe["components"].values() for p in paths)
    if not {"application/" + p for p in required_source}.issubset(lock["files"]):
        raise ValueError("Instance lock omits required source components")
    key = recipe["id"] + "@" + recipe["version"]
    if lock.get("recipe") != key or (config["recipe"], config["version"]) != (recipe["id"], recipe["version"]):
        raise ValueError("Instance recipe binding mismatch")
    return {"valid": True, "name": config["name"], "recipe": key,
            "source_commit": recipe["source"]["commit"], "managed_files": len(lock["files"]),
            "creation_mode": "recipe-instantiation", "model_calls": 0, "runtime_verified": False}


def environment(root, *, check=False):
    root = Path(root).resolve()
    recipe = json.loads((root / "recipe.json").read_text(encoding="utf-8"))
    config = json.loads((root / "instance.json").read_text(encoding="utf-8"))
    env, missing = dict(os.environ), []
    for name, value in config["settings"].items():
        kind = recipe["settings"][name]["type"]
        env[recipe["settings"][name]["env"]] = (json.dumps(value, ensure_ascii=False) if kind in ("argv", "boolean") else str(value))
    for target, alias in config["env_refs"].items():
        value = os.environ.get(alias, "")
        field = recipe["env_refs"][target]
        if not value:
            env.pop(target, None)
            if field.get("required"):
                missing.append(alias)
        else:
            if check and field.get("kind") in ("file", "directory"):
                path = Path(value).expanduser().resolve()
                valid = path.is_file() if field["kind"] == "file" else path.is_dir()
                if not valid or (field.get("contains") and not (path / field["contains"]).is_file()):
                    missing.append(alias + " (invalid " + field["kind"] + ")")
                value = str(path)
            env[target] = value
    for target, relative in recipe["runtime"].get("instance_paths", {}).items():
        env[target] = str(_path(root, "application/" + relative))
    if check:
        if sys.version_info[:2] < tuple(recipe["runtime"]["python_min"]):
            missing.append("Python " + ".".join(map(str, recipe["runtime"]["python_min"])) + "+")
        for executable in recipe["runtime"].get("executables", []):
            command = env.get(executable["env"], executable["command"]) if "env" in executable else executable["command"]
            if not shutil.which(command, path=env.get("PATH")):
                missing.append(executable.get("env", executable["command"]) + " (executable)")
        for module in recipe["runtime"].get("modules", []):
            if importlib.util.find_spec(module) is None:
                missing.append(module + " (Python dependency)")
    return env, sorted(set(missing))


def run_instance(root, arguments):
    verify_instance(root)
    root = Path(root).resolve()
    env, missing = environment(root, check=True)
    if missing:
        raise ValueError("Missing runtime requirements: " + ", ".join(missing))
    recipe = json.loads((root / "recipe.json").read_text(encoding="utf-8"))
    app = root / "application"
    # Instance-local settings override inherited settings from other employees.
    # External config files are copied only for the lifetime of this invocation.
    copied = []
    try:
        for destination, variable in recipe["runtime"].get("external_files", {}).items():
            path = _path(app, destination)
            path.parent.mkdir(parents=True, exist_ok=True)
            with path.open("xb") as target:
                copied.append(path)
                os.chmod(path, 0o600)
                target.write(Path(env[variable]).read_bytes())
        return subprocess.call([sys.executable, str(_path(app, recipe["runtime"]["entrypoint"])), *arguments], cwd=app, env=env)
    finally:
        for path in copied:
            path.unlink(missing_ok=True)


def main(argv=None):
    args = list(sys.argv[1:] if argv is None else argv)
    root = Path(__file__).resolve().parent
    try:
        if args and args[0] == "run":
            return run_instance(root, args[1:])
        parser = argparse.ArgumentParser(description="Check or launch a versioned role recipe instance")
        parser.add_argument("command", choices=["check"])
        parser.add_argument("--environment", action="store_true", help="Check runtime dependencies and environment references without connecting")
        parsed = parser.parse_args(args)
        report = verify_instance(root)
        if parsed.environment:
            _, missing = environment(root, check=True)
            report.update(environment_ready=not missing, missing=missing)
        print(json.dumps(report, ensure_ascii=False))
        return 2 if report.get("missing") else 0
    except (ValueError, OSError, KeyError) as exc:
        print(json.dumps({"valid": False, "error_type": type(exc).__name__, "error": str(exc),
                          "runtime_verified": False}, ensure_ascii=False))
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
