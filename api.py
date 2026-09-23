"""Local FastAPI adapter. The frontend is served independently from frontend/."""

import json
import os
from collections import deque
from contextlib import asynccontextmanager, contextmanager
from pathlib import Path
from threading import Event, Lock
from uuid import uuid4

from dotenv import load_dotenv
from fastapi import FastAPI, HTTPException, Query, Request
from fastapi.middleware.cors import CORSMiddleware
from fastapi.middleware.trustedhost import TrustedHostMiddleware
from fastapi.responses import JSONResponse
from pydantic import BaseModel, ConfigDict, Field

from agent import Agent
from main import Application
from session import (
    atomic_write,
    configured_context_limit,
    estimate_context,
    estimate_tokens,
    list_sessions,
    read_preview,
    search_history,
    session_history,
    timestamp,
)
from tools import RunCancelled

MAX_EVENT_LOG_BYTES = 4 * 1024 * 1024
COMPACT_EVENT_LOG_BYTES = MAX_EVENT_LOG_BYTES // 2


class NewProject(BaseModel):
    model_config = ConfigDict(str_strip_whitespace=True, extra="forbid")
    name: str = Field(min_length=1, max_length=100)
    root: str = Field(min_length=1, max_length=4096)


class ChatRequest(BaseModel):
    model_config = ConfigDict(str_strip_whitespace=True, extra="forbid")
    project_id: str
    message: str | None = Field(default=None, min_length=1, max_length=100_000)


class Workspace:
    def __init__(self, data_home, agent):
        self.runtime = Application(data_home, agent)
        self.runtime.agent.emit = self.emit
        self.runtime.agent.on_event = self.activity
        self.lock = Lock()
        self.cancel_event = Event()
        self.chat_active = False
        self.runtime.agent.cancel_event = self.cancel_event
        self.events = deque(maxlen=200)
        self.view = {}
        self.phase = None
        self.refresh()

    @contextmanager
    def operation(self):
        if not self.lock.acquire(blocking=False):
            raise HTTPException(409, "The agent is busy. Wait for this run to finish.")
        try:
            yield
        except ValueError as exc:
            raise HTTPException(400, str(exc)) from exc
        finally:
            try:
                self.phase = None
                self.refresh()
            finally:
                self.lock.release()

    def refresh(self):
        project, session = self.runtime.project, self.runtime.session
        context_size = estimate_context(session.messages) if session else 0
        documents = {}
        if project:
            for name, path in (("project", project.project_file), ("handoff", project.handoff_file)):
                documents[name] = read_preview(path, 20_000) if path.exists() else ""
        # Publish a complete snapshot; readers never traverse a mutating messages list.
        self.view = {
            "project": {"id": project.id, "name": project.name, "root": str(project.root)} if project else None,
            "session": {
                "id": session.id, "compact_count": session.compact_count,
                "active_request": session.active_request, "todos": list(session.todos),
                "context_size": context_size,
                "context_limit": self.runtime.agent.context_limit,
                "context_tokens": estimate_tokens(context_size),
                "context_limit_tokens": estimate_tokens(self.runtime.agent.context_limit),
                "last_input_tokens": session.last_input_tokens,
                "last_output_tokens": session.last_output_tokens,
                "total_input_tokens": session.total_input_tokens,
                "total_output_tokens": session.total_output_tokens,
            } if session else None,
            "events": list(self.events), "documents": documents,
            "model": self.runtime.agent.model,
            "phase": self.phase,
        }

    def state(self):
        return {**self.view, "busy": self.lock.locked()}

    def open(self, project_id):
        if self.runtime.project and self.runtime.project.id == project_id:
            return
        self.runtime.open(project_id)
        self.events.clear()
        path = self.runtime.project.data_dir / "web-events.jsonl"
        if path.exists():
            with path.open(encoding="utf-8") as handle:
                for line in handle:
                    try:
                        self.events.append(json.loads(line))
                    except json.JSONDecodeError:
                        continue  # A crash may leave an incomplete last line.
            if path.stat().st_size > MAX_EVENT_LOG_BYTES:
                self.compact_events(path)

    def compact_events(self, path):
        retained = []
        size = 0
        for item in reversed(self.events):
            line = json.dumps(item, ensure_ascii=False) + "\n"
            length = len(line.encode("utf-8"))
            if retained and size + length > COMPACT_EVENT_LOG_BYTES:
                break
            retained.append(line)
            size += length
        retained.reverse()
        atomic_write(path, "".join(retained))
        self.events = deque((json.loads(line) for line in retained), maxlen=200)

    def record(self, kind, text, **details):
        event = {"id": uuid4().hex, "kind": kind, "text": text,
                 "time": timestamp(), "session": self.runtime.session.id, **details}
        path = self.runtime.project.data_dir / "web-events.jsonl"
        with path.open("a", encoding="utf-8") as handle:
            handle.write(json.dumps(event, ensure_ascii=False) + "\n")
            handle.flush()
        self.events.append(event)
        if path.stat().st_size > MAX_EVENT_LOG_BYTES:
            self.compact_events(path)
        self.refresh()

    def emit(self, text):
        kind = "activity" if text.startswith(("[tool]", "[compact]", "[rollover]")) else "assistant"
        self.record(kind, text)

    def activity(self, event):
        if event["status"] == "running":
            self.phase = event["text"]
        elif event["status"] in ("completed", "failed"):
            self.phase = None
        self.record("activity", **event)


def create_app(data_home=None, agent=None):
    load_dotenv(Path(__file__).with_name(".env"))
    workspace = Workspace(
        data_home or os.getenv("SIMPLE_AGENT_HOME", "~/.simple-agent"),
        agent or Agent(context_limit=configured_context_limit(),
                       output_limit=int(os.getenv("MAX_TOOL_OUTPUT", "10000")),
                       summary_max_tokens=int(os.getenv("SUMMARY_MAX_TOKENS", "4000")),
                       max_tokens=int(os.getenv("MAX_TOKENS", "8000"))),
    )

    @asynccontextmanager
    async def lifespan(_app):
        yield
        workspace.runtime.close()
        client = workspace.runtime.agent.client
        if client is not None and hasattr(client, "close"):
            client.close()

    app = FastAPI(title="Long Code API", version="1.0.0", lifespan=lifespan)
    app.state.workspace = workspace
    origins = {o.strip() for o in os.getenv(
        "FRONTEND_ORIGINS", "http://localhost:5173,http://127.0.0.1:5173",
    ).split(",")}
    app.add_middleware(CORSMiddleware, allow_origins=list(origins),
                       allow_methods=["GET", "POST"], allow_headers=["Content-Type"])
    app.add_middleware(TrustedHostMiddleware, allowed_hosts=["localhost", "127.0.0.1", "testserver"])

    @app.middleware("http")
    async def check_origin(request: Request, call_next):
        # CORS alone does not stop simple cross-origin POSTs from changing state.
        origin = request.headers.get("origin")
        if request.method == "POST" and origin and origin not in origins:
            return JSONResponse({"detail": "Origin is not allowed"}, status_code=403)
        return await call_next(request)

    @app.exception_handler(OSError)
    async def storage_error(_request: Request, exc: OSError):
        return JSONResponse({"detail": f"Local storage error: {exc}"}, status_code=500)

    @app.get("/api/state")
    def state():
        return workspace.state()

    @app.get("/api/projects")
    def projects():
        return [{"id": key, **value} for key, value in workspace.runtime.registry.entries().items()]

    @app.post("/api/projects", status_code=201)
    def new_project(body: NewProject):
        with workspace.operation():
            project = workspace.runtime.registry.create(body.name, body.root)
            workspace.open(project.id)
        return workspace.state()

    @app.post("/api/projects/{project_id}/open")
    def open_project(project_id: str):
        with workspace.operation():
            if project_id not in workspace.runtime.registry.entries():
                raise HTTPException(404, "Project not found")
            workspace.open(project_id)
        return workspace.state()

    @app.get("/api/projects/{project_id}/history")
    def history(project_id: str, query: str):
        try:
            project = workspace.runtime.registry.open(project_id)
            return {"text": search_history(project, query)}
        except ValueError as exc:
            raise HTTPException(400, str(exc)) from exc

    @app.get("/api/projects/{project_id}/sessions")
    def sessions(project_id: str):
        try:
            return list_sessions(workspace.runtime.registry.open(project_id))
        except ValueError as exc:
            raise HTTPException(400, str(exc)) from exc

    @app.get("/api/projects/{project_id}/sessions/{session_id}")
    def session_detail(project_id: str, session_id: int,
                       offset: int = Query(default=0, ge=0),
                       limit: int = Query(default=50, ge=1, le=100)):
        try:
            return session_history(workspace.runtime.registry.open(project_id), session_id, offset, limit)
        except ValueError as exc:
            raise HTTPException(404, str(exc)) from exc

    @app.post("/api/chat")
    def chat(body: ChatRequest):
        with workspace.operation():
            runtime = workspace.runtime
            if not runtime.project or runtime.project.id != body.project_id:
                raise HTTPException(409, "The selected project changed. Refresh before sending.")
            if body.message is None and not runtime.session.active_request:
                raise HTTPException(400, "There is no unfinished request to continue.")
            workspace.cancel_event.clear()
            workspace.chat_active = True
            try:
                runtime.agent.get_client()
                workspace.record("user", body.message or "Continue the unfinished request.")
                runtime.agent.run(runtime.project, runtime.session, body.message)
            except RunCancelled:
                workspace.record("activity", "Run cancelled; unfinished request retained",
                                 activity="run", status="interrupted")
            except Exception as exc:
                workspace.record("error", str(exc))
                raise HTTPException(502, str(exc)) from exc
            finally:
                workspace.chat_active = False
                workspace.cancel_event.clear()
        return workspace.state()

    @app.post("/api/chat/cancel")
    def cancel_chat():
        if not workspace.chat_active:
            return {"accepted": False}
        workspace.cancel_event.set()
        return {"accepted": True}

    return app


class LazyApp:
    """Create the default workspace when the ASGI server starts, not on import."""

    def __init__(self):
        self.instance = None
        self.lock = Lock()

    async def __call__(self, scope, receive, send):
        if self.instance is None:
            with self.lock:
                if self.instance is None:
                    self.instance = create_app()
        await self.instance(scope, receive, send)


app = LazyApp()
