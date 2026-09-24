"""Real API and agent, with a deterministic offline model for the browser demo."""

import tempfile
from pathlib import Path
from threading import Event

import uvicorn
from anthropic.types import Message

from agent import Agent
from api import create_app
from session import HANDOFF_SECTIONS


def response(content, stop="end_turn"):
    return Message.model_validate({
        "id": "offline-demo", "type": "message", "role": "assistant", "model": "offline-demo",
        "content": content, "stop_reason": stop, "stop_sequence": None,
        "usage": {"input_tokens": 100, "output_tokens": 20},
    })


class DemoModel:
    def __init__(self):
        self.messages = self
        self.turns = 0
        self.summaries = 0
        self.advance = Event()

    def create(self, **kwargs):
        if "tools" not in kwargs:
            self.summaries += 1
            if self.summaries == 1:
                return response([{"type": "text", "text": "Incomplete summary"}], "max_tokens")
            # Let the browser inspect each in-progress lifecycle phase before continuing.
            if not self.advance.wait(30):
                raise RuntimeError("The browser did not advance the offline demo within 30 seconds")
            self.advance.clear()
            handoff = "# Session Handoff\n\n" + "\n\n".join(
                f"## {heading}\n\nPreserve DEMO_REQUIREMENT. Verify demo output before finishing."
                for heading in HANDOFF_SECTIONS
            )
            return response([{"type": "text", "text": handoff}])

        self.turns += 1
        if self.turns in (3, 5):
            # Rollover must bootstrap from requirements and handoff, without old messages.
            assert len(kwargs["messages"]) == 1
            bootstrap = kwargs["messages"][0]["content"]
            assert "DEMO_REQUIREMENT" in bootstrap and "Verify demo output" in bootstrap
            assert "Complete the offline rollover demo" in bootstrap
            assert "demo-constraint" in bootstrap and "Inspect the generated demo artifacts" in bootstrap
        if self.turns == 5:
            return response([{"type": "text", "text": "Offline rollover demo complete. Requirements retained through Session 3."}])
        calls = []
        if self.turns == 1:
            calls.extend([
                ("write_file", {"path": "agent://PROJECT.md", "content": "# Project\n\nPreserve DEMO_REQUIREMENT."}),
                ("todo_write", {"todos": [{"content": "Verify demo output", "status": "pending"}]}),
                ("task_update", {
                    "notes": [{"id": "demo-constraint", "kind": "constraint",
                               "text": "Preserve DEMO_REQUIREMENT across all sessions.",
                               "status": "active", "evidence": ["request-0001"]}],
                    "next_action": {"action": "Inspect the generated demo artifacts.",
                                    "verification": "Verify demo output retains the requested content.",
                                    "files": ["artifact-4.txt"], "evidence": []},
                }),
            ])
        # A large tool input crosses the real threshold; its content is not dumped into the UI.
        calls.append(("write_file", {"path": f"artifact-{self.turns}.txt", "content": "demo evidence\n" * 1600}))
        return response([
            {"type": "tool_use", "id": f"demo_{self.turns}_{i}", "name": name, "input": args}
            for i, (name, args) in enumerate(calls)
        ], "tool_use")


if __name__ == "__main__":
    with tempfile.TemporaryDirectory(prefix="long-code-demo-") as directory:
        base = Path(directory)
        repo = base / "repo"
        repo.mkdir()
        model = DemoModel()
        app = create_app(base / "data", Agent(model, "offline-demo", context_limit=16_000))
        app.state.workspace.runtime.registry.create("Rollover demo", str(repo))

        @app.post("/__demo__/advance")
        def advance():
            model.advance.set()
            return {"summary_calls": model.summaries}

        uvicorn.run(app, host="127.0.0.1", port=8765)
