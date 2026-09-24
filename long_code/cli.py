"""Interactive command-line adapter for the shared application runtime."""

import argparse
import os
import shlex
from pathlib import Path

from .agent import Agent
from .application import Application
from .config import agent_options, load_environment


def execute_command(app, line):
    if line in ("exit", "/exit"):
        return False
    if line == "/back":
        app.back()
        return True
    if line in ("help", "/help"):
        print("new <name> <path> | open <id> | list | current | exit\n"
              "In a project: chat, /continue, /back, /project <command>, /help")
        return True
    if line in ("current", "/current"):
        print(app.current())
        return True
    if line == "/continue":
        if not app.project:
            raise ValueError("Open a project first")
        app.agent.run(app.project, app.session)
        return True
    if app.project and not line.startswith("/project "):
        app.agent.run(app.project, app.session, line)
        return True
    parts = shlex.split(line.removeprefix("/project "))
    if not parts:
        return True
    command, *args = parts
    if command == "list" and not args:
        for project_id, entry in app.registry.entries().items():
            print(f"{project_id}: {entry['name']} — {entry['root']}")
    elif command == "current" and not args:
        print(app.current())
    elif command == "new" and len(args) == 2:
        project = app.registry.create(*args)
        app.open(project.id)
        print(app.current())
    elif command == "open" and len(args) == 1:
        app.open(args[0])
        print(app.current())
        if app.session.active_request:
            print("Unfinished request restored. Use /continue to resume.")
    else:
        raise ValueError("Unknown command or arguments; use /help")
    return True


def main(argv=None):
    load_environment()
    parser = argparse.ArgumentParser(prog="python -m long_code", description="Long Code")
    parser.add_argument("--data-home", type=Path,
                        default=Path(os.getenv("SIMPLE_AGENT_HOME", "~/.simple-agent")))
    args = parser.parse_args(argv)
    try:
        agent = Agent(**agent_options())
    except ValueError as exc:
        parser.error(str(exc))
    app = Application(args.data_home, agent)
    print("Simple Coding Agent\nUse /help for commands.")
    try:
        execute_command(app, "list")
        while True:
            try:
                prompt = f"{app.project.id} [{app.session.id}] > " if app.project else "> "
                line = input(prompt).strip()
                if line and not execute_command(app, line):
                    break
            except EOFError:
                break
            except KeyboardInterrupt:
                print("\nInterrupted. Use /continue to resume or /exit to leave.")
            except Exception as exc:  # noqa: BLE001 - keep the interactive CLI alive on API errors
                print(f"Error: {exc}")
    finally:
        app.close()
