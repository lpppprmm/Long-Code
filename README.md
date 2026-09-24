<p align="center">
  <img src="docs/images/hero.svg" alt="Long Code — Keep building. Across sessions." width="1280">
</p>

<p align="center">
  A local coding agent for long-running work in your existing projects.<br>
  <strong>CLI and web workspace · Recoverable sessions · Durable task records</strong>
</p>

<p align="center">
  <a href="https://github.com/lpppprmm/Long-Code/actions/workflows/checks.yml"><img src="https://github.com/lpppprmm/Long-Code/actions/workflows/checks.yml/badge.svg" alt="Checks"></a>
  <img src="https://img.shields.io/badge/Python-3.10%2B-345b46?style=flat-square" alt="Python 3.10 or newer">
  <img src="https://img.shields.io/badge/UI-Vite%20%2B%20JavaScript-697f5c?style=flat-square" alt="Vite and JavaScript">
  <img src="https://img.shields.io/badge/API-Anthropic%20compatible-8b795c?style=flat-square" alt="Anthropic-compatible Messages API">
</p>

<p align="center">
  <a href="#quick-start">Quick start</a> ·
  <a href="#the-workspace">Workspace</a> ·
  <a href="#how-it-works">How it works</a> ·
  <a href="docs/reference.md">Reference</a> ·
  <a href="#development">Development</a>
</p>

---

Long development tasks outlive a single context window. Long Code keeps the project,
the active task, and the next action available as sessions change. It works in an
existing directory, calls an Anthropic-compatible Messages API, and stores its working
records in local files.

| Keep working | Keep your bearings | Keep the evidence |
| :--- | :--- | :--- |
| Compact once, then hand off to a fresh session when the budget is reached again. | Carry forward user constraints, decisions, failed attempts, and a concrete next action. | Revisit original requests, tool results, file fingerprints, and archived sessions. |

## The workspace

Chat with the agent, follow tool activity, and inspect context usage, todos, project
documents, and task records in one place.

![Long Code web workspace with a coding conversation, tool activity, and task context](docs/images/workspace.png)

<sub>The actual web interface, rendered with illustrative offline data. No model calls are used to produce these screenshots.</sub>

<details>
<summary><strong>A closer look at the task record</strong></summary>

The task view brings together the next action, its verification condition, effective
constraints, and source references. It reads the same durable record used during recovery.

![Task record showing the next action, verification, constraints, and evidence references](docs/images/task-record.png)

</details>

## Quick start

**You need:** Python 3.10+, Bash, and Linux or macOS. The web interface also requires
Node.js 20.19+ or 22.12+. Git is optional, but enables repository inspection.

**1. Install the backend**

```sh
git clone https://github.com/lpppprmm/Long-Code.git long-code
cd long-code
python3 -m venv .venv
.venv/bin/python -m pip install -r requirements.txt
[ -f .env ] || cp .env.example .env
```

**2. Configure your model**

Edit the root `.env` file:

```dotenv
ANTHROPIC_API_KEY=your-api-key
MODEL_ID=your-model-id

# Optional: use an Anthropic-compatible provider
# ANTHROPIC_BASE_URL=https://your-compatible-api.example
```

Project management and offline tests work without credentials. Chat requires a model and
API key. Exported environment variables take precedence over `.env`.

**3. Open the web workspace**

```sh
npm --prefix frontend ci
```

Start these in **two separate terminals**, from the repository root:

| Terminal 1 · Backend | Terminal 2 · Frontend |
| :--- | :--- |
| `.venv/bin/python -m uvicorn api:app --host 127.0.0.1 --port 8000` | `npm --prefix frontend run dev` |

Open **[localhost:5173](http://127.0.0.1:5173)** and connect an existing project directory.
The interface is in Chinese. API documentation is at
[localhost:8000/docs](http://127.0.0.1:8000/docs).

<details>
<summary><strong>Prefer the terminal?</strong></summary>

```sh
.venv/bin/python main.py
```

```text
> new demo ~/code/existing-project
demo [1] > Find the failing parser test and fix it.
demo [1] > /current
demo [1] > /back
> list
> open demo
demo [1] > /continue
```

Use `/continue` when a request is unfinished. Use `/help` for commands and `/exit`
to quit. Quote names or paths containing spaces; the directory must already exist.
`code.py` is an equivalent entry point.

[Full command reference →](docs/reference.md#cli-commands)

</details>

> **Local execution:** run one backend worker and keep it bound to loopback. The backend
> has no login, and shell commands run with your user permissions. Shell execution is
> not a security sandbox.

<a id="projects-sessions-and-recovery"></a>

## How it works

Each session has a bounded lifetime. At the first context threshold, Long Code compacts
the conversation while retaining recent complete tool exchanges. At the next threshold,
it writes a handoff and checkpoint, then starts a fresh session.

![Session lifecycle: work, compact once, save a handoff, and continue with persistent task records](docs/images/session-lifecycle.svg)

The next session inspects the project and checks its recorded state before continuing.
Task records are loaded directly from disk, so constraints and next actions remain
available even when a conversation summary leaves out details.

| Record | What it carries |
| :--- | :--- |
| `PROJECT.md` | Long-term project goals, requirements, and rules. |
| `tasks/<id>/task.json` | Current constraints, decisions, findings, failed attempts, and the next action. |
| `request-*.json` · `evidence-*.json` | Original user messages and observed tool results, with source IDs. |
| `HANDOFF.md` · `checkpoints/` | The previous session's handoff, task snapshot, and repository observations. |
| `transcripts/` | Archived messages and events, retrieved when needed. |

Records live under `~/.simple-agent/projects/<project-id>/` by default. Unfinished
requests keep the same task record across interruptions and session changes. A new
request after completion starts a new record; earlier records remain on disk.

**Evidence still needs interpretation.** A note marked `active` means it remains
relevant. A matching file hash or successful command exit does not prove that a feature
works. New sessions are instructed to recheck relevant evidence before relying on it.

[Storage layout and recovery details →](docs/reference.md#projects-sessions-and-recovery)

## Stop, resume, inspect

| When you need to… | In the web workspace | In the CLI |
| :--- | :--- | :--- |
| Stop the current run | **停止任务** | `Ctrl-C` |
| Resume unfinished work | **继续未完成的请求** | `/continue` |
| Inspect the next action and evidence | **任务记录** | Ask the agent to read the task record |
| Review earlier sessions | Click the session badge | Ask the agent to search project history |
| Switch projects | Select a project while idle | `/back`, then `open <id>` |

An in-flight model call finishes before web cancellation takes effect. Closing the browser
does not cancel an accepted run; reopening reconnects to the workspace. After interruption,
inspect existing changes before retrying an operation that may have partly completed.

## Configuration

The most useful settings in the root `.env`:

| Setting | Default | Purpose |
| :--- | :--- | :--- |
| `CONTEXT_LIMIT_TOKENS` | `100000` | Approximate context budget before a session transition. |
| `MAX_TOKENS` | `8000` | Output budget for each coding-model response. |
| `SUMMARY_MAX_TOKENS` | `4000` | Maximum output budget for summary and handoff attempts. |
| `SIMPLE_AGENT_HOME` | `~/.simple-agent` | Where project records and history are stored. |

Restart the CLI or backend after changes. For another API address, set `VITE_API_URL`
in `frontend/.env.local`; configure `FRONTEND_ORIGINS` on the backend to match.

[All settings and tool limits →](docs/reference.md#configuration)

## Development

```sh
# Python tests and lint
.venv/bin/python -m pip install -r requirements-dev.txt
.venv/bin/python -m unittest discover -s tests -v
.venv/bin/python -m ruff check .

# Frontend build and browser tests
npm --prefix frontend ci
npm --prefix frontend run build
cd frontend
npx playwright install chromium
npm test
npm run test:integration
```

Tests use deterministic offline responses and make no paid model calls. Coverage includes
interruption recovery, five successive handoffs with incomplete summaries, and a real
FastAPI/browser workflow through two session rollovers.

<details>
<summary><strong>Find your way around the code</strong></summary>

| Module | Responsibility |
| :--- | :--- |
| [`main.py`](main.py) | CLI, project selection, and application lifecycle. |
| [`agent.py`](agent.py) | Model loop, tool dispatch, usage tracking, and recovery. |
| [`session.py`](session.py) | Project registry, compaction, handoffs, and checkpoints. |
| [`task_state.py`](task_state.py) | Durable task notes, original requests, and evidence. |
| [`tools.py`](tools.py) | Shell, files, todos, history search, and task updates. |
| [`api.py`](api.py) | FastAPI adapter for the shared runtime. |
| [`frontend/`](frontend/) | Vite + JavaScript workspace and Playwright tests. |

</details>

---

[Technical design](long-code%20text.en.md) ·
[Operational reference](docs/reference.md) ·
[Image sources and reproduction](docs/images/README.md)
