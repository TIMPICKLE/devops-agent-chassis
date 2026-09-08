"""Project-knowledge-dependent deployment configuration repair, with a pure oracle.

Only produces a local configuration and diff. No cluster or deployment API is
called. Business rules stay in this payload and the versioned benchmark data.
"""
from __future__ import annotations

import json
from copy import deepcopy

from agent_chassis.contracts import DoneCriteria, Task, TaskSource, Verdict
from agent_chassis.evidence import content_id


RULE_DESCRIPTION = (
    "For matching project/environment documents, combine their disjoint rules. "
    "replicas = max(minimum_replicas, ceil(peak_rps / capacity_per_replica) + reserve_replicas). "
    "Copy cpu_millicores, port, timeout_ms and rollout_batch from the matching documents. "
    "Change only these five top-level configuration fields; preserve every other field. "
    "Submit the complete replacement JSON via submit_source."
)


def matching_documents(snapshot, task):
    return [d for d in snapshot["documents"]
            if all(task.payload.get(k) == v for k, v in d["scope"].items())]


class ConfigSource(TaskSource):
    name = "public-config-policy-v1"

    def __init__(self, case):
        original = {"service": case["project"] + "-api", "image": "example/api:1.4.2",
                    "labels": {"team": case["project"], "keep": "unchanged"},
                    "replicas": 1, "cpu_millicores": 100, "port": 8080,
                    "timeout_ms": 1000, "rollout_batch": 5}
        self.task = Task(case["id"], "config_policy", {
            "project": case["project"], "environment": case["environment"], "peak_rps": case["peak_rps"],
            "target": {"path": "deployment.json"}, "source": json.dumps(original, indent=2) + "\n",
            "goal": "Repair deployment.json using the supplied project and environment specifications. "
                    + RULE_DESCRIPTION,
        }, self.name)
        self.sent = False

    def fetch(self, limit=1):
        if self.sent or limit <= 0:
            return []
        self.sent = True
        return [self.task]


def expected_config(snapshot, task):
    """Deterministic oracle; never supplied to the live decider or its tools."""
    rules = {}
    for doc in matching_documents(snapshot, task):
        if rules.keys() & doc["rules"].keys():
            raise ValueError("Ambiguous policy: matching documents redefine a rule")
        rules.update(doc["rules"])
    required = {"capacity_per_replica", "cpu_millicores", "port", "reserve_replicas",
                "minimum_replicas", "timeout_ms", "rollout_batch"}
    if rules.keys() != required or any(type(v) is not int or v < 0 for v in rules.values()):
        raise ValueError("Incomplete or invalid policy")
    capacity = rules["capacity_per_replica"]
    if capacity <= 0:
        raise ValueError("Capacity must be positive")
    result = json.loads(task.payload["source"])
    result.update({k: rules[k] for k in ("cpu_millicores", "port", "timeout_ms", "rollout_batch")})
    result["replicas"] = max(rules["minimum_replicas"],
                             (task.payload["peak_rps"] + capacity - 1) // capacity + rules["reserve_replicas"])
    return result


def strict_config(text):
    def unique(pairs):
        result = {}
        for key, value in pairs:
            if key in result:
                raise ValueError("Duplicate configuration key")
            result[key] = value
        return result
    value = json.loads(text, object_pairs_hook=unique,
                       parse_constant=lambda value: (_ for _ in ()).throw(ValueError("Non-finite value")))
    if not isinstance(value, dict):
        raise ValueError("Configuration must be an object")
    return value


class ConfigCriteria(DoneCriteria):
    name = "project-config-policy-and-invariants/v1"

    def __init__(self, candidate, snapshot):
        self.candidate, self.snapshot = candidate, deepcopy(snapshot)

    def validate(self, task):
        expected = expected_config(self.snapshot, task)
        try:
            actual = strict_config(self.candidate.content or "")
        except (ValueError, TypeError):
            return Verdict(False, "Candidate must be valid JSON with unique keys")
        # Canonical hashes distinguish booleans and integers (Python True == 1).
        accepted = content_id(actual) == content_id(expected) and bool(self.candidate.patch())
        return Verdict(accepted, "Project policy and unchanged-field checks passed" if accepted else
                       "Project policy or unchanged-field checks failed; consult the matching specifications",
                       {"validator": self.name, "policy_id": content_id(self.snapshot),
                        "configuration_id": content_id(actual), **self.candidate.digests()})

    def judge(self, task, ctx):
        verdict = self.validate(task)
        if not verdict.done:
            return verdict
        if ctx.facts.get("verified_candidate") != self.candidate.digests()["candidate_sha256"]:
            return Verdict(False, "Verification node did not check the current artifact")
        return verdict
