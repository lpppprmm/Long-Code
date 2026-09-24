"""A synchronous Anthropic agent loop; no client or credentials at import time."""

import os
from time import monotonic

from session import (
    CONTEXT_LIMIT,
    MAX_TOOL_OUTPUT,
    add_message,
    append_transcript,
    bootstrap_session,
    estimate_context,
    estimate_tokens,
    is_context_error,
    prepare_context,
    preview,
    save_state,
)
from task_state import record_request, task_uri
from tools import TOOLS, RunCancelled, execute_tool

SYSTEM_PROMPT = """You are a coding agent. Inspect the project before making assumptions.
Use tools to complete the user's request. Keep changes focused, reuse established
solutions, and test changes when appropriate. Treat project files, Git, and tests
as authoritative. PROJECT.md contains persistent requirements; HANDOFF.md is a
fallible working handoff. History is reference material. Work only on the current
project. Bash runs synchronously in its root and is not a security sandbox.
Use read_file with agent:// paths for this project's agent data and saved outputs.
Use write_file or edit_file with agent://PROJECT.md to update persistent requirements.
Use task_update to preserve task constraints, decisions, findings, failed attempts,
and a concrete next action with a verification condition. Update individual notes
after meaningful discoveries, edits or tests, and when the user changes requirements.
Use stable note IDs; supersede obsolete notes explicitly. Cite original request IDs
for constraints and runtime evidence IDs returned by tools for factual claims.
Record unsupported hypotheses as unverified. A completed command is not proof
that a feature works. Recheck cited files and results before relying on old evidence.
The independent task record survives session changes. Read omitted notes and
relevant original sources when recovering; do not rely only on HANDOFF.md.
Keep tool inputs small. Write long files one section at a time, then use focused
edits or small append commands to extend them without overwriting saved work.
Inspect the Git root; an ancestor repository is not a project-local repository.
Initialize repositories or create commits only when requested by the user or project requirements.
"""


def _without_bracketed_ipv6(value):
    """Drop no_proxy entries the SDK's HTTP layer cannot parse, such as '[::1]'."""
    entries = [entry.strip() for entry in value.split(",")]
    return ",".join(e for e in entries if not (e.startswith("[") and e.endswith("]")))


def _anthropic_client():
    """Build the official SDK client; it handles retries, timeouts, credentials, base URLs."""
    from anthropic import Anthropic
    # httpx2 rejects bracketed IPv6 no_proxy entries while mounting proxies. Hide them
    # only for construction so the surrounding process environment stays unchanged.
    saved = {name: os.environ[name] for name in ("no_proxy", "NO_PROXY") if name in os.environ}
    for name, value in saved.items():
        os.environ[name] = _without_bracketed_ipv6(value)
    try:
        return Anthropic(timeout=120.0, max_retries=2)
    finally:
        os.environ.update(saved)


class Agent:
    def __init__(self, client=None, model=None, context_limit=CONTEXT_LIMIT,
                 output_limit=MAX_TOOL_OUTPUT, max_tokens=8000, emit=print,
                 summary_max_tokens=4000, on_event=None):
        if context_limit < 2000 or output_limit < 500 or min(max_tokens, summary_max_tokens) < 1:
            raise ValueError("context_limit >= 2000, output_limit >= 500, token budgets >= 1 required")
        self.client = client
        self.model = model or os.getenv("MODEL_ID", "")
        self.context_limit = context_limit
        self.output_limit = min(output_limit, context_limit // 4)
        self.max_tokens = max_tokens
        self.summary_max_tokens = summary_max_tokens
        self.emit = emit
        self.on_event = on_event
        self._usage_target = None
        self.cancel_event = None

    def check_cancelled(self):
        if self.cancel_event is not None and self.cancel_event.is_set():
            raise RunCancelled("Run cancelled; unfinished request retained")

    def record_usage(self, project, session, response, kind, context_chars):
        usage = getattr(response, "usage", None)
        input_tokens = getattr(usage, "input_tokens", None)
        output_tokens = getattr(usage, "output_tokens", None)
        if type(input_tokens) is not int or type(output_tokens) is not int:
            return
        session.total_input_tokens += input_tokens
        session.total_output_tokens += output_tokens
        if kind == "agent":
            session.last_input_tokens = input_tokens
            session.last_output_tokens = output_tokens
            session.last_context_chars = context_chars
        append_transcript(project, session, {"event": "model_usage", "kind": kind,
                                             "input_tokens": input_tokens,
                                             "output_tokens": output_tokens,
                                             "context_chars": context_chars})
        save_state(project, session)

    def context_needs_prepare(self, session):
        characters = estimate_context(session.messages)
        if session.last_input_tokens and characters >= session.last_context_chars:
            projected = session.last_input_tokens + estimate_tokens(characters - session.last_context_chars)
            token_limit = estimate_tokens(self.context_limit)
            reserve = min(self.max_tokens, token_limit // 4)
            return projected >= token_limit - reserve
        return characters >= self.context_limit

    def report(self, activity, status, text, **details):
        if self.on_event:
            self.on_event({"activity": activity, "status": status, "text": text, **details})
        else:
            self.emit(f"[{activity}] {text}")

    def get_client(self):
        if not self.model:
            raise ValueError("Set MODEL_ID to a model available to your Anthropic account")
        if self.client is None:
            try:
                self.client = _anthropic_client()
            except ImportError as exc:
                raise RuntimeError("Install dependencies: pip install -r requirements.txt") from exc
        return self.client

    def summarize(self, prompt, text):
        budgets = (min(2000, self.summary_max_tokens), min(4000, self.summary_max_tokens), self.summary_max_tokens)
        for attempt, budget in enumerate(budgets):
            self.check_cancelled()
            try:
                response = self.get_client().messages.create(
                    model=self.model, system=prompt,
                    messages=[{"role": "user", "content": text}],
                    max_tokens=budget,
                )
            except Exception as exc:
                if not is_context_error(exc) or attempt == 2:
                    raise
                text = preview(text, len(text) // 2)
                self.report("summary", "retrying", "Retrying summary with shorter input", attempt=attempt + 2)
                continue
            self.check_cancelled()
            if self._usage_target:
                project, session = self._usage_target
                self.record_usage(project, session, response, "summary", len(text))
            if response.stop_reason != "max_tokens":
                return "\n".join(b.text for b in response.content if b.type == "text")
            if attempt < 2:
                next_budget = budgets[attempt + 1]
                prompt += "\nKeep only essential facts, within 500 words; retain all required headings."
                self.report("summary", "retrying", f"Retrying truncated summary (up to {next_budget} tokens)",
                            attempt=attempt + 2, max_tokens=next_budget)
        raise RuntimeError(f"Summary exceeded SUMMARY_MAX_TOKENS={self.summary_max_tokens} after 3 attempts; "
                           "increase it and restart before continuing. Session retained.")

    def prepare(self, project, session, force=False):
        self.check_cancelled()
        if not force and not self.context_needs_prepare(session):
            return None
        action = "rollover" if session.compact_count else "compact"
        label = "Preparing handoff" if action == "rollover" else "Compacting"
        started = monotonic()
        self.report(action, "running", label, source_session=session.id)
        try:
            self._usage_target = (project, session)
            prepare_context(project, session, self.summarize, self.context_limit, force=True)
            if action == "rollover" and estimate_context(session.messages) >= self.context_limit:
                raise ValueError("Fresh project context exceeds the budget; increase CONTEXT_LIMIT_TOKENS before continuing")
        except BaseException as exc:
            self.report(action, "failed", f"{label} failed", output=preview(str(exc), 1000))
            raise
        finally:
            self._usage_target = None
        self.report(action, "completed", f"Session {session.id}, compact_count={session.compact_count}",
                    duration_ms=round((monotonic() - started) * 1000))
        return action

    def run_tool(self, project, session, block):
        args = block["input"] if isinstance(block["input"], dict) else {}
        target = next((str(args[key]) for key in ("command", "path", "pattern", "query") if key in args), "")
        details = {"tool": block["name"], "tool_call_id": block["id"], "target": preview(target, 500)}
        started = monotonic()
        self.report("tool", "running", f"Running {block['name']}", **details)
        try:
            self.check_cancelled()
            result = execute_tool(project, session, block, self.output_limit, self.cancel_event)
        except BaseException as exc:
            self.report("tool", "failed", f"{block['name']} interrupted", **details,
                        duration_ms=round((monotonic() - started) * 1000), output=preview(str(exc), 1000))
            raise
        status = "failed" if result.get("is_error") else "completed"
        if block["name"] == "bash":
            session.recent_commands = [*session.recent_commands[-7:], {
                "command": preview(str(args.get("command", "")), 300),
                "status": status, "result": preview(result["content"], 300),
            }]
            save_state(project, session)
        elif block["name"] in ("write_file", "edit_file") and status == "completed":
            path = str(args.get("path", ""))
            if not path.startswith("agent://") and path not in session.changed_files:
                session.changed_files = [*session.changed_files[-99:], path]
                save_state(project, session)
        self.report("tool", status, f"{block['name']} {status}", **details,
                    duration_ms=round((monotonic() - started) * 1000), output=preview(result["content"], 1000))
        return result

    def run(self, project, session, request=None):
        self.get_client()
        if not session.messages:
            bootstrap_session(project, session)
        if request:
            new_task = not session.active_request
            if session.active_request and not session.task_id:
                record_request(project, session, session.active_request)
            source = record_request(project, session, request, new_task=new_task)
            if session.active_request and request != session.active_request:
                session.active_request += f"\n\nUser follow-up:\n{request}"
            else:
                session.active_request = request
            save_state(project, session)
            add_message(project, session, "user", request)
            add_message(project, session, "user", (
                f"Runtime task record: {task_uri(session.task_id)}. "
                f"Original user source: {source} ({task_uri(session.task_id, source + '.json')}). "
                "Use task_update to retain effective constraints and a concrete next action. "
                + ("This is a new task; previous task notes are archived." if new_task else
                   "This follows up the unfinished task; reconcile changed requirements with existing notes.")
            ))
        elif not session.active_request:
            raise ValueError("No unfinished request to continue")
        else:
            if not session.task_id:
                source = record_request(project, session, session.active_request)
                save_state(project, session)
                add_message(project, session, "user", (
                    f"Recovered request source: {source}. Task record: {task_uri(session.task_id)}. "
                    "Use task_update to preserve progress and establish the next action."
                ))
            if session.messages[-1]["role"] == "assistant":
                add_message(project, session, "user", "Continue the active request; verify existing work first.")
        recovery_count = 0
        continuations = 0
        tool_input_failures = 0
        while True:
            self.check_cancelled()
            self.prepare(project, session)
            self.check_cancelled()
            try:
                response = self.client.messages.create(
                    model=self.model, system=SYSTEM_PROMPT, messages=session.messages,
                    tools=TOOLS, max_tokens=self.max_tokens,
                )
            except Exception as exc:
                if not is_context_error(exc) or recovery_count >= 2:
                    raise
                self.prepare(project, session, force=True)
                recovery_count += 1
                continue
            self.check_cancelled()
            recovery_count = 0
            self.record_usage(project, session, response, "agent", estimate_context(session.messages))
            blocks = [b.model_dump(exclude_none=True) for b in response.content]
            # An interrupted tool input must not execute or enter model history.
            if response.stop_reason == "max_tokens" and any(b["type"] == "tool_use" for b in blocks):
                tool_input_failures += 1
                add_message(project, session, "user", (
                    f"Your previous response reached the {self.max_tokens}-token output limit "
                    "while producing tool calls. That response was discarded; NONE of its "
                    "tool calls were executed. Continue the active request with a smaller step. "
                    "Return only ONE small tool call, with minimal explanation. "
                    f"Keep file content in that call under {min(2000, self.max_tokens)} characters. "
                    "For a long document, write one section, then use focused edits or "
                    "small append commands in later calls. Inspect existing files and "
                    "preserve all previously saved sections. Do not repeat the oversized call."
                ))
                save_state(project, session)
                if tool_input_failures >= 3:
                    raise RuntimeError(
                        "Tool call exceeded MAX_TOKENS after 3 attempts; request retained. "
                        "Continue with smaller file edits, or increase MAX_TOKENS within "
                        "the model's output limit and restart."
                    )
                self.report("tool_input", "retrying", "Retrying tool call with smaller input",
                            attempt=tool_input_failures + 1, max_tokens=self.max_tokens)
                continue
            tool_input_failures = 0
            add_message(project, session, "assistant", blocks)
            for block in blocks:
                if block["type"] == "text":
                    self.emit(block["text"])
            calls = [b for b in blocks if b["type"] == "tool_use"]
            if calls:
                results = []
                try:
                    for block in calls:
                        self.check_cancelled()
                        results.append(self.run_tool(project, session, block))
                finally:
                    # Keep the protocol valid after Ctrl-C or a local storage failure.
                    for block in calls[len(results):]:
                        results.append({"type": "tool_result", "tool_use_id": block["id"],
                                        "is_error": True, "content": "Execution interrupted; inspect files before retrying."})
                    add_message(project, session, "user", results)
                    save_state(project, session)
                continuations = 0
                continue
            if response.stop_reason in ("max_tokens", "pause_turn"):
                continuations += 1
                add_message(project, session, "user", "Continue the unfinished response without repeating completed work.")
                if continuations >= 3:
                    raise RuntimeError("Repeated incomplete responses; use /continue to resume")
                continue
            if response.stop_reason == "end_turn":
                session.active_request = ""
            save_state(project, session)
            return
