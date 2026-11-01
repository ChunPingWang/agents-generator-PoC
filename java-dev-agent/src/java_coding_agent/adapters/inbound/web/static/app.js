/* Java Coding Agent 聊天介面：POST /api/chat 以 SSE 串流回覆；Run 進度以 /api/runs/{id}/events 串流。 */
const $ = (s) => document.querySelector(s);
const messages = $("#messages"), input = $("#input"), send = $("#send");
let conversationId = localStorage.getItem("conversationId") || newId();
const watching = new Set();

function newId() { const id = Math.random().toString(36).slice(2, 10); localStorage.setItem("conversationId", id); return id; }
function addMsg(role, text) { const d = document.createElement("div"); d.className = `msg ${role}`; d.textContent = text; messages.appendChild(d); messages.scrollTop = messages.scrollHeight; return d; }

async function loadSettings() {
  const [llm, repo, prompt, health] = await Promise.all([
    fetch("/api/settings/llm").then(r => r.json()), fetch("/api/settings/repo").then(r => r.json()),
    fetch("/api/system-prompt").then(r => r.json()), fetch("/api/health").then(r => r.json())]);
  $("#settings").innerHTML = `
    <dt>LLM</dt><dd>${llm.provider} / ${llm.model}</dd>
    <dt>Base URL</dt><dd>${llm.base_url}</dd>
    <dt>Temp / Ctx</dt><dd>${llm.temperature} / ${llm.context_length}</dd>
    <dt>Repo</dt><dd>${repo.full_name} (${repo.base_branch})</dd>
    <dt>System Prompt</dt><dd>v${prompt.version}${prompt.is_default ? "（預設）" : "：" + prompt.note}</dd>
    <dt>Langfuse</dt><dd>${health.langfuse ? "已啟用" : "未啟用"}</dd>`;
  $("#status").textContent = "就緒";
}

async function loadRuns() {
  const runs = await fetch("/api/runs").then(r => r.json());
  $("#runs").innerHTML = "";
  for (const run of runs) { renderRun(run); if (!["succeeded", "failed"].includes(run.stage)) watchRun(run.id); }
}

function renderRun(run) {
  let el = document.getElementById(`run-${run.id}`);
  if (!el) { el = $("#runTpl").content.firstElementChild.cloneNode(true); el.id = `run-${run.id}`; $("#runs").prepend(el); }
  const stage = el.querySelector(".stage"); stage.textContent = run.stage; stage.className = `stage ${run.stage}`;
  el.querySelector(".rid").textContent = run.id;
  el.querySelector(".req").textContent = run.requirement;
  el.querySelector(".tasks").innerHTML = (run.tasks || []).map(t => `<li class="${t.done ? "done" : ""}">${t.id} ${t.title}</li>`).join("");
  el.querySelector(".events").innerHTML = (run.events || []).slice(-30).map(e => `<li class="${e.level}">[${e.stage}] ${e.message}</li>`).join("");
  el.querySelector(".pr").innerHTML = run.pull_request ? `PR：<a href="${run.pull_request}" target="_blank">${run.pull_request}</a>` : (run.error ? `<span class="muted">${run.error}</span>` : "");
}

function watchRun(runId) {
  if (watching.has(runId)) return; watching.add(runId);
  const es = new EventSource(`/api/runs/${runId}/events`);
  es.addEventListener("run_event", async () => { const run = await fetch(`/api/runs/${runId}`).then(r => r.json()); renderRun(run); });
  es.addEventListener("done", (ev) => { renderRun(JSON.parse(ev.data)); es.close(); watching.delete(runId); });
  es.onerror = () => { es.close(); watching.delete(runId); };
}

async function sendMessage(text) {
  addMsg("user", text); send.disabled = true;
  const bubble = addMsg("assistant", "");
  const res = await fetch("/api/chat", { method: "POST", headers: { "Content-Type": "application/json" }, body: JSON.stringify({ conversation_id: conversationId, message: text }) });
  const reader = res.body.getReader(); const dec = new TextDecoder(); let buf = "";
  while (true) {
    const { value, done } = await reader.read(); if (done) break;
    buf += dec.decode(value, { stream: true }).replace(/\r\n/g, "\n");
    let idx; while ((idx = buf.indexOf("\n\n")) >= 0) { handleSse(buf.slice(0, idx), bubble); buf = buf.slice(idx + 2); }
  }
  if (!bubble.textContent) bubble.remove();
  send.disabled = false; loadSettings();
}

function handleSse(block, bubble) {
  let event = "message", data = "";
  for (const line of block.split("\n")) { if (line.startsWith("event:")) event = line.slice(6).trim(); else if (line.startsWith("data:")) data += line.slice(5).trim(); }
  if (!data) return; const payload = JSON.parse(data);
  if (event === "text") { bubble.textContent += payload.content; messages.scrollTop = messages.scrollHeight; }
  else if (event === "tool") addMsg("tool", `⚙ ${payload.content}`);
  else if (event === "run_started" && payload.data) { addMsg("tool", `▶ 已啟動 SDD Run ${payload.data.run_id}`); loadRuns().then(() => watchRun(payload.data.run_id)); }
  else if (event === "error") addMsg("tool", `✖ ${payload.content}`);
}

$("#composer").addEventListener("submit", (e) => { e.preventDefault(); const t = input.value.trim(); if (!t) return; input.value = ""; sendMessage(t); });
input.addEventListener("keydown", (e) => { if (e.key === "Enter" && (e.metaKey || e.ctrlKey)) $("#composer").requestSubmit(); });
$("#newConv").addEventListener("click", () => { conversationId = newId(); messages.innerHTML = ""; });
loadSettings(); loadRuns();
