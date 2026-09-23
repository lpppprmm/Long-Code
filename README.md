# Long Code — Simple Coding Agent

A synchronous, project-scoped coding agent implementing the [V1 technical design](long-code%20text.en.md).
Each session may compact its context once. The next compaction creates a factual
handoff and a fresh session, which inspects the repository before continuing.

For the browser interface, follow [Web workspace](#web-workspace). For CLI mode,
follow the instructions below.

## Run

Requires Python 3.10+, Bash, a POSIX system (Linux or macOS), and an
Anthropic-compatible Messages API. Git enables
repository inspection but is not required for non-Git projects.

```sh
python3 -m venv .venv
. .venv/bin/activate
pip install -r requirements.txt
[ -f .env ] || cp .env.example .env
# Set ANTHROPIC_API_KEY and MODEL_ID in .env.
python main.py
```

The environment lives in the project-local, git-ignored `.venv/`. Without
activating it, call the interpreter directly (`.venv/bin/python main.py`).
`pip install -r requirements-dev.txt` adds the lint tooling used below on top of
the runtime dependencies.

`python code.py` remains an equivalent entry point. Project management works
without credentials; model calls require the API configuration. Environment
variables take precedence over `.env` in this application's directory.

```text
> new my-project ~/code/my-project
my-project [1] > Implement the parser and run its tests.
my-project [1] > /current
my-project [1] > /back
> list
> open my-project
my-project [1] > /continue
my-project [1] > /exit
```

The repository directory must already exist. Quote names or paths containing
spaces. IDs are generated from names, for example `"My Project"` → `my-project`.
Names without Latin letters or digits receive a generated `project-...` ID.
Use `/project open <id>` to switch directly, `/project list` to list projects,
and `/help` for commands. Plain text inside a project is sent to the agent.
`/continue` resumes the saved unfinished request after an error or restart.
Ctrl-C interrupts the current action and returns to the prompt; EOF exits.

## Web workspace

The web interface runs as a separate Vite frontend and FastAPI backend. It reuses
the CLI's project registry, tools, and session lifecycle. Requires Node.js 20.19+
or 22.12+ in addition to the Python environment above.

Keep the backend and frontend running in **two separate terminals**. The commands
below assume the checkout is at `/home/lpxbtt/code/long-code`; adjust the path if
you cloned it elsewhere.

For first-time setup, create the Python environment and install dependencies:

```sh
cd /home/lpxbtt/code/long-code
python3 -m venv .venv
.venv/bin/pip install -r requirements.txt
[ -f .env ] || cp .env.example .env
npm --prefix frontend ci
```

Set `ANTHROPIC_API_KEY` and `MODEL_ID` in the root `.env`. Set
`ANTHROPIC_BASE_URL` if you use a compatible provider. Skip the setup commands
when the environment and dependencies are already installed.

**Terminal 1 — start FastAPI:**

```sh
cd /home/lpxbtt/code/long-code
.venv/bin/python -m uvicorn api:app --host 127.0.0.1 --port 8000
```

**Terminal 2 — start the frontend:**

```sh
cd /home/lpxbtt/code/long-code/frontend
npm run dev
```

Open [http://127.0.0.1:5173](http://127.0.0.1:5173) for the web interface.
The API's interactive documentation is at
[http://127.0.0.1:8000/docs](http://127.0.0.1:8000/docs).

**A `404 Not Found` at `http://127.0.0.1:8000/` is expected.** Port 8000 serves
API routes, not the frontend; `/` and `/favicon.ico` are not defined there. Use
port 5173 to open the page.

If startup reports that port 8000 or 5173 is already in use, reuse the running
service or stop it with `Ctrl-C` in its terminal before restarting. You can inspect
the listener with `lsof -i :8000` or `lsof -i :5173`. Stop both services with
`Ctrl-C` in their respective terminals when finished.

The frontend offers project
creation and switching, chat, progress updates, context usage, todos, project
documents, history search, a session timeline, and continuation of unfinished requests. Responses
render as sanitized Markdown. The page polls for progress while a chat request
runs, so tool activity and session transitions appear before the request finishes.
Use **停止任务** during a run to stop the next agent step or an active Bash command.
The unfinished request remains available through **继续未完成的请求**. A model API
call already in flight finishes before cancellation takes effect.
Tool activity includes the command or file target, running/completed/failed status,
elapsed time, and an expandable result preview. Compaction and handoff phases are
shown while they run, including summary retries.

The backend permits one active run at a time and rejects project changes or a
second chat while busy. Closing the browser does not cancel an accepted run;
reopening reconnects to the current workspace. Recent web conversation events
are stored in each project's `web-events.jsonl` independently of model context.
Opening another project loads only that project's events. A server restart
requires selecting the project again; its unfinished request can then be resumed.
The event log keeps recent activity and is compacted when it exceeds 4 MiB.

The data home uses a process lock: another CLI or API process using the same home
exits with an error until the first process stops. Run one backend worker. This is
a local, single-user interface with no login: keep the backend
bound to loopback. It can execute shell commands with your user permissions.

The frontend defaults to `http://127.0.0.1:8000`. To change it, copy
`frontend/.env.example` to `frontend/.env.local` and set `VITE_API_URL` before
starting or building Vite. The backend's `FRONTEND_ORIGINS` is a comma-separated
allowlist, defaulting to `http://localhost:5173,http://127.0.0.1:5173`.

```sh
cd frontend
npm run build       # Static frontend output: frontend/dist/
npm run preview     # Preview the build on port 5173; backend runs separately
npx playwright install chromium
npm test           # Browser tests use mocked API responses, no model calls
```

The API exposes `GET /api/projects`, `POST /api/projects`,
`POST /api/projects/{id}/open`, `GET /api/state`, `POST /api/chat`,
`POST /api/chat/cancel`,
`GET /api/projects/{id}/history?query=...`, and the session list/detail routes
under `GET /api/projects/{id}/sessions`. Chat requires `project_id` and a
`message`; omit `message` to continue the current unfinished request. API keys
stay in the backend environment and are never sent to the browser.

## State and recovery

By default, the registry lives at `~/.simple-agent/projects.json`. Each registered
project has its own directory under `~/.simple-agent/projects/<id>/`:

- `PROJECT.md`: user-maintained long-term goals, constraints, and design decisions.
- `state.json`: program-owned session number, compaction count, request, and todos.
- `HANDOFF.md` and `handoffs/session_NNN.md`: latest and archived working handoffs.
- `checkpoints/session_NNN.json`: program-recorded request, unfinished todos,
  recent command outcomes, Git state, relevant file hashes, and token totals.
- `transcripts/session_NNN.jsonl`: append-only messages and compaction events.
- `tool-results/`: outputs too large to include in model context.

Edit the generated `PROJECT.md` to record stable project requirements. Temporary
progress belongs in the handoff. Every fresh context includes directory inspection,
Git status, staged/unstaged diff statistics, key-file previews, persistent rules,
the handoff, and the active request. Files and tests outrank the handoff.

The first compaction preserves a small suffix of complete tool exchanges and a
summary; it never overwrites the original transcript. The next compaction archives
the handoff, advances the session, clears messages, and bootstraps automatically.
Unfinished todos are recorded verbatim in the handoff and carried into the new
session's structured todo list. Rollover continues the same
request without user intervention. Reopening restores the session counter and
compaction count, then inspects the repository and compares the checkpoint's Git
state and recorded file hashes without replaying transcripts. The comparison
result is written to the new session's transcript.
Historical details can be retrieved with `search_history`.
The session badge in the browser opens archived handoffs and paged transcript
records, including for projects created before checkpoints were introduced.

State, registry, handoffs, and project file writes use atomic replacement. A failed
project registration removes its newly created data directory. Messages are flushed to the
transcript as they arrive. If execution is interrupted, an external command may
have made partial changes; the agent is instructed to inspect before retrying.
A crash immediately after the model's final response may leave the request marked
unfinished, so recovery should verify the files before doing more work.

## Tools and configuration

The static tool set is `bash`, `read_file`, `write_file`, `edit_file`, `glob`,
`todo_write`, and `search_history`. File writes and edits reject paths outside
the repository, including symlink escapes, except for the explicit
`agent://PROJECT.md` path for persistent requirements. Both `write_file` and
`edit_file` support that path. Other agent data, including state, handoffs, and
transcripts, is read-only through file tools. `read_file` accepts `agent://...`
for this project's data and saved outputs. Each read is limited to 1,000 lines
and 100,000 characters, with a maximum starting offset of 100,000 lines. History
search scans at most 32 MiB per request and reports when it stops early; it never
searches another project. Bash uses the project root as its working directory;
it **is not a security sandbox** and executes with the CLI user's permissions.
Commands run synchronously with a default 120-second timeout (maximum 600 seconds).
Bash output is capped at 10 MiB; a command exceeding that limit is stopped and
returns an error with its captured output available in `tool-results/`.
Use one CLI process or one API worker per data home.
Bootstrap identifies the Git root and whether it belongs to the selected project
or an ancestor directory. The agent is instructed to initialize repositories and
create commits only when requested by the user or project requirements.

| Setting | Default | Meaning |
| --- | --- | --- |
| `ANTHROPIC_API_KEY` | required for chat | API credential |
| `MODEL_ID` | required for chat | Model available on your endpoint |
| `ANTHROPIC_BASE_URL` | SDK default | Optional compatible Messages API endpoint |
| `SIMPLE_AGENT_HOME` | `~/.simple-agent` | Registry and project data |
| `CONTEXT_LIMIT_TOKENS` | `100000` | Estimated token budget before compaction; minimum 500 |
| `CONTEXT_LIMIT` | `400000` | Legacy character budget; used only when `CONTEXT_LIMIT_TOKENS` is unset; minimum 2000 |
| `MAX_TOOL_OUTPUT` | `10000` | Tool-result preview limit; minimum 500, capped at one quarter of context |
| `MAX_TOKENS` | `8000` | Output token limit per coding model call |
| `SUMMARY_MAX_TOKENS` | `4000` | Maximum output tokens per summary/handoff attempt; independent of `MAX_TOKENS`, minimum 1 |

For the demo, the context budget defaults to **100,000 estimated tokens**. Set
`CONTEXT_LIMIT_TOKENS=100000` in the root `.env`, then restart the backend or CLI
to apply it. A smaller value, such as `20000`, demonstrates compaction and
rollover sooner. The first threshold crossing compacts the session; the next
creates a handoff and a fresh session.

The frontend displays estimated tokens using four characters per token and, after
a model response, its actual input-token count. Before any measured usage is
available, the runtime uses the character estimate. After a response, it projects
the next input size from the measured token count plus new message characters,
reserving space for output. This projection is still approximate because message
content and model overhead change. The token setting takes
precedence over the legacy character setting. `MAX_TOKENS` controls response
length independently.

Summaries start with up to 2,000 output tokens. A truncated summary is retried with
a shorter requested answer and up to 4,000 output tokens; the final attempt uses
the full `SUMMARY_MAX_TOKENS` budget. Every attempt is capped by that setting.
There are at most three attempts, including context-error
retries. Failed summaries leave the existing session and checkpoint intact.
If retries are exhausted, raise `SUMMARY_MAX_TOKENS` in `.env`, restart the
backend or CLI, and continue the unfinished request.

`--data-home PATH` overrides `SIMPLE_AGENT_HOME`. The official
[Anthropic Python SDK](https://github.com/anthropics/anthropic-sdk-python) supplies
API transport and transient-error retries. Bracketed IPv6 entries such as `[::1]`
are hidden from `no_proxy` only while the client is constructed, because the SDK's
HTTP layer rejects them; the rest of the process environment is left unchanged.
Context-length errors follow the same
compact-then-rollover policy, with bounded recovery attempts. Truncated text
responses continue automatically; incomplete tool inputs are never executed.
When a response containing tool calls reaches `MAX_TOKENS`, the entire response is
discarded and the agent receives explicit instructions to retry with one small tool
call and incremental file edits. It retries automatically up to twice, without
raising the output budget or forcing compaction. Three consecutive truncated tool
responses stop the run with the active request preserved; completed tool calls reset
this retry count. The context threshold still controls compaction and rollover.

The core has four modules: `main.py` (CLI), `agent.py` (model loop),
`session.py` (project state and lifecycle), and `tools.py` (tools). `api.py` is a
thin HTTP adapter and `frontend/` is an independently built browser client.
FastAPI runs synchronous agent requests on its request thread pool; there is no
separate job queue, scheduler, team, worktree, memory runtime, or dynamic tool set.

## Verify

```sh
.venv/bin/python -m unittest discover -s tests -v
.venv/bin/python -m ruff check .
```

Tests use temporary repositories and deterministic model responses, require no
API key, and cover the documented lifecycle, repeated rollovers, project isolation,
restart and failure recovery, file boundaries, tool errors, and output persistence.
With dependencies installed, an additional test exercises the real SDK against
an in-memory HTTP transport; no live API request is made.

Run the complete browser-to-backend rollover demonstration with:

```sh
npm --prefix frontend run test:integration
```

This uses the existing Playwright browser installation, a real FastAPI backend,
the real agent and file tools, and a deterministic offline model. It recovers a
truncated summary, compacts and rolls over twice, and checks Session 3, retained
requirements, saved handoffs, execution details, and lifecycle phases in the UI.
The test starts isolated servers on ports 8765 and 5174 and cleans up its temporary
project. Its 4,000-token demo threshold does not change the normal 100,000-token
configuration. No API key or paid model calls are needed.
