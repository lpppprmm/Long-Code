# Architecture

[← Back to Long Code](../README.md)

Long Code has one Python runtime and two interfaces: the CLI and the browser workspace.
The backend lives in the `long_code` package. The frontend, tests, and documentation have
their own top-level directories.

## Entry points

Run these commands from the repository root, using the existing virtual environment:

| Interface | Command |
| --- | --- |
| CLI | `.venv/bin/python -m long_code` |
| API | `.venv/bin/python -m uvicorn long_code.api:app --host 127.0.0.1 --port 8000` |
| Frontend | `npm --prefix frontend run dev` |
| Python tests | `.venv/bin/python -m unittest discover -s tests -v` |
| Browser integration | `npm --prefix frontend run test:integration` |

The earlier root scripts `main.py` and `code.py` are replaced by the module command.
The ASGI import path changes from `api:app` to `long_code.api:app`. There are no duplicate
root entry points to maintain, and the package no longer shadows Python's standard `code`
module. Update any local launch scripts that still use the old paths.

`config.py` loads the repository's root `.env` for both interfaces, independently of the
working directory, with exported variables taking precedence. Importing the package does
not open a project, acquire a data-home lock, or create a model client. The API creates its
workspace when the ASGI application starts.

## Module responsibilities

| Area | Modules | Responsibility |
| --- | --- | --- |
| Interfaces | `cli.py`, `api.py` | Terminal commands, HTTP validation, web events, and cancellation. |
| Application | `application.py` | Own the selected project, session, registry, and agent for either interface. |
| Execution | `agent.py`, `tools.py` | Call the model, dispatch tools, capture results, and handle interruption. |
| Recovery | `session.py`, `task_state.py` | Bound context, archive handoffs, and preserve requests, notes, and evidence. |
| Projects | `projects.py`, `models.py` | Register directories, lease the data home, and define project/session records. |
| History | `history.py` | Append transcripts, search archived material, and page session timelines. |
| Repository | `repository.py` | Inspect files and Git state; compare recovery checkpoints. |
| Foundation | `storage.py`, `config.py` | Atomic persistence, bounded reads, path checks, and shared configuration. |

CLI parsing stays in `cli.py`; the API uses `application.py` directly. The runtime modules
do not import either interface. Persistence helpers have no dependency on session or task
logic, so `session.py` can load durable task records without a circular import.

## Where changes belong

- Add model-loop behavior in `agent.py` and tool behavior in `tools.py`.
- Change compaction or rollover in `session.py`; change durable evidence in `task_state.py`.
- Change project registration and data-home locking in `projects.py`.
- Add API routes in `api.py` and browser behavior in `frontend/src/`.
- Keep Python regressions in `tests/` and browser scenarios in `frontend/tests/`.
- Put reusable integration fixtures in `tests/support/`; the offline rollover server runs
  as `python -m tests.support.rollover_server` from the repository root.
- Keep operational instructions in `reference.md` and image assets in `images/`.

The original proposal is archived in [design-v1.md](design-v1.md). It describes the V1
baseline; this document and the README describe the current source layout.

## Persistent data

Source organization is separate from user data. Project IDs, registered directories,
`state.json`, task records, handoffs, and transcripts retain their existing formats and
default location under `~/.simple-agent/`. The directory reorganization requires no data
migration. See the [storage reference](reference.md#projects-sessions-and-recovery).
