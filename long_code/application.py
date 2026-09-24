"""Project selection and runtime ownership shared by the CLI and web API."""

from pathlib import Path

from .agent import Agent
from .projects import Registry
from .session import bootstrap_session, load_session, save_state


class Application:
    def __init__(self, data_home, agent=None):
        self.registry = Registry(Path(data_home))
        self.agent = agent or Agent()
        self.project = None
        self.session = None

    def back(self):
        if self.project:
            save_state(self.project, self.session)
            self.session.messages.clear()
            self.session.todos.clear()
        self.project = self.session = None

    def close(self):
        try:
            self.back()
        finally:
            self.registry.close()

    def open(self, project_id):
        project = self.registry.open(project_id)
        self.back()
        session = load_session(project)
        self.project, self.session = project, session
        bootstrap_session(project, session)

    def current(self):
        if not self.project:
            return "No project selected."
        return (f"Project: {self.project.name} ({self.project.id})\nRoot: {self.project.root}\n"
                f"Session: {self.session.id} | Compactions: {self.session.compact_count}\n"
                f"Project requirements: {self.project.project_file}")
