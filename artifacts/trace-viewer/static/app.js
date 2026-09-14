// Cartwheel trace viewer - read-only. Nothing here writes anything anywhere.

const el = (tag, className, text) => {
  const node = document.createElement(tag);
  if (className) node.className = className;
  if (text !== undefined && text !== null) node.textContent = text;
  return node;
};

// Missing values are shown, never hidden: null and undefined are real findings.
const show = (value) => {
  if (value === null) return { text: "null", missing: true };
  if (value === undefined) return { text: "(not recorded)", missing: true };
  if (value === "") return { text: '"" (empty string)', missing: true };
  return { text: String(value), missing: false };
};

const valueNode = (value) => {
  const { text, missing } = show(value);
  return el("span", missing ? "null" : "", text);
};

const pretty = (value) => {
  if (value === null) return "null";
  if (value === undefined) return "(not recorded)";
  if (typeof value === "string") {
    try {
      return JSON.stringify(JSON.parse(value), null, 2);
    } catch {
      return value;
    }
  }
  try {
    return JSON.stringify(value, null, 2);
  } catch {
    return String(value);
  }
};

const escapeHTML = (s) =>
  s.replace(/[&<>]/g, (c) => ({ "&": "&amp;", "<": "&lt;", ">": "&gt;" }[c]));

// Small JSON colouriser. Input is escaped first, so setting innerHTML is safe.
const highlight = (text) =>
  escapeHTML(text).replace(
    /("(?:\\u[a-zA-Z0-9]{4}|\\[^u]|[^\\"])*"\s*:?|\b(?:true|false)\b|\bnull\b|-?\d+(?:\.\d*)?(?:[eE][+-]?\d+)?)/g,
    (match) => {
      let cls = "j-num";
      if (match.startsWith("&quot;") || match.startsWith('"')) {
        cls = /:\s*$/.test(match) ? "j-key" : "j-str";
      } else if (match === "true" || match === "false") {
        cls = "j-bool";
      } else if (match === "null") {
        cls = "j-null";
      }
      return `<span class="${cls}">${match}</span>`;
    },
  );

const codeBlock = (value, className) => {
  const node = el("pre", className);
  node.innerHTML = highlight(pretty(value));
  return node;
};

// Each tool keeps the same hue everywhere, so a run has a recognisable shape.
const hueFor = (name) => {
  let hash = 0;
  for (const ch of String(name || "?")) hash = (hash * 31 + ch.charCodeAt(0)) % 360;
  return hash;
};

const time = (iso) => {
  if (!iso) return "(no timestamp)";
  const d = new Date(iso);
  return Number.isNaN(d.getTime()) ? String(iso) : d.toLocaleString();
};

const clock = (iso) => {
  if (!iso) return "--:--:--";
  const d = new Date(iso);
  return Number.isNaN(d.getTime()) ? String(iso) : d.toLocaleTimeString();
};

const seconds = (value) =>
  typeof value === "number" ? `${value.toFixed(2)}s` : show(value).text;

let traces = [];
let selectedId = null;

// ---------------------------------------------------------------- list

function renderList() {
  const onlyFlagged = document.getElementById("only-flagged").checked;
  const list = document.getElementById("traces");
  list.replaceChildren();

  const rows = onlyFlagged ? traces.filter((t) => t.flags.length) : traces;
  document.getElementById("count").textContent = `${rows.length} / ${traces.length}`;

  if (!rows.length) {
    const li = el("li");
    li.append(el("p", "empty", onlyFlagged
      ? "Nothing flagged in these traces."
      : "No traces yet. Send a request through the agent server first."));
    list.append(li);
    return;
  }

  for (const trace of rows) {
    const li = el("li");
    const role = String(trace.user_role || "unknown").toLowerCase();
    const button = el("button", "row");
    button.type = "button";
    button.dataset.role = role;
    button.dataset.flagged = String(trace.flags.length > 0);
    button.setAttribute("aria-current", String(trace.id === selectedId));

    const top = el("div", "row-top");
    const who = el("span", "who");
    who.append(el("span", `avatar ${role}`, role.slice(0, 1).toUpperCase() || "?"));
    who.append(el("span", "", trace.user_role
      ? `${trace.user_role} ${show(trace.user_id).text}`
      : "(no role recorded)"));
    top.append(who, el("span", "when", clock(trace.timestamp)));

    const meta = el("div", "row-meta");
    for (const name of trace.tool_order) {
      const chip = el("span", "chip tool", name || "(unnamed tool)");
      chip.style.setProperty("--h", hueFor(name));
      meta.append(chip);
    }
    if (!trace.tool_order.length) meta.append(el("span", "chip", "no tools"));
    for (const flag of trace.flags) {
      meta.append(el("span", `chip ${flag.kind === "missing" ? "warn" : "bad"}`, flag.label));
    }
    if (typeof trace.latency === "number") {
      meta.append(el("span", "chip", seconds(trace.latency)));
    }

    button.append(top, el("div", "q", trace.question || "(no question recorded)"), meta);
    button.addEventListener("click", () => selectTrace(trace.id));
    li.append(button);
    list.append(li);
  }
}

// ---------------------------------------------------------------- detail

function factList(summary) {
  const facts = el("dl", "facts");
  const entries = [
    ["role", summary.user_role, false],
    ["user", summary.user_id, false],
    ["prompt version", summary.prompt_version, true],
    ["model", summary.model, false],
    ["model calls", summary.model_calls, false],
    ["latency", seconds(summary.latency), false],
    ["observations", summary.observation_count, false],
    ["session", summary.session_id, false],
    ["scenario", summary.scenario_id, false],
    ["when", time(summary.timestamp), false],
    ["trace id", summary.id, false],
  ];
  for (const [label, value, accent] of entries) {
    const box = el("div", `fact${accent ? " accent" : ""}`);
    box.append(el("dt", "", label));
    const dd = el("dd");
    dd.append(valueNode(value));
    box.append(dd);
    facts.append(box);
  }
  return facts;
}

// The run drawn to scale: where the time actually went.
function stripNode(observations) {
  const timed = observations.filter((o) => o.startTime && o.endTime);
  if (timed.length < 2) return null;

  const starts = timed.map((o) => new Date(o.startTime).getTime());
  const ends = timed.map((o) => new Date(o.endTime).getTime());
  const t0 = Math.min(...starts);
  const t1 = Math.max(...ends);
  const span = t1 - t0;
  if (!Number.isFinite(span) || span <= 0) return null;

  const wrap = el("div", "strip");
  const head = el("div", "strip-head");
  head.append(el("span", "", "the run, drawn to scale"), el("span", "", `${(span / 1000).toFixed(2)}s total`));
  wrap.append(head);

  const bar = el("div", "strip-bar");
  for (const o of timed) {
    const start = new Date(o.startTime).getTime();
    const end = new Date(o.endTime).getTime();
    const seg = el("div", `seg ${String(o.type || "span").toLowerCase()}`);
    seg.style.left = `${((start - t0) / span) * 100}%`;
    seg.style.width = `${Math.max(((end - start) / span) * 100, 0.6)}%`;
    seg.title = `${o.name || o.type}: ${((end - start) / 1000).toFixed(2)}s`;
    bar.append(seg);
  }
  wrap.append(bar);

  const key = el("div", "strip-key");
  for (const [cls, label] of [["generation", "model"], ["tool", "tool"], ["agent", "agent"], ["span", "wrapper"]]) {
    const item = el("span");
    item.append(el("i", cls), document.createTextNode(label));
    key.append(item);
  }
  wrap.append(key);
  return wrap;
}

function flagList(flags) {
  const wrap = el("div", "flags");
  for (const flag of flags) {
    const box = el("div", `flag ${flag.kind === "missing" ? "warn" : ""}`);
    const body = el("div");
    const head = el("div");
    head.append(el("strong", "", flag.label));
    if (flag.where) head.append(" ", el("span", "where", `on ${flag.where}`));
    body.append(head);
    if (flag.detail) body.append(el("div", "detail", flag.detail));
    box.append(body);
    wrap.append(box);
  }
  return wrap;
}

function toolCallNode(part) {
  const obs = part.observation;
  const failed = Boolean(obs && obs.error);
  const denied = Boolean(
    obs && obs.attributes && String(obs.attributes["cartwheel.permission_denied"]) === "true"
  );

  const details = el("details", `tool-call${failed || denied ? " failed" : ""}`);
  if (failed || denied) details.open = true;

  const name = part.name || (obs && obs.name) || "(unnamed tool)";
  const summary = el("summary");
  const nameChip = el("span", "tool-name", name);
  summary.append(nameChip);
  if (denied) summary.append(el("span", "chip bad", "permission denied"));
  else if (failed) summary.append(el("span", "chip bad", obs.error));
  else if (obs) summary.append(el("span", "chip good", "ok"));
  if (obs && typeof obs.latency === "number") summary.append(el("span", "chip", seconds(obs.latency)));
  summary.append(el("span", "chip", part.call_id ? `id ${part.call_id}` : "no call id"));
  details.append(summary);

  const body = el("div", "tool-body");
  const section = (label, value) => {
    const kv = el("div", "kv");
    kv.append(el("span", "kv-label", label));
    kv.append(codeBlock(value));
    return kv;
  };

  body.append(section("arguments (from the model)", part.arguments));

  if (obs) {
    body.append(section("arguments (as recorded on the tool)", obs.arguments));
    body.append(section("result", obs.result));

    const when = el("div", "kv");
    when.append(el("span", "kv-label", "timing and ids"));
    when.append(codeBlock({
      start: obs.start ?? null,
      end: obs.end ?? null,
      latency_seconds: obs.latency ?? null,
      observation_id: obs.observation_id ?? null,
      call_id: part.call_id ?? null,
    }));
    body.append(when);

    body.append(section("attributes", obs.attributes));
  } else {
    const missing = el("div", "kv");
    missing.append(el("span", "kv-label", "result"));
    missing.append(el("p", "null", "No tool observation matched this call - the result was not recorded."));
    body.append(missing);
  }

  details.append(body);
  return details;
}

function messageNode(message) {
  const role = String(message.role || "unknown").toLowerCase();
  const box = el("div", `msg ${role}`);
  box.append(el("div", "msg-role", role));

  const parts = message.parts || [];
  if (!parts.length) box.append(el("p", "null", "(no parts recorded)"));

  for (const part of parts) {
    if (part.type === "text") {
      const text = part.content;
      if (text === null || text === undefined || text === "") {
        box.append(el("p", "null", show(text).text));
      } else {
        box.append(el("div", "text", String(text)));
      }
    } else if (part.type === "thinking") {
      const d = el("details", "think");
      d.append(el("summary", "", "reasoning (never shown to the customer)"));
      d.append(el("div", "thinking", part.thinking ?? show(part.thinking).text));
      box.append(d);
    } else if (part.type === "tool_call") {
      box.append(toolCallNode(part));
    } else if (["tool_result", "tool_response", "tool_call_response"].includes(part.type)) {
      const kv = el("div", "kv");
      const id = part.id || (part.response && part.response.id);
      kv.append(el("span", "kv-label", `${part.type}${id ? ` · id ${id}` : ""}`));
      kv.append(codeBlock(part.content ?? part.response ?? part));
      box.append(kv);
    } else {
      const kv = el("div", "kv");
      kv.append(el("span", "kv-label", part.type || "unrecognised part"));
      kv.append(codeBlock(part));
      box.append(kv);
    }
  }
  return box;
}

function observationNode(observation) {
  const d = el("details", "obs");
  d.dataset.type = observation.type || "SPAN";
  const summary = el("summary");
  summary.append(
    el("span", "obs-type", observation.type || "?"),
    el("span", "obs-name", observation.name || "(unnamed)"),
    el("span", "chip", clock(observation.startTime)),
  );
  if (typeof observation.latency === "number") {
    summary.append(el("span", "chip", seconds(observation.latency)));
  }
  if (observation.level && observation.level !== "DEFAULT") {
    summary.append(el("span", "chip bad", String(observation.level).toLowerCase()));
  }
  d.append(summary);
  const body = el("div", "obs-body");
  body.append(codeBlock(observation, "raw"));
  d.append(body);
  return d;
}

function renderDetail(data) {
  const root = document.getElementById("detail");
  root.replaceChildren();

  root.append(factList(data.summary));
  const strip = stripNode(data.observations);
  if (strip) root.append(strip);
  if (data.summary.flags.length) root.append(flagList(data.summary.flags));

  const tabs = el("div", "tabs");
  const conversationTab = el("button", "tab", "Conversation");
  const rawTab = el("button", "tab", `Raw observations (${data.observations.length})`);
  conversationTab.type = rawTab.type = "button";
  tabs.append(conversationTab, rawTab);
  root.append(tabs);

  const panel = el("div");
  root.append(panel);

  const showConversation = () => {
    conversationTab.setAttribute("aria-selected", "true");
    rawTab.setAttribute("aria-selected", "false");
    panel.replaceChildren();
    if (!data.conversation.length) {
      panel.append(el("p", "null", "No messages were recorded on this trace."));
      return;
    }
    for (const message of data.conversation) panel.append(messageNode(message));
  };

  const showRaw = () => {
    conversationTab.setAttribute("aria-selected", "false");
    rawTab.setAttribute("aria-selected", "true");
    panel.replaceChildren();
    for (const observation of data.observations) panel.append(observationNode(observation));
    const trace = el("details", "obs");
    trace.append(el("summary", "", "trace record (without observations)"));
    const body = el("div", "obs-body");
    body.append(codeBlock(data.trace, "raw"));
    trace.append(body);
    panel.append(trace);
  };

  conversationTab.addEventListener("click", showConversation);
  rawTab.addEventListener("click", showRaw);
  showConversation();
}

// ---------------------------------------------------------------- loading

async function selectTrace(id) {
  selectedId = id;
  renderList();
  const root = document.getElementById("detail");
  root.replaceChildren(el("p", "empty", "Loading…"));
  try {
    const response = await fetch(`/api/traces/${encodeURIComponent(id)}`);
    if (!response.ok) throw new Error((await response.json()).detail || response.statusText);
    renderDetail(await response.json());
  } catch (error) {
    root.replaceChildren(el("div", "flag", String(error.message || error)));
  }
}

async function load() {
  const list = document.getElementById("traces");
  list.replaceChildren(el("li", "", "Loading…"));
  try {
    const response = await fetch("/api/traces?limit=50");
    const payload = await response.json();
    if (!response.ok) throw new Error(payload.detail || response.statusText);
    traces = payload.traces;
    document.getElementById("source").textContent = payload.host;
    renderList();
    if (traces.length && !selectedId) selectTrace(traces[0].id);
  } catch (error) {
    list.replaceChildren();
    const li = el("li");
    li.append(el("div", "flag", String(error.message || error)));
    list.append(li);
  }
}

document.getElementById("refresh").addEventListener("click", load);
document.getElementById("only-flagged").addEventListener("change", renderList);
load();
