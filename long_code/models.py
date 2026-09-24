"""Project and session data structures, without runtime orchestration."""

from dataclasses import dataclass, field
from pathlib import Path

from .storage import timestamp


@dataclass
class Project:
    id: str
    name: str
    root: Path
    data_dir: Path

    @property
    def project_file(self):
        return self.data_dir / "PROJECT.md"

    @property
    def handoff_file(self):
        return self.data_dir / "HANDOFF.md"

    @property
    def state_file(self):
        return self.data_dir / "state.json"

    @property
    def transcripts_dir(self):
        return self.data_dir / "transcripts"

    @property
    def handoffs_dir(self):
        return self.data_dir / "handoffs"

    @property
    def checkpoints_dir(self):
        return self.data_dir / "checkpoints"

    def checkpoint_file(self, session_id):
        return self.checkpoints_dir / f"session_{session_id:03d}.json"

    def transcript_file(self, session_id):
        return self.transcripts_dir / f"session_{session_id:03d}.jsonl"


@dataclass
class Session:
    id: int = 1
    compact_count: int = 0
    messages: list = field(default_factory=list)
    active_request: str = ""
    task_id: str = ""
    todos: list = field(default_factory=list)
    changed_files: list = field(default_factory=list)
    recent_commands: list = field(default_factory=list)
    last_input_tokens: int = 0
    last_output_tokens: int = 0
    last_context_chars: int = 0
    total_input_tokens: int = 0
    total_output_tokens: int = 0
    last_handoff: str = ""
    created_at: str = field(default_factory=timestamp)
