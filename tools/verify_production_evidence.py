"""Payload-independent production-format evidence check; does not replay tasks."""
import argparse
import json
from pathlib import Path
import sys

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from tools.verify_roadmap_evidence import verify_documents


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("manifest", type=Path)
    parser.add_argument("evidence", type=Path)
    parser.add_argument("--require-live", action="store_true")
    args = parser.parse_args(argv)
    count = verify_documents(json.loads(args.manifest.read_text(encoding="utf-8")),
                             json.loads(args.evidence.read_text(encoding="utf-8")),
                             require_production=True, require_live=args.require_live)
    print(f"Verified {count} production-format report(s); no task replay or authenticity attestation.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
