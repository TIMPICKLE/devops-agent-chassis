"""Offline ReAct concurrency demonstration: python examples/06_parallel_tools.py."""
from pathlib import Path
import sys
from threading import Barrier, Lock, get_ident

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

from agent_chassis.contracts import RunContext, Task
from agent_chassis.orchestration import ReActPattern, ToolBox, ToolRequest


def main():
    rendezvous = Barrier(2, timeout=5)
    lock = Lock()
    workers = set()

    def read(value):
        with lock:
            workers.add(get_ident())
        rendezvous.wait()  # Serial execution cannot pass this rendezvous.
        return {"value": value, "checked": True}

    box = ToolBox().add("read", read, parallel_safe=True)
    calls = [ToolRequest(f"read-{value}", "read", {"value": value}) for value in (1, 2)]
    pattern = ReActPattern(lambda t, c, b: ("batch", calls, None), max_parallel_tools=2,
                           max_iterations=1, stop_when=lambda t, c: "observations collected")
    ctx = RunContext()
    pattern.reason(Task("parallel-demo", "offline"), ctx, box)
    assert len(workers) == 2
    assert [call.result["value"] for call in ctx.tool_calls] == [1, 2]
    print("PASS: two tools rendezvoused on two workers; results retained in request order.")
    print(f"Iterations: {ctx.iterations}; tool actions: {ctx.tool_actions_started}; model requests: {len(ctx.model_calls)}")
    print("This demonstrates scheduling only; a full employee still needs independent DoneCriteria.")


if __name__ == "__main__":
    main()
