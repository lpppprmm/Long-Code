"""Command-line project selection and chat."""

import argparse
import os
import shlex
from pathlib import Path

from agent import Agent
from session import (
    Registry,
    bootstrap_session,
    configured_context_limit,
    load_session,
    save_state,
)


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

    def command(self, line):
        if line in ("exit", "/exit"):
            return False
        if line == "/back":
            self.back()
            return True
        if line in ("help", "/help"):
            print("new <name> <path> | open <id> | list | current | exit\n"
                  "In a project: chat, /continue, /back, /project <command>, /help")
            return True
        if line in ("current", "/current"):
            print(self.current())
            return True
        if line == "/continue":
            if not self.project:
                raise ValueError("Open a project first")
            self.agent.run(self.project, self.session)
            return True
        if self.project and not line.startswith("/project "):
            self.agent.run(self.project, self.session, line)
            return True
        parts = shlex.split(line.removeprefix("/project "))
        if not parts:
            return True
        command, *args = parts
        if command == "list" and not args:
            for project_id, entry in self.registry.entries().items():
                print(f"{project_id}: {entry['name']} — {entry['root']}")
        elif command == "current" and not args:
            print(self.current())
        elif command == "new" and len(args) == 2:
            project = self.registry.create(*args)
            self.open(project.id)
            print(self.current())
        elif command == "open" and len(args) == 1:
            self.open(args[0])
            print(self.current())
            if self.session.active_request:
                print("Unfinished request restored. Use /continue to resume.")
        else:
            raise ValueError("Unknown command or arguments; use /help")
        return True


def main(argv=None):
    # Optional at startup so project management and tests work without API packages.
    try:
        from dotenv import load_dotenv
        load_dotenv(Path(__file__).with_name(".env"))
    except ImportError:
        pass
    parser = argparse.ArgumentParser(description="Simple Coding Agent")
    parser.add_argument("--data-home", type=Path,
                        default=Path(os.getenv("SIMPLE_AGENT_HOME", "~/.simple-agent")))
    args = parser.parse_args(argv)
    try:
        agent = Agent(context_limit=configured_context_limit(),
                      output_limit=int(os.getenv("MAX_TOOL_OUTPUT", "10000")),
                      summary_max_tokens=int(os.getenv("SUMMARY_MAX_TOKENS", "4000")),
                      max_tokens=int(os.getenv("MAX_TOKENS", "8000")))
    except ValueError as exc:
        parser.error(str(exc))
    app = Application(args.data_home, agent)
    print("Simple Coding Agent\nUse /help for commands.")
    try:
        app.command("list")
        while True:
            try:
                prompt = f"{app.project.id} [{app.session.id}] > " if app.project else "> "
                line = input(prompt).strip()
                if line and not app.command(line):
                    break
            except EOFError:
                break
            except KeyboardInterrupt:
                print("\nInterrupted. Use /continue to resume or /exit to leave.")
            except Exception as exc:  # noqa: BLE001 - keep the interactive CLI alive on API errors
                print(f"Error: {exc}")
    finally:
        app.close()


if __name__ == "__main__":
    main()
