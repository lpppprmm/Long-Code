import "./style.css";
import { marked } from "marked";
import DOMPurify from "dompurify";
import {
  createIcons,
  Terminal,
  Plus,
  Search,
  HardDrive,
  BookOpen,
  ArrowUpRight,
  PanelLeft,
  ChevronRight,
  Layers2,
  MessagesSquare,
  CodeXml,
  ScanSearch,
  Bug,
  FlaskConical,
  Play,
  FolderCode,
  ArrowUp,
  SlidersHorizontal,
  FileText,
  ArrowRightLeft,
  History,
  Sprout,
  X,
  ArrowRight,
  Folder,
  Check,
  Circle,
  LoaderCircle,
} from "lucide";
import { API_URL, request } from "./api.js";
import { translateSystemText } from "./locale.js";

const $ = (selector) => document.querySelector(selector);
const icons = {
  Terminal,
  Plus,
  Search,
  HardDrive,
  BookOpen,
  ArrowUpRight,
  PanelLeft,
  ChevronRight,
  Layers2,
  MessagesSquare,
  CodeXml,
  ScanSearch,
  Bug,
  FlaskConical,
  Play,
  FolderCode,
  ArrowUp,
  SlidersHorizontal,
  FileText,
  ArrowRightLeft,
  History,
  Sprout,
  X,
  ArrowRight,
  Folder,
  Check,
  Circle,
  LoaderCircle,
};
const drawIcons = () => createIcons({ icons, attrs: { "stroke-width": 1.7 } });
const markdown = (text) =>
  DOMPurify.sanitize(marked.parse(text || ""), {
    USE_PROFILES: { html: true },
  });
const escape = (value) => {
  const span = document.createElement("span");
  span.textContent = value ?? "";
  return span.innerHTML;
};
let state = { project: null, session: null, events: [], busy: false };
let projects = [];
let connected = false;
let pending = false;
let generation = 0;
let eventSignature = "";
let projectSignature = "";

function notice(message = "") {
  $("#notice").textContent = message;
  $("#notice").hidden = !message;
}

function renderProjects() {
  const filter = $("#project-filter").value.toLowerCase();
  const signature = JSON.stringify([projects, filter, state.project?.id, pending, state.busy, connected]);
  if (signature === projectSignature) return;
  projectSignature = signature;
  const matches = projects.filter((project) =>
    `${project.name} ${project.root}`.toLowerCase().includes(filter),
  );
  $("#project-count").textContent = projects.length;
  $("#projects").replaceChildren();
  if (!matches.length) {
    $("#projects").innerHTML =
      `<p class="sidebar-empty">${projects.length ? "没有匹配的项目。" : "从这里开始你的下一个项目。"}</p>`;
  }
  for (const project of matches) {
    const button = document.createElement("button");
    button.className = `project-item${project.id === state.project?.id ? " selected" : ""}`;
    button.setAttribute(
      "aria-current",
      project.id === state.project?.id ? "page" : "false",
    );
    button.innerHTML = `<span class="project-icon"><i data-lucide="folder"></i></span><span class="project-item-text"><strong>${escape(project.name)}</strong><small>${escape(project.root.split("/").filter(Boolean).slice(-2).join("/"))}</small></span><span class="project-selected-dot"></span>`;
    button.title = project.root;
    button.disabled = pending || state.busy || !connected;
    button.addEventListener("click", () => openProject(project.id));
    $("#projects").append(button);
  }
  drawIcons();
}

function renderEvents() {
  const signature = `${state.project?.id}:${state.events.map((event) => event.id).join(",")}`;
  if (signature === eventSignature) return;
  eventSignature = signature;
  const log = $("#conversation");
  const nearBottom = log.scrollHeight - log.scrollTop - log.clientHeight < 100;
  $("#messages").replaceChildren();
  for (const event of state.events) {
    const item = document.createElement("article");
    if (event.kind === "activity") {
      item.className = "activity-event";
      item.dataset.status = event.status || "completed";
      const duration = event.duration_ms == null ? "" : ` · ${(event.duration_ms / 1000).toFixed(2)} 秒`;
      item.innerHTML = `<span></span><div class="activity-body"><strong>${escape(translateSystemText(event.text))}</strong>${event.target ? `<code>${escape(event.target)}</code>` : ""}${event.output ? `<details><summary>结果预览</summary><pre>${escape(event.output)}</pre></details>` : ""}</div><small>${escape(translateSystemText(event.status || ""))} · 会话 ${event.session}${duration}</small>`;
    } else {
      item.className = `message message-${event.kind}`;
      const name =
        event.kind === "user"
          ? "你"
          : event.kind === "error"
            ? "运行中断"
            : "Long Code";
      const time = new Date(event.time).toLocaleTimeString("zh-CN", {
        hour: "2-digit",
        minute: "2-digit",
      });
      const text = event.kind === "error" ? translateSystemText(event.text) : event.text;
      item.innerHTML = `<div class="message-avatar">${event.kind === "user" ? "我" : '<i data-lucide="terminal"></i>'}</div><div class="message-body"><div class="message-meta"><strong>${name}</strong><time>${escape(time)}</time><span>会话 ${event.session}</span></div><div class="markdown">${event.kind === "user" || event.kind === "error" ? `<p class="plain-message">${escape(text)}</p>` : markdown(text)}</div></div>`;
    }
    $("#messages").append(item);
  }
  $("#welcome").hidden = state.events.length > 0;
  drawIcons();
  if (!state.events.length) log.scrollTop = 0;
  else if (nearBottom) log.scrollTop = log.scrollHeight;
}

function render() {
  const { project, session } = state;
  const busy = pending || state.busy;
  $("#connection").className = `connection ${connected ? "online" : "offline"}`;
  $("#connection").innerHTML =
    `<span></span> ${connected ? "已连接本地服务" : "API 不可用"}`;
  $("#breadcrumb-project").textContent = project?.name || "概览";
  $("#project-title").textContent =
    project?.name || "延续上下文，让开发走得更远。";
  $("#project-path").textContent =
    project?.root ||
    "选择一个项目，在每次专注的会话中持续推进开发。";
  $("#project-path").classList.toggle("repo-path", Boolean(project));
  $("#session-badge").hidden = !session;
  $("#session-number").textContent = String(session?.id || 1).padStart(2, "0");
  $("#context-session").innerHTML = session
    ? `${String(session.id).padStart(2, "0")}<small>${busy ? "正在推进" : "专注当前任务"}</small>`
    : "—<small>随时可以开始</small>";
  const phase = translateSystemText(state.phase || "");
  $("#run-status").textContent = busy ? phase || "处理中" : "就绪";
  $("#working-label").textContent = phase ? `${phase}…` : "助手正在处理…";
  $("#run-status").classList.toggle("running", busy);
  const percent = session
    ? Math.min(
        100,
        Math.round((100 * session.context_size) / session.context_limit),
      )
    : 0;
  $("#context-percent").textContent = `${percent}%`;
  $("#context-meter").style.width = `${percent}%`;
  $("#context-tokens").textContent = session
    ? `≈ ${session.context_tokens.toLocaleString("zh-CN")} / ${session.context_limit_tokens.toLocaleString("zh-CN")} 词元`
    : "为下一个想法留出全新空间。";
  $("#compact-count").innerHTML =
    `${session?.compact_count || 0} <span>/ 1</span>`;
  document
    .querySelectorAll(".lifecycle-step")
    .forEach((step, i) =>
      step.classList.toggle("active", i <= (session?.compact_count || 0)),
    );
  const todos = session?.todos || [];
  $("#todo-count").textContent = todos.filter(
    (todo) => todo.status !== "completed",
  ).length;
  $("#todos").innerHTML = todos.length
    ? todos
        .map(
          (todo) =>
            `<div class="todo ${todo.status}"><i data-lucide="${todo.status === "completed" ? "check" : todo.status === "in_progress" ? "loader-circle" : "circle"}"></i><span>${escape(todo.content)}</span></div>`,
        )
        .join("")
    : '<p class="context-empty">助手开始工作后，后续步骤会显示在这里。</p>';
  $("#new-project").disabled = busy;
  $("#message").disabled = !project || !connected || busy;
  $("#message").placeholder = !project
    ? "选择一个项目，开始对话…"
    : busy
      ? "助手正在处理你的请求…"
      : "你想构建、修复或探索什么？";
  $("#send").disabled =
    !project || !connected || busy || !$("#message").value.trim();
  $("#composer-project").textContent = project?.name || "尚未选择项目";
  $("#model-name").textContent = state.model || "尚未配置模型";
  $("#continue").hidden = !session?.active_request || busy;
  $("#continue").disabled = !connected;
  $("#working").hidden = !busy || !project;
  document.querySelectorAll(".document-button").forEach((button) => {
    button.disabled = !project;
  });
  renderEvents();
  renderProjects();
  drawIcons();
}

function applyState(next) {
  if (state.project?.id !== next.project?.id) {
    $("#message").value = "";
    $("#history-results").textContent =
      "搜索即可查找项目以往的决策。";
    document
      .querySelectorAll("#history-dialog[open], #document-dialog[open]")
      .forEach((dialog) => dialog.close());
  }
  state = next;
  connected = true;
  render();
}

async function openProject(id) {
  pending = true;
  generation++;
  notice();
  render();
  try {
    applyState(await request(`/projects/${encodeURIComponent(id)}/open`, {}));
  } catch (error) {
    notice(error.message);
  } finally {
    pending = false;
    generation++;
    render();
  }
}

async function send(message) {
  if (!state.project || pending || state.busy) return;
  const projectId = state.project.id;
  const previousEventIds = new Set(state.events.map((event) => event.id));
  pending = true;
  generation++;
  notice();
  if (message !== null) $("#message").value = "";
  render();
  try {
    applyState(await request("/chat", { project_id: projectId, message }));
  } catch (error) {
    notice(error.message);
    try {
      applyState(await request("/state"));
    } catch {
      connected = false;
    }
    const accepted = state.events.some((event) =>
      event.kind === "user" && event.text === message && !previousEventIds.has(event.id),
    );
    if (message !== null && !accepted && state.project?.id === projectId)
      $("#message").value = message;
  } finally {
    pending = false;
    generation++;
    render();
    if (!$("#message").disabled) $("#message").focus();
  }
}

async function poll() {
  const current = generation;
  try {
    const [next, list] = await Promise.all([
      request("/state"),
      request("/projects"),
    ]);
    if (current === generation) {
      if (!connected) notice();
      projects = list;
      applyState(next);
    }
  } catch {
    if (current === generation) {
      connected = false;
      notice(
        "本地 API 暂时不可用，请确认服务已启动。此页面会自动重新连接。",
      );
      render();
    }
  } finally {
    window.setTimeout(poll, pending || state.busy ? 1000 : 3000);
  }
}

$("#api-docs").href = `${API_URL}/docs`;
$("#new-project").addEventListener("click", () => {
  $("#project-error").hidden = true;
  $("#project-dialog").showModal();
});
$("#project-filter").addEventListener("input", renderProjects);
$("#message").addEventListener("input", () => {
  $("#send").disabled =
    !$("#message").value.trim() || pending || state.busy || !connected;
});
$("#message").addEventListener("keydown", (event) => {
  if (event.key === "Enter" && !event.shiftKey && !event.isComposing) {
    event.preventDefault();
    if (!$("#send").disabled) $("#chat-form").requestSubmit();
  }
});
$("#chat-form").addEventListener("submit", (event) => {
  event.preventDefault();
  const text = $("#message").value.trim();
  if (text) send(text);
});
$("#continue").addEventListener("click", () => send(null));
document
  .querySelectorAll("[data-close]")
  .forEach((button) =>
    button.addEventListener("click", () => button.closest("dialog").close()),
  );
document.querySelectorAll("dialog").forEach((dialog) =>
  dialog.addEventListener("click", (event) => {
    if (event.target === dialog) {
      const rect = dialog.getBoundingClientRect();
      if (
        event.clientX < rect.left ||
        event.clientX > rect.right ||
        event.clientY < rect.top ||
        event.clientY > rect.bottom
      )
        dialog.close();
    }
  }),
);
document.querySelectorAll("[data-prompt]").forEach((button) =>
  button.addEventListener("click", () => {
    if (!state.project) {
      $("#project-dialog").showModal();
      return;
    }
    if (pending || state.busy) return;
    $("#message").value = button.dataset.prompt;
    $("#message").dispatchEvent(new Event("input"));
    $("#message").focus();
  }),
);
document.addEventListener("keydown", (event) => {
  if (
    event.key.toLowerCase() === "n" &&
    !event.metaKey &&
    !event.ctrlKey &&
    !event.altKey &&
    !["INPUT", "TEXTAREA"].includes(event.target.tagName) &&
    !document.querySelector("dialog[open]") &&
    !pending &&
    !state.busy
  ) {
    event.preventDefault();
    $("#new-project").click();
  }
});
$("#project-form").addEventListener("submit", async (event) => {
  event.preventDefault();
  const button = $('#project-form button[type="submit"]');
  button.disabled = true;
  pending = true;
  generation++;
  $("#project-error").hidden = true;
  render();
  try {
    const body = {
      name: $("#project-name").value.trim(),
      root: $("#project-root").value.trim(),
    };
    const next = await request("/projects", body);
    projects = await request("/projects");
    applyState(next);
    $("#project-dialog").close();
    $("#project-form").reset();
    notice();
  } catch (error) {
    $("#project-error").textContent = error.message;
    $("#project-error").hidden = false;
  } finally {
    button.disabled = false;
    pending = false;
    generation++;
    render();
  }
});
document.querySelectorAll("[data-document]").forEach((button) =>
  button.addEventListener("click", () => {
    const name = button.dataset.document;
    $("#document-title").textContent =
      name === "project" ? "项目概述" : "会话交接";
    $("#document-content").innerHTML = markdown(
      state.documents?.[name] ||
        "暂无交接记录。切换到下一次会话时，助手会自动生成记录。",
    );
    $("#document-dialog").showModal();
  }),
);
$("#history-button").addEventListener("click", () =>
  $("#history-dialog").showModal(),
);
$("#history-form").addEventListener("submit", async (event) => {
  event.preventDefault();
  const projectId = state.project?.id;
  if (!projectId) return;
  const button = $("#history-form button");
  button.disabled = true;
  $("#history-results").textContent = "正在搜索项目历史…";
  try {
    const result = await request(
      `/projects/${encodeURIComponent(projectId)}/history?query=${encodeURIComponent($("#history-query").value)}`,
    );
    if (state.project?.id === projectId)
      $("#history-results").textContent = translateSystemText(result.text);
  } catch (error) {
    if (state.project?.id === projectId)
      $("#history-results").textContent = error.message;
  } finally {
    button.disabled = false;
  }
});

drawIcons();
poll();
