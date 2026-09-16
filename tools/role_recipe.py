"""Discover, inspect, plan and instantiate versioned employee role recipes."""
from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path[:0] = [str(ROOT / "src"), str(ROOT)]

from employee_factory.recipes import CATALOG, RecipeRegistry
from employee_factory.recipe_runtime import verify_instance


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--catalog", type=Path, default=CATALOG, help="Trusted recipe catalog directory")
    commands = parser.add_subparsers(dest="command", required=True)
    commands.add_parser("list")
    show = commands.add_parser("show")
    show.add_argument("recipe")
    show.add_argument("--version", required=True)
    for command in ("plan", "create"):
        sub = commands.add_parser(command)
        sub.add_argument("--config", type=Path, required=True)
        if command == "create":
            sub.add_argument("--source-dir", type=Path, required=True)
            sub.add_argument("--output-dir", type=Path, required=True)
    check = commands.add_parser("verify")
    check.add_argument("instance", type=Path)
    args = parser.parse_args(argv)
    try:
        if args.command == "verify":
            result = verify_instance(args.instance)
        else:
            registry = RecipeRegistry(args.catalog)
            if args.command == "list":
                result = [{k: d[k] for k in ("id", "version", "title", "description")} for d in registry.list()]
            elif args.command == "show":
                result = registry.get(args.recipe, args.version).describe()
            else:
                config = json.loads(args.config.read_text(encoding="utf-8"))
                recipe = registry.get(config["recipe"], config["version"])
                normalized = recipe.validate(config)
                result = ({"valid": True, "config": normalized, "source": recipe.describe()["source"],
                           "creation_mode": "recipe-instantiation", "model_calls": 0, "runtime_verified": False}
                          if args.command == "plan" else recipe.instantiate(normalized, args.source_dir, args.output_dir))
        print(json.dumps(result, ensure_ascii=False, indent=2))
        return 0
    except (ValueError, OSError, KeyError, TypeError) as exc:
        print(json.dumps({"valid": False, "error_type": type(exc).__name__, "error": str(exc),
                          "runtime_verified": False}, ensure_ascii=False))
        return 2


if __name__ == "__main__":
    raise SystemExit(main())
