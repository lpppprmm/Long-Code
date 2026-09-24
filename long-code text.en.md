# Simple Coding Agent Technical Design Document

This document describes the original V1 baseline. The current implementation also
includes a web workspace and independent task records with evidence-backed notes;
see [README.md](README.md#projects-sessions-and-recovery) for the current recovery workflow.

## 1. Project Overview

### 1.1 Project Name

Working title:

**Simple Coding Agent**

The name can be changed later.

------

## 1.2 Project Goals

Build a lightweight Coding Agent that can run for a long time.

The core goal is not to provide as many Agent features as possible, but to solve one key problem in long-running development tasks:

> When the AI context keeps growing, avoid the gradual distortion of information caused by repeatedly compressing the same context.

The traditional approach is usually:

```text
Original context
    ↓
First summary
    ↓
Continue working
    ↓
Second summary
    ↓
Continue working
    ↓
Third summary
```

After running for a long time, this easily turns into:

```text
Original information
→ Summary
→ Summary of the summary
→ Summary of the summary of the summary
```

This inevitably causes information loss.

This project adopts:

```text
Session 1
    ↓
First compaction
    ↓
Continue working
    ↓
Second compaction needed
    ↓
Generate handoff document
    ↓
End Session 1
    ↓
Create Session 2
    ↓
Re-inspect the project
    ↓
Read handoff document
    ↓
Continue working
```

In other words:

> **Allow one soft compaction; when a second compaction is needed, do not compact again — open a brand-new Session instead.**

A new Session does not inherit the full messages of the old Session.

It does so through:

- The project's actual files
- Git state
- PROJECT.md
- HANDOFF.md
- Historical transcripts, retrieved when necessary

and thereby re-establishes its understanding of the project.

------

# 2. Design Principles

The whole project follows the principles below.

## 2.1 Simplicity First

Code should be as concise as possible.

Do not add complex abstractions in advance for requirements that may exist in the future.

Prefer:

```text
Simple functions
Simple dataclasses
Simple JSON
Markdown
Local files
```

Rather than:

```text
Database
Message bus
Complex dependency injection
MCP
Background scheduler
Distributed Agent
Vector database
Complex state machine
```

------

## 2.2 The Filesystem Is the Real State

The Agent's understanding of the project may be wrong.

HANDOFF.md may also be wrong.

Therefore the order of authority should be:

```text
Project code / Git / Tests
        ↓
PROJECT.md
        ↓
HANDOFF.md
        ↓
Historical Transcript
        ↓
Current model reasoning
```

That is:

> The project's real files always outrank summaries left behind by the AI.

------

## 2.3 Project and Session Must Be Separated

The core hierarchy in the system:

```text
Application
    ↓
Project
    ↓
Session
    ↓
Messages
```

A Project is long-lived.

A Session is temporary context.

Messages belong to exactly one Session.

It must never happen that:

```text
A single global messages
simultaneously carries Project A
Project B
Project C
```

------

## 2.4 Handoff Is Not Long-Term Memory

HANDOFF.md is only responsible for:

> The short-term work handoff from the previous Session to the next Session.

Long-term project rules go into:

```text
PROJECT.md
```

Historical raw records go into:

```text
transcripts/
```

So the three have clear responsibilities:

```text
PROJECT.md
= long-term project constraints

HANDOFF.md
= most recent working state

transcript
= complete historical archive
```

------

# 3. Project Scope

## 3.1 Must Be Implemented in Version 1

Version 1 includes:

- CLI
- Multi-project management
- Project Registry
- Independent project state
- Session management
- LLM Agent Loop
- File reading
- File writing
- File editing
- Glob
- Bash
- Todo
- Context size estimation
- Tool output truncation
- First soft compact
- Second occurrence triggers session rollover
- HANDOFF.md
- PROJECT.md
- Transcript
- Simple history search
- Automatic bootstrap of a new Session
- State isolation between projects

------

## 3.2 Explicitly Not Implemented in Version 1

Delete, and do not keep:

- MCP
- MCP server
- MCP dynamic tools
- Background Tasks
- `run_in_background`
- Cron
- Scheduler
- Durable Cron Jobs
- Automatic background event loop

Version 1 is also advised not to implement:

- Web frontend
- Database
- SQLite
- Vector database
- Embedding
- Docker Sandbox
- Multi-Agent Team
- Persistent Teammate
- Worktree
- Plan approval protocol
- Cross-Agent message bus
- Automatic long-term memory

If the core architecture later proves effective, add these gradually.

------

# 4. Session Lifecycle

A session may compact its history once. When the context reaches the limit again,
the agent writes a handoff and starts a new session. The new session inspects the
project and continues the active request using that handoff.

------

# 5. Overall Architecture

Overall structure:

```text
                 ┌─────────────────┐
                 │   Application   │
                 └────────┬────────┘
                          │
                 Project Registry
                          │
              ┌───────────┴───────────┐
              │                       │
         Project A                Project B
              │                       │
       ┌──────┴──────┐          ┌─────┴─────┐
       │             │          │           │
    Repo Path     Agent Data  Repo Path   Agent Data
       │             │
       │       ┌─────┴──────────────┐
       │       │                    │
       │    PROJECT.md          state.json
       │       │
       │    HANDOFF.md
       │
       ├── source files
       ├── tests
       └── git
```

Sessions live inside a Project:

```text
Project
   │
   ├── Session 1
   │      ↓
   │    compact
   │      ↓
   │    rollover
   │
   ├── Session 2
   │      ↓
   │    compact
   │      ↓
   │    rollover
   │
   └── Session 3
```

------

# 6. Recommended Directory Structure

The Agent's own data:

```text
~/.simple-agent/
│
├── projects.json
│
└── projects/
    │
    ├── project_a/
    │   ├── PROJECT.md
    │   ├── HANDOFF.md
    │   ├── state.json
    │   │
    │   ├── handoffs/
    │   │   ├── session_001.md
    │   │   ├── session_002.md
    │   │   └── ...
    │   │
    │   └── transcripts/
    │       ├── session_001.jsonl
    │       ├── session_002.jsonl
    │       └── ...
    │
    └── project_b/
        ├── PROJECT.md
        ├── HANDOFF.md
        ├── state.json
        ├── handoffs/
        └── transcripts/
```

The real project code can live at any path:

```text
~/code/project-a/
~/code/project-b/
~/work/company-project/
```

The two are linked through the Registry.

------

# 7. Project Registry

File:

```text
~/.simple-agent/projects.json
```

Example:

```json
{
  "project_a": {
    "name": "Project A",
    "root": "/Users/user/code/project-a"
  },
  "project_b": {
    "name": "Project B",
    "root": "/Users/user/code/project-b"
  }
}
```

The Registry only stores:

- Project ID
- Display name
- Actual code path

Do not store Session messages.

------

# 8. Project Data Model

Suggested:

```python
@dataclass
class Project:
    id: str
    name: str
    root: Path
    data_dir: Path
```

Derived paths:

```python
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
```

All Agent state must be derived from:

```text
current_project
```

Avoid the reappearance of large numbers of globals:

```python
WORKDIR
TRANSCRIPT_DIR
MEMORY_DIR
TASKS_DIR
```

------

# 9. Session Data Model

Suggested, and keep it simple:

```python
@dataclass
class Session:
    id: int
    compact_count: int = 0
    messages: list = field(default_factory=list)
    active_request: str = ""
```

Where:

### id

The Session sequence number within the current project.

For example:

```text
1
2
3
...
```

### compact_count

How many formal history compactions have happened in the current Session.

Normal state:

```text
0
```

After the first compact:

```text
1
```

When the second one is reached:

```text
do not set it to 2
roll over directly
```

### messages

Contains only the context of the current Session.

### active_request

The user's current core request that has not yet been completed.

------

# 10. state.json

Stored independently for each Project.

For example:

```json
{
  "current_session": 8,
  "compact_count": 0,
  "active_request": "Continue finishing the session rollover feature",
  "last_handoff": "handoffs/session_007.md",
  "created_at": "2026-09-21T10:00:00",
  "updated_at": "2026-09-21T19:30:00"
}
```

What is stored here is program state.

It should not be freely generated by the LLM.

It should be read and written by the Python program.

------

# 11. PROJECT.md

PROJECT.md holds the long-term stable information of the project.

For example:

```markdown
# Project

## Name

Simple Coding Agent

## Goal

Implement a lightweight Coding Agent.

## Requirements

- Keep the code as concise as possible
- Do not use MCP
- Do not use cron
- Do not support background tasks
- Support multiple independent Projects
- Each Project has its own independent history
- Perform a Session rollover when a second compact is needed

## Architecture

Project
→ Session
→ Messages

## Rules

The project's real files outrank the Handoff.

A new Session must first re-inspect the project.
```

------

## 11.1 Purpose of PROJECT.md

Used to store:

- Long-term user requirements
- Project goals
- Technical constraints
- Architecture principles
- Explicit prohibitions
- Long-term important design decisions

Do not store:

- Temporary errors
- Which commands were recently executed
- Which function is currently being worked on
- Temporary TODOs

These belong to HANDOFF.

------

# 12. HANDOFF.md

HANDOFF.md holds the handoff state of the most recent Session.

A fixed format is recommended.

```markdown
# Session Handoff

## Active Goal

What the user ultimately wants to accomplish.

## User Constraints

- Constraint 1
- Constraint 2

## Completed

- Completed items

## Current State

What stage the project is currently in.

## Changed Files

- src/a.py
- src/b.py

## Important Decisions

- Decision A
- Decision B

## Problems / Risks

- Current problems
- Unresolved risks

## Validation

- Which tests were run
- Test results

## Next Actions

1. Next step
2. Second step

## Active Request

The user's currently unfinished request.
```

------

# 13. Design Principles of the Handoff

The Handoff should be:

- Concise
- Structured
- Factual
- Verifiable
- Free of long natural-language passages
- Not a repetition of the whole chat
- Not a copy of large blocks of source code

The Handoff is not:

```text
Conversation Summary
```

It is closer to:

```text
Developer Shift Handoff
```

------

# 14. Saving Handoff History

On every rollover:

```text
HANDOFF.md
```

keeps the latest version.

At the same time, copy it to:

```text
handoffs/session_007.md
```

This way:

```text
HANDOFF.md
```

always represents the latest working state.

While:

```text
handoffs/
```

is used for historical traceability.

------

# 15. Transcript

Every Session saves one complete transcript.

For example:

```text
transcripts/
├── session_001.jsonl
├── session_002.jsonl
├── session_003.jsonl
└── session_004.jsonl
```

For the format, JSONL is still recommended:

```json
{"role":"user","content":"..."}
{"role":"assistant","content":[...]}
{"role":"user","content":[...]}
```

------

# 16. The Role of the Transcript

The Transcript is:

> Cold history.

A normal new Session does not load the entire transcript.

It must not do:

```text
new session
↓
load all transcripts
↓
fill up the context again
```

Otherwise Session rollover would lose its meaning.

The Transcript is searched only when:

- The Handoff information is insufficient
- A certain historical decision needs to be looked up
- The user explicitly asks to query history
- The Agent judges that key context is missing

Only in these cases is a search performed.

------

# 17. History Search

Version 1 does not use embeddings.

It does not use a vector database.

The implementation is simple:

```python
search_history(query)
```

The search scope must be limited to the current Project:

```text
current_project/handoffs/
current_project/transcripts/
```

It must never search other Projects by default.

Version 1 can use:

- String matching
- grep
- Simple keyword scoring

For example:

```text
query:
"why was sqlite chosen for the database"
```

Returns only a small number of relevant fragments.

------

# 18. Project Isolation

Project A:

```text
repo:
~/code/project-a

agent data:
~/.simple-agent/projects/project_a
```

Project B:

```text
repo:
~/code/project-b

agent data:
~/.simple-agent/projects/project_b
```

The two have:

- Independent PROJECT
- Independent HANDOFF
- Independent state
- Independent transcript
- Independent messages
- Independent session counter

------

# 19. Project Switching

For example:

```text
simple-agent > open project_a
```

The system:

```text
current_project = Project A
load state A
load project metadata A
create/load current session A
```

Then:

```text
simple-agent > open project_b
```

It must first do:

```python
messages = []
```

and only then load Project B.

It must not inherit:

- Project A messages
- Project A Handoff
- Project A transcript
- Project A TODO
- Project A Session state

------

# 20. Creating a New Project

Command:

```text
new project-b ~/code/project-b
```

Flow:

```text
Validate the code directory
    ↓
Generate project id
    ↓
Write projects.json
    ↓
Create agent data dir
    ↓
Generate PROJECT.md
    ↓
Generate state.json
    ↓
current_session = 1
    ↓
messages = []
    ↓
Scan the project
    ↓
Enter Session 1
```

Session 1 has no preceding HANDOFF.

------

# 21. Reopening an Existing Project

For example:

```text
open project-a
```

Execute:

```text
Read the Registry
↓
Find project root
↓
Read state.json
↓
Read PROJECT.md
↓
Check whether HANDOFF.md exists
↓
Re-inspect the repo
↓
Start the current Session
```

------

# 22. New Session Bootstrap

This is one of the most critical flows in the whole architecture.

A new Session should not simply:

```text
Read HANDOFF
→ Continue
```

It should:

```text
Start a brand-new Session
        ↓
Inspect the project directory
        ↓
git status
        ↓
git diff --stat
        ↓
Inspect key files
        ↓
Read PROJECT.md
        ↓
Read HANDOFF.md
        ↓
Compare the Handoff with the actual state
        ↓
search_history if necessary
        ↓
Continue the Active Request
```

------

# 23. Bootstrap Prompt

Recommended:

```text
You are starting a fresh work session for an existing coding project.

Before continuing:

1. Inspect the repository and current filesystem state.
2. Check git status and relevant diffs.
3. Read PROJECT.md for persistent project requirements.
4. Read HANDOFF.md for the previous session's working state.
5. Treat the actual filesystem, git state, and tests as authoritative.
6. Treat HANDOFF.md as useful but potentially incomplete.
7. Search historical handoffs/transcripts only when necessary.
8. Continue the active request.
```

Key idea:

> Do not directly tell the new model "what the world is".

Instead, tell it:

> Confirm for yourself what the world is.

------

# 24. Context Management

Context is divided into three layers.

## Layer One: Tool Output Control

Tool output is handled first.

For example:

```text
pytest
npm install
git diff
grep
cat of a large file
```

These easily produce enormous numbers of tokens.

Suggested:

```python
MAX_TOOL_OUTPUT = 10_000
```

If it is exceeded:

```python
save_tool_output(...)
return preview + saved_path
```

For example:

```text
[first 3000 chars]

[Full output saved at:
.agent/tool-results/tool_123.txt]
```

Do not let large amounts of mechanical output fill up the context.

------

# 25. Soft Compact

When the current Session exceeds the threshold for the first time:

```python
if needs_compact() and session.compact_count == 0:
    soft_compact()
```

Behavior:

```text
Save a snapshot of the current transcript
↓
Generate a compact summary
↓
Replace the early messages
↓
Keep the necessary recent messages
↓
compact_count = 1
```

At this point it still belongs to:

```text
The same Session
```

------

# 26. Session Rollover

If compact_count is already:

```text
1
```

and the context threshold is reached again:

```python
if needs_compact() and session.compact_count >= 1:
    rollover_session()
```

Do not:

```text
compact_count = 2
```

Instead, end that Session immediately.

------

# 27. The Complete Rollover Flow

```text
Second compaction detected as needed
        ↓
Stop calling the current Session further
        ↓
Save the complete transcript
        ↓
Generate a structured HANDOFF
        ↓
Save handoffs/session_N.md
        ↓
Update HANDOFF.md
        ↓
Update state.json
        ↓
current_session += 1
        ↓
compact_count = 0
        ↓
messages = []
        ↓
bootstrap_new_session()
        ↓
Continue the active_request
```

------

# 28. Session Rollover Core Pseudocode

```python
def prepare_context(project, session):
    trim_tool_outputs(session.messages)

    if context_size(session.messages) < CONTEXT_LIMIT:
        return

    if session.compact_count == 0:
        session.messages = soft_compact(session.messages)
        session.compact_count = 1
        save_state(project, session)
        return

    rollover_session(project, session)
```

------

# 29. rollover_session Pseudocode

```python
def rollover_session(project, session):
    save_transcript(project, session)

    handoff = generate_handoff(
        project=project,
        session=session
    )

    save_handoff(project, session.id, handoff)

    session.id += 1
    session.compact_count = 0
    session.messages = []

    save_state(project, session)

    bootstrap_session(project, session)
```

------

# 30. Agent Loop

The goal is to simplify the Agent Loop to its most essential form.

```python
def agent_loop(project, session):
    while True:
        prepare_context(project, session)

        response = call_llm(
            project=project,
            messages=session.messages,
            tools=TOOLS,
        )

        session.messages.append(
            assistant_message(response)
        )

        if not has_tool_calls(response):
            save_session_state()
            return

        results = execute_tools(
            project,
            response.tool_calls
        )

        session.messages.append(
            tool_results_message(results)
        )
```

At its core it only does:

```text
context
↓
LLM
↓
tools
↓
results
↓
LLM
```

------

# 31. Version 1 Tool Set

It is suggested to keep only:

```text
bash
read_file
write_file
edit_file
glob
todo_write
search_history
```

compact can be triggered automatically by the system.

If you want the model to be able to request compact on its own as well, you may keep:

```text
compact
```

but it is not required.

------

# 32. Bash

Synchronous execution only.

Delete:

```text
run_in_background
```

Interface:

```python
def run_bash(project, command):
    ...
```

cwd:

```python
project.root
```

------

# 33. File Tools

All tools must be bound to the current Project.

For example:

```python
def read_file(project, path):
    fp = safe_path(project.root, path)
```

`safe_path()`:

```python
def safe_path(root: Path, path: str) -> Path:
    base = root.resolve()
    resolved = (base / path).resolve()

    if not resolved.is_relative_to(base):
        raise ValueError("Path escapes project")

    return resolved
```

This prevents ordinary file tools from using:

```text
../../other-project
```

to escape the project boundary.

------

# 34. Bash Safety Boundary

It needs to be made explicit:

```python
cwd=project.root
```

only provides logical working-directory isolation.

It is not a security sandbox.

For example, a shell can in theory still do:

```bash
cat /Users/user/code/other-project/file.py
```

Version 1 accepts this limitation.

Therefore the goal of version 1 is:

> To prevent the normal Agent workflow from confusing projects.

Rather than:

> To defend against malicious shell commands.

If real isolation is needed in the future:

```text
Docker
Sandbox
Container
VM
```

as a phase-two capability.

------

# 35. Todo

The Todo can simply be kept in the current Session.

For example:

```python
session.todos
```

Or in:

```text
state.json
```

For version 1, if TODOs need to survive a rollover, it is suggested to write the currently unfinished tasks into the HANDOFF.

No complex Task DAG is needed.

------

# 36. Delete the Task System

The current version contains:

```text
create_task
update_task
claim_task
complete_task
dependencies
ownership
worktree binding
```

Version 1 deletes all of them.

Reason:

The new goal is only:

> Continuous development by a single Agent.

A task scheduling platform is not needed.

------

# 37. Delete Worktree

Version 1 deletes Worktree.

One Project is bound to one Repo Root.

The Agent works directly inside that root.

If multi-Agent parallelism is restored in the future, reintroduce worktrees then.

------

# 38. Delete the Team System

Delete:

- MessageBus
- teammates
- spawn_teammate
- shutdown protocol
- plan protocol
- team inbox
- async team event

This significantly reduces:

- threading
- locks
- protocol state
- assignment state

------

# 39. Delete the Memory Runtime

Do not load:

```text
s09_memory
```

Do not use:

```text
extract_memories
load_memories
consolidate_memories
```

Project state is already explicitly defined by:

```text
PROJECT.md
HANDOFF.md
transcript
filesystem
```

These are responsible for it.

This is what makes a new Session genuinely clean.

------

# 40. Delete MCP

Delete completely:

```text
MCPClient
mcp_clients
connect_mcp
assemble dynamic MCP tool pool
MCP_HOST_POLICY
mock MCP server
```

The tool list becomes a static list.

------

# 41. Delete Background Task

Delete completely:

```text
background_tasks
background_results
background_lock
start_background_task
collect_background_results
has_pending_background
run_in_background
```

The Bash tool only allows:

```text
synchronous execution
```

------

# 42. Delete Cron

Delete:

```text
CronJob
scheduled_jobs
cron_queue
cron scheduler thread
schedule_cron
list_crons
cancel_cron
scheduled prompt injection
```

The CLI does not need to run a background service.

------

# 43. Delete async_event_loop

Without:

- Cron
- Background Task
- Teammate Event

there is no need for a thread that constantly listens for events.

The CLI can become standard synchronous code:

```text
User input
↓
Agent Loop
↓
Return result
↓
Wait for the next user input
```

------

# 44. CLI Design

Version 1 can support only:

```text
new
open
list
current
exit
```

plus chatting directly after entering a Project.

------

# 45. CLI Example

Startup:

```text
$ python main.py

Simple Coding Agent

Projects:
1. simple-agent
2. website
3. test-api

> open simple-agent
```

Output:

```text
Project: Simple Coding Agent
Root: ~/code/simple-agent
Session: 8

Inspecting project...
Reading project state...
Reading previous handoff...

Ready.
```

Then:

```text
agent > continue finishing session rollover
```

------

# 46. Creating a Project

```text
> new test-app /Users/me/code/test-app
```

The system:

```text
Created project: test-app
Root: /Users/me/code/test-app
Session: 1
```

It then enters the project.

------

# 47. Project Switching

```text
agent > /project open website
```

Or, more simply for version 1:

exit the current project shell:

```text
agent > /back

> open website
```

When switching, it must:

```python
current_session.messages.clear()
```

and then load the other project.

------

# 48. Is a Frontend Needed

Version 1:

> No.

Because the real problem is the Agent Runtime, not the UI.

The CLI is enough to validate:

- Multi-project isolation
- Long-running Sessions
- Compact
- Rollover
- Handoff
- Quality of recovery in a new Session

------

# 49. Later Web UI

Develop it only after the core mechanisms are stable.

A possible layout:

```text
┌──────────────┬──────────────────────────┬─────────────┐
│ Projects     │ Conversation             │ Session     │
│              │                          │             │
│ Project A    │ User                     │ #8          │
│ Project B    │ Agent                    │ Compact: 0  │
│ Project C    │                          │ Context 42% │
│              │                          │             │
│ + New        │                          │ Git status  │
└──────────────┴──────────────────────────┴─────────────┘
```

But the frontend can only be a client.

The core Runtime does not depend on the frontend.

------

# 50. Recommended Code Structure

Version 1:

```text
simple-agent/
│
├── main.py
├── agent.py
├── project.py
├── session.py
├── tools.py
├── history.py
└── config.py
```

If you want it even leaner:

```text
simple-agent/
│
├── main.py
├── agent.py
├── session.py
└── tools.py
```

It is recommended to start with the latter.

------

# 51. main.py

Responsibilities:

- CLI
- Project list
- new project
- open project
- User input
- Calling agent_loop

It must not contain:

- Context algorithms
- Tool implementations
- Handoff generation logic

------

# 52. agent.py

Responsibilities:

- system prompt
- LLM API
- Agent loop
- tool call dispatch
- response parsing

------

# 53. session.py

Responsibilities:

- Project data
- Session data
- Registry
- state.json
- Context size
- Compact
- Transcript
- Handoff
- Rollover
- Bootstrap
- History search

This is the core state module of the entire project.

------

# 54. tools.py

Responsibilities:

```text
bash
read_file
write_file
edit_file
glob
todo
```

All file tools take:

```python
project
```

as their execution boundary.

------

# 55. config.py

If it exists separately, it only holds:

```python
CONTEXT_LIMIT
MAX_TOOL_OUTPUT
DEFAULT_MAX_TOKENS
MODEL
DATA_HOME
```

No complex configuration framework.

------

# 56. Estimating Context Size

Version 1 can keep using an approximate character count.

For example:

```python
def estimate_context(messages):
    return len(json.dumps(messages, default=str))
```

There is no need to introduce a complex tokenizer just for an exact token count.

The core requirement is:

> To sense in advance that the context has grown too large.

------

# 57. Compact Threshold

For example:

```python
CONTEXT_LIMIT = 50_000
```

It can remain configurable.

It does not matter that the real model has a larger window.

The Agent should proactively keep the context lean, rather than waiting for the API to return an error.

------

# 58. Reactive Recovery

A simple safeguard should still be kept:

If the API returns:

```text
context_length_exceeded
```

the behavior is:

If:

```text
compact_count == 0
```

perform a soft compact.

Otherwise:

```text
rollover
```

Do not build another complex reactive compact algorithm.

------

# 59. Handoff Generation Prompt

Suggested:

```text
Create a structured factual handoff for the next coding-agent session.

Do not continue the task.
Do not speculate.
Do not include unnecessary conversation.

Return these sections:

Active Goal
User Constraints
Completed
Current State
Changed Files
Important Decisions
Problems / Risks
Validation
Next Actions
Active Request

The next session will independently inspect the repository,
so preserve only information that is useful for continuation.
```

------

# 60. The Handoff Must Be Generated in the Old Session

Reason:

The old Session has the complete context.

Therefore:

```text
Session N
↓
generate handoff
↓
save handoff
↓
destroy messages
↓
Session N+1
```

It must not wait until messages have been cleared before generating it.

------

# 61. A New Session Does Not Inherit the Old Messages

This is a strict rule.

After rollover:

```python
session.messages = []
```

Then bootstrap only adds:

- The new Session bootstrap prompt
- The necessary material of the current Project
- The current active request

Old messages are not restored.

------

# 62. Conflict Between Handoff and Repo

For example, the HANDOFF says:

```text
src/auth.py is complete
```

but after inspection the new Session finds:

```text
src/auth.py does not exist
```

It must be concluded that:

```text
The Repo is correct
The Handoff is wrong
```

and the task state must then be re-assessed.

------

# 63. Project Search Order

Recommended for a new Session:

```text
1. PROJECT.md
2. state.json
3. git status
4. git diff --stat
5. relevant source files
6. HANDOFF.md
7. historical search if needed
```

The Handoff may also be read earlier to locate relevant files, but it must not be treated as a source of facts.

------

# 64. History Search Isolation

Function signature:

```python
def search_history(project: Project, query: str):
    ...
```

Rather than:

```python
def search_history(query):
```

Because the Project parameter enforces the isolation boundary.

------

# 65. Deleting a Project

Version 1 may skip implementing real deletion for now.

If it is implemented:

```text
remove project-a
```

By default it only removes from:

```text
projects.json
```

the Registry entry.

It must not automatically delete the real repo.

Agent Data should also preferably be kept, unless the user explicitly chooses:

```text
--delete-history
```

------

# 66. Project Completion

A project being "completed" does not mean deletion.

It only needs:

```json
{
  "status": "completed"
}
```

or version 1 may not even need a status.

Afterwards the user can still:

```text
open project-a
```

and continue making changes.

------

# 67. Multiple Projects Coexisting

For example:

```text
project-a
session 12

project-b
session 3

project-c
session 1
```

Each Project maintains its own independent Session numbering.

No global Session ID is needed.

------

# 68. Error Recovery

When the program exits unexpectedly:

- state.json already exists
- The transcript can be written continuously per turn, or written on exit
- The Handoff may not be the latest

On re-entry:

```text
Check the Repo
Read PROJECT
Read state
Read HANDOFF
```

Because the Repo is authoritative, even a missing most recent Handoff does not make recovery impossible.

------

# 69. Transcript Writing Strategy

There can be two approaches.

Recommended for version 1:

Write it in full at every Session rollover.

A more reliable version:

Append JSONL on every turn.

It is ultimately recommended to use:

```text
append on every turn
```

This way a process crash does not lose the entire Session.

------

# 70. Atomic Writes

For these important files:

```text
projects.json
state.json
HANDOFF.md
```

Suggested:

```text
write temporary
↓
os.replace
```

This avoids a half-written JSON file caused by an abnormal process termination.

------

# 71. Concurrency

Version 1 only allows:

```text
One CLI
One active Project
One active Session
One Agent loop
```

Therefore a great deal can be deleted:

```text
threading.Lock
RLock
Condition
daemon thread
```

Keep the whole program as synchronous as possible.

------

# 72. System Prompt

It is suggested to keep it very short:

```text
You are a coding agent.

Inspect the project before making assumptions.
Use tools to complete the user's request.
Keep changes focused.
Test changes when appropriate.
Treat project files and git state as authoritative.
```

Do not stuff in dozens of complex protocols.

Project-specific rules come from:

```text
PROJECT.md
```

------

# 73. Separate Session Prompt and Project Prompt

System:

```text
The Agent's basic behavior
```

PROJECT:

```text
This project's permanent requirements
```

HANDOFF:

```text
The previous Session's state
```

User:

```text
The current request
```

Avoid stuffing everything into the system prompt.

------

# 74. Authority Information Model

Final definition:

```text
Level 1:
Filesystem / Git / Tests

Level 2:
PROJECT.md

Level 3:
state.json

Level 4:
HANDOFF.md

Level 5:
Historical transcript

Level 6:
Current model assumptions
```

------

# 75. Recommended Development Order

## Phase 1: Basic CLI

Complete:

```text
projects.json
new
list
open
exit
```

------

## Phase 2: Basic Agent

Implement:

```text
LLM call
messages
tool use
tool result
bash/read/write/edit/glob
```

------

## Phase 3: Project Isolation

Verify:

```text
Project A does not read Project B history
Project B tool cwd is correct
file safe_path is effective
```

------

## Phase 4: Transcript

Implement:

```text
session_xxx.jsonl
```

------

## Phase 5: Soft Compact

Implement:

```text
context estimate
first compact
compact_count = 1
```

------

## Phase 6: Handoff

Implement the structured:

```text
HANDOFF.md
handoffs/session_N.md
```

------

## Phase 7: Rollover

Implement:

```text
second compact
→ save transcript
→ handoff
→ clear messages
→ session + 1
```

------

## Phase 8: Bootstrap

New Session:

```text
inspect repo
PROJECT
HANDOFF
active request
```

------

## Phase 9: History Search

Implement the lightweight:

```text
search_history
```

------

## Phase 10: Test Long-Running Operation

Artificially produce:

```text
Session 1
→ compact
→ rollover
→ Session 2
→ compact
→ rollover
→ Session 3
```

Check whether the information stays stable.

------

# 76. Key Scenarios That Must Be Tested

### Scenario A: New Project

```text
new A
→ Session 1
→ no Handoff
```

It should start normally.

------

### Scenario B: First Compact

```text
Session 1
compact_count = 0
↓
Over the limit
↓
soft compact
↓
compact_count = 1
```

The Session ID does not change.

------

### Scenario C: Second Compact

```text
Session 1
compact_count = 1
↓
Over the limit again
↓
Generate Handoff
↓
Session 2
compact_count = 0
messages = []
```

------

### Scenario D: Project Switching

```text
Project A Session 8
↓
open Project B
↓
Project B Session 2
```

Project A's context must not appear.

------

### Scenario E: Program Restart

Close the program.

Reopen:

```text
open Project A
```

It should restore:

- Project root
- Session ID
- Active request
- Handoff

------

### Scenario F: Wrong Handoff

Artificially modify the HANDOFF:

```text
File A already exists
```

while in reality it does not exist.

The new Session should discover the real state by inspecting the Repo.

------

### Scenario G: History Query

In Session 10, query:

```text
Why was a certain design chosen early on?
```

It can search old transcripts.

But it will not load all transcripts.

------

# 77. Version 1 Success Criteria

When version 1 is complete, it must satisfy:

1. Multiple Projects can be created.
2. Each Project points to an independent Repo path.
3. Session messages are not shared between Projects.
4. Handoffs are not shared between Projects.
5. Transcript search scope is not shared between Projects.
6. The Agent can read and write code normally.
7. The Agent can run synchronous Bash.
8. Large Tool Output does not directly fill up the Context.
9. The current Session compacts when it exceeds the Context limit for the first time.
10. It rolls over when it exceeds the Context limit for the second time.
11. Rollover generates a Handoff.
12. Old messages are cleared after rollover.
13. The new Session re-inspects the Repo.
14. The new Session reads PROJECT.md.
15. The new Session reads the most recent Handoff.
16. The new Session can continue the previous task.
17. MCP does not exist.
18. Cron does not exist.
19. Background Task does not exist.
20. Memory Runtime does not exist.
21. It can run without any background thread.

------

# 78. Version 1 Non-Goals

Do not re-add any of the following for the sake of "feature completeness":

```text
Complex task graph
Multi-Agent
MCP
Cron
Background worker
Database
Web UI
Vector DB
Embedding
Docker
Plugin system
```

None of these are necessary to validate the core idea.

------

# 79. Possible Future Evolution

Once the core approach is validated, the following can be added gradually:

### V2

```text
Web UI
A better Project Manager
Session browsing
Handoff browsing
Context usage UI
```

### V3

```text
SQLite
Full-text search
Higher-quality history retrieval
```

### V4

```text
Sandbox
Docker
Permission policies
```

### V5

```text
Optional multi-agent
Optional worktree
```

But these should all be built on top of the stable:

```text
Project
→ Session
→ Handoff
→ Rollover
```

model.

------

# 80. The Most Essential Data Flow

In the end, the core only requires understanding this one diagram:

```text
                   USER
                    │
                    ▼
              Current Project
                    │
                    ▼
                 Session
                    │
                    ▼
              Agent Messages
                    │
             ┌──────┴───────┐
             │              │
        Context OK      Context Full
             │              │
             │       compact_count == 0?
             │          /          \
             │        YES          NO
             │         │            │
             │    Soft Compact      │
             │         │            │
             │         └──────┐     │
             │                │     │
             │                │  Handoff
             │                │     │
             │                │ Transcript
             │                │     │
             │                │ Session + 1
             │                │     │
             │                │ messages=[]
             │                │     │
             │                └─ Bootstrap
             │                      │
             └──────────────────────┘
                    │
                    ▼
                 LLM Call
                    │
                    ▼
                Tool Calls
                    │
                    ▼
                 Project
```

------

# 81. The Core Idea of the Project

The most important thing about this project is not the "compression algorithm".

What really needs to be validated is:

> Whether a long-running Coding Agent should proactively limit the lifespan of a single Session.

That is, it does not pursue:

```text
A single model context that continues forever.
```

Instead it pursues:

```text
A Project that exists for a long time.

Multiple short-lived Sessions
serve that Project in turn.
```

Every Session:

```text
Receives the task
→ Works
→ Compacts once if necessary
→ Works
→ Hands off
→ Ends
```

The next Session:

```text
Re-understands the project
→ Verifies the facts
→ Takes over the task
→ Continues
```

Therefore what truly persists is:

```text
Project
```

Not:

```text
Conversation
```

------

# 82. Final Architecture Principles

The whole project can be summarized in five sentences:

**First, a Project is the long-term lifecycle unit.**

**Second, a Session is a context container with a limited lifespan.**

**Third, a Session undergoes at most one formal history compaction.**

**Fourth, when compaction is needed again, Session Rollover is carried out through a Handoff.**

**Fifth, a new Session does not inherit the old Messages; instead it re-inspects the project facts and then continues the work.**

The final relationship:

```text
Project
    │
    ├── Persistent Requirements
    │       PROJECT.md
    │
    ├── Current Working State
    │       HANDOFF.md
    │
    ├── Machine State
    │       state.json
    │
    ├── Historical Records
    │       transcripts/
    │       handoffs/
    │
    └── Sessions
            │
            ├── Session 1
            ├── Session 2
            ├── Session 3
            └── ...
```

This is the complete technical design baseline for version 1 of the Simple Coding Agent.
