# Long Code

Long Code is a local coding agent for working in existing project directories. It runs from
a terminal or a browser, uses an Anthropic-compatible Messages API, and keeps project state
across interrupted requests and long sessions.

- **One project at a time:** chat, file tools, and history stay scoped to the selected
  directory.
- **Recoverable sessions:** context compaction, handoffs, checkpoints, and `/continue` keep
  unfinished work available after an error or restart.
- **Local web workspace:** chat, tool activity, todos, project documents, history search,
  and a session timeline.
- **Predictable tools:** a fixed tool set for shell commands, file operations, todos, and
  history search.

The implementation follows the [V1 technical design](long-code%20text.en.md). The web
interface and CLI share the same project registry and session lifecycle.

## Requirements

- Python 3.10+, Bash, and Linux or macOS
- An Anthropic-compatible Messages API and a model ID for chat
- Node.js 20.19+ or 22.12+ for the web interface
- Git for repository inspection (optional for projects without a Git repository)

Project management and the automated tests work without an API key. Model calls require
`ANTHROPIC_API_KEY` and `MODEL_ID`.

## Quick start: CLI

Clone the repository and install the Python dependencies:

```sh
git clone https://github.com/lpppprmm/Long-Code.git long-code
cd long-code
python3 -m venv .venv
.venv/bin/python -m pip install -r requirements.txt
[ -f .env ] || cp .env.example .env
```

Set `ANTHROPIC_API_KEY` and `MODEL_ID` in the root `.env` file. Set `ANTHROPIC_BASE_URL` if
your provider uses a compatible endpoint. Then start the CLI:

```sh
.venv/bin/python main.py
```

`.venv/bin/python code.py` is an equivalent entry point. Environment variables take precedence
over values in `.env`.

The directory you register must already exist. For example:

```text
> new demo ~/code/existing-project
demo [1] > Find the failing parser test and fix it.
demo [1] > /current
demo [1] > /back
> list
> open demo
demo [1] > /exit
```

| Command | Purpose |
| --- | --- |
| `new <name> <path>` | Register and open an existing directory |
| `list`, `open <id>` | List or reopen registered projects |
| `/current`, `/back` | Show the selected project or return to project selection |
| `/continue` | Resume an unfinished request after an error, interruption, or restart |
| `/project <command>` | Run a project command without leaving the current project |
| `/help`, `/exit` | Show help or quit |

Quote names or paths containing spaces. IDs are derived from names; names without Latin
letters or digits receive a generated `project-...` ID. Plain text inside an open project
goes to the agent. Ctrl-C interrupts the current action and returns to the prompt; EOF
exits.

## Web workspace

Install the frontend dependencies in addition to the Python setup above:

```sh
npm --prefix frontend ci
```

Start the backend and frontend in **separate terminals**, both from the repository root:

```sh
# Terminal 1: API
.venv/bin/python -m uvicorn api:app --host 127.0.0.1 --port 8000
```

```sh
# Terminal 2: browser client
npm --prefix frontend run dev
```

Open [http://127.0.0.1:5173](http://127.0.0.1:5173). API documentation is available at
[http://127.0.0.1:8000/docs](http://127.0.0.1:8000/docs). Port 8000 serves API routes, so a
404 at its root URL is expected.

The workspace supports project creation and switching, chat, live tool activity, context
usage, todos, editable project requirements, history search, and archived session details.
Responses render as sanitized Markdown. Tool activity shows its target, status, elapsed
time, and an expandable result preview; compaction and handoff progress also appear while a
request runs.

Use **停止任务** to interrupt the next agent step or an active shell command. The unfinished
request remains available through **继续未完成的请求**. A model API call already in flight finishes
before cancellation takes effect. Closing the browser does not cancel an accepted run;
reopening it reconnects to the current workspace.

The backend allows one active run at a time and rejects project changes or another chat
request while busy. It has no login and can run shell commands with your user permissions,
so keep it bound to loopback and run only one backend worker.

To use a different API address, copy `frontend/.env.example` to `frontend/.env.local` and
set `VITE_API_URL` before starting or building the frontend. Set `FRONTEND_ORIGINS` on the
backend to allow the frontend origin; the default allows `http://localhost:5173` and
`http://127.0.0.1:5173`.

## Projects, sessions, and recovery

The default data home is `~/.simple-agent`. It contains `projects.json` and one directory
per registered project:

| Path inside `projects/<id>/` | Contents |
| --- | --- |
| `PROJECT.md` | Long-term goals, constraints, and decisions you can edit |
| `state.json` | Current session number, request, compaction count, and todos |
| `HANDOFF.md` and `handoffs/` | Latest and archived working handoffs |
| `checkpoints/` | Request, unfinished todos, recent commands, Git state, file hashes, and token totals |
| `transcripts/` | Append-only messages and compaction events |
| `tool-results/` | Full outputs that exceed the context preview limit |
| `web-events.jsonl` | Recent browser conversation events, separate from model context |

Keep stable project requirements in `PROJECT.md` and temporary progress in the handoff. A
fresh session inspects the project directory, Git state, relevant files, persistent
requirements, and any handoff before continuing.

When the context budget is reached, the first transition compacts the current session while
retaining recent complete tool exchanges. The next transition writes a factual handoff,
archives it, and starts a fresh session. Unfinished todos carry forward. On reopening, the
agent inspects the files again and compares them with its checkpoint; it does not replay old
transcripts. The browser's session timeline shows archived handoffs and paged transcript
records. `search_history` retrieves older details.

State and project file writes use atomic replacement. If a run stops after making changes,
inspect the files before continuing: an interrupted shell command may have left partial
results. A crash just after a model response may leave its request marked unfinished even
though the work was completed.

## Configuration

The root `.env` is loaded by the CLI and backend. Process environment variables override it.

| Variable | Default | Purpose |
| --- | --- | --- |
| `ANTHROPIC_API_KEY` | Required for chat | API credential |
| `MODEL_ID` | Required for chat | Model available at the configured endpoint |
| `ANTHROPIC_BASE_URL` | SDK default | Optional compatible Messages API endpoint |
| `SIMPLE_AGENT_HOME` | `~/.simple-agent` | Registry and project data |
| `CONTEXT_LIMIT_TOKENS` | `100000` | Estimated token budget before compaction; minimum 500 |
| `CONTEXT_LIMIT` | `400000` | Legacy character budget, used only when the token setting is absent |
| `MAX_TOOL_OUTPUT` | `10000` | Tool-result preview size; minimum 500 |
| `MAX_TOKENS` | `8000` | Maximum output tokens for a coding model call |
| `SUMMARY_MAX_TOKENS` | `4000` | Maximum output tokens for a summary or handoff attempt |
| `FRONTEND_ORIGINS` | Local Vite origins | Comma-separated browser origin allowlist |

The CLI's `--data-home PATH` overrides `SIMPLE_AGENT_HOME`. Restart the CLI or backend after
changing configuration.

The browser shows a four-characters-per-token estimate and, after a model response, the
measured input-token count. The runtime uses measured usage to project the next input size,
but the projection remains approximate. Set a smaller `CONTEXT_LIMIT_TOKENS` value, such as
`20000`, to observe compaction and rollover sooner.

Summaries begin with a small output budget and retry with shorter input or a larger output
budget when necessary, up to three attempts within `SUMMARY_MAX_TOKENS`. Failed attempts
preserve the active session and checkpoint. If retries are exhausted, raise that limit and
continue the unfinished request.

## Tools and boundaries

The agent's fixed tool set is `bash`, `read_file`, `write_file`, `edit_file`, `glob`,
`todo_write`, and `search_history`. File writes stay inside the selected project, including
checks against symlink escapes. The explicit `agent://PROJECT.md` path allows edits to
persistent requirements; other agent data is read-only through file tools.

`read_file` can inspect project data and saved outputs through `agent://...`. A read is
limited to 1,000 lines and 100,000 characters; history search scans at most 32 MiB per
request and stays within the selected project. Shell commands start in the project root, run
with a 120-second default timeout (600-second maximum), and stop if output exceeds 10 MiB.
**Shell execution is not a security sandbox.**

Only one CLI process or API worker can use a data home at a time. Git inspection identifies
whether a repository is inside the selected project or in an ancestor directory. The agent
is instructed to initialize repositories and create commits only when requested.

The Anthropic Python SDK handles API transport and transient-error retries. Context-length
errors trigger bounded compaction or rollover recovery. Truncated text responses continue
automatically; incomplete tool inputs are never executed. Repeated truncated tool responses
stop the run with its request preserved for `/continue`.

## Development and verification

Install the development dependencies, then run the Python tests and lint:

```sh
.venv/bin/python -m pip install -r requirements-dev.txt
.venv/bin/python -m unittest discover -s tests -v
.venv/bin/python -m ruff check .
```

Build and test the frontend:

```sh
npm --prefix frontend run build
cd frontend
npx playwright install chromium
npm test
npm run test:integration
```

The Python and browser tests use deterministic responses and make no paid model calls. The
browser tests mock API responses. The integration test starts a real FastAPI backend with an
offline model on ports 8765 and 5174, then exercises compaction, two rollovers, and recovery
in the UI. It uses an isolated 4,000-token demo budget; normal configuration is unchanged.

The core modules are `main.py` (CLI), `agent.py` (model loop), `session.py` (project state
and lifecycle), and `tools.py` (tools). `api.py` provides the HTTP adapter, and `frontend/`
is the browser client. The backend exposes project selection, state, chat and cancellation,
history search, and session list/detail routes under `/api/`.
