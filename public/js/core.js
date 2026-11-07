// Общие помощники интерфейса. Без сборки и зависимостей: модуль можно встроить
// как веб-ресурс Dynamics 365 — адрес API задаётся атрибутом data-api у <body>.

export const API = (document.body.dataset.api || "").replace(/\/$/, "");

export async function api(path, body) {
  const opt = body === undefined ? {} : { method: "POST", headers: { "Content-Type": "application/json" }, body: JSON.stringify(body) };
  let res;
  try { res = await fetch(API + "/api" + path, opt); }
  catch { throw new Error("Сервер не отвечает. Проверьте, что он запущен: python -m app.server"); }
  const data = await res.json().catch(() => ({}));
  if (!res.ok) throw new Error(data.error || `Ошибка сервера ${res.status}`);
  return data;
}

const ESC = { "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;" };
export const esc = (s) => String(s ?? "").replace(/[&<>"']/g, (c) => ESC[c]);

// html`...` — шаблон с экранированием подстановок; raw() — уже готовая разметка.
class Raw { constructor(s) { this.s = s; } toString() { return this.s; } }
export const raw = (s) => new Raw(s);
export function html(strings, ...vals) {
  let out = strings[0];
  vals.forEach((v, i) => {
    const part = Array.isArray(v) ? v.map((x) => (x instanceof Raw ? x.s : esc(x))).join("")
      : v instanceof Raw ? v.s : v === false || v == null ? "" : esc(v);
    out += part + strings[i + 1];
  });
  return raw(out);
}

export function toast(text, err = false) {
  document.querySelector(".toast")?.remove();
  const el = document.createElement("div");
  el.className = "toast" + (err ? " err" : "");
  el.setAttribute("role", err ? "alert" : "status");
  el.textContent = text;
  document.body.append(el);
  setTimeout(() => el.remove(), err ? 6000 : 3200);
}

export const fmtTotal = (t) => (t == null ? "—" : Number(t).toLocaleString("ru-RU", { minimumFractionDigits: t % 1 ? 1 : 0, maximumFractionDigits: 2 }));
export const scoreClass = (t) => (t == null ? "" : t >= 80 ? "score-hi" : t >= 60 ? "score-mid" : "score-lo");
export const money = (v) => (v == null ? "—" : "$" + Number(v).toFixed(v < 0.1 ? 3 : 2));
export const CHANNEL = { "agent chat": "Чат", "voice call": "Звонок" };

export const ANSWER = { yes: "Да", no: "Нет", na: "Не применяется" };
export function answerLabel(c, a) {
  if (a === "na") return "Не применяется";
  if (c.type === "scale") return String(a);
  if (c.type === "critical") return a === "yes" ? "Есть нарушение" : "Нет нарушений";
  return ANSWER[a] || String(a);
}
export function answerTone(c, a) {
  if (a === "na") return "na";
  if (c.type === "critical") return a === "yes" ? "bad" : "ok";
  if (c.type === "scale") { const top = Math.max(...c.scale.map((s) => s.value)); return a >= top ? "ok" : a === 0 ? "bad" : "mid"; }
  return a === "yes" ? "ok" : "bad";
}
export function allowedAnswers(c) {
  const vals = c.type === "scale" ? c.scale.map((s) => s.value).sort((a, b) => b - a) : ["yes", "no"];
  return c.na_allowed ? [...vals, "na"] : vals;
}

// роль: оператор подаёт апелляцию, проверяющий решает
export const role = {
  get: () => localStorage.getItem("cqa-role") || "operator",
  set: (r) => { localStorage.setItem("cqa-role", r); window.dispatchEvent(new Event("rolechange")); },
};

export const store = {
  get dataset() { return localStorage.getItem("cqa-dataset") || "DEV"; },
  set dataset(v) { localStorage.setItem("cqa-dataset", v); },
};

export function hero({ title, text = "", art, back, actions = "", tag = "", aside = "" }) {
  const acts = actions ? html`<div class="actions">${actions}</div>` : "";
  return html`<section class="hero ${aside ? "has-side" : ""}" style="--art:url(art/${art}.jpg)">
    <div>${back ? html`<a class="back" href="${back.href}">← ${back.label}</a>` : ""}
      <h1>${title} ${tag}</h1>${text ? html`<p>${text}</p>` : ""}</div>
    ${aside ? html`<div class="side">${aside}${acts}</div>` : acts}
  </section>`;
}

export function dialog(inner) {
  const d = document.createElement("dialog");
  d.innerHTML = String(inner);
  document.body.append(d);
  d.addEventListener("close", () => d.remove());
  d.showModal();
  return d;
}

export function confirmDialog(title, text, okLabel) {
  return new Promise((resolve) => {
    const d = dialog(html`<header><h2>${title}</h2></header><div class="content"><p class="muted" style="margin:0">${text}</p></div>
      <footer><button class="btn ghost" value="no">Отмена</button><button class="btn primary" value="yes">${okLabel}</button></footer>`);
    d.querySelectorAll("footer button").forEach((b) => b.onclick = () => { resolve(b.value === "yes"); d.close(); });
    d.addEventListener("cancel", () => resolve(false));
  });
}

// задания оценки: опрос, пока идут
const listeners = new Set();
let polling = null;
export function onJobs(fn) { listeners.add(fn); return () => listeners.delete(fn); }
export async function watchJobs() {
  if (polling) return;
  const tick = async () => {
    try {
      const jobs = await api("/jobs");
      listeners.forEach((fn) => fn(jobs));
      if (!jobs.some((j) => j.status === "running")) { clearInterval(polling); polling = null; }
    } catch { /* сервер перезапускается — попробуем на следующем тике */ }
  };
  polling = setInterval(tick, 1500);
  tick();
}

export const icon = {
  search: raw('<svg width="14" height="14" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2.2" aria-hidden="true"><circle cx="11" cy="11" r="7"/><path d="m20 20-3.5-3.5"/></svg>'),
  chev: raw('<svg width="14" height="14" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2.2" aria-hidden="true"><path d="m9 6 6 6-6 6"/></svg>'),
  x: raw('<svg width="14" height="14" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2.2" aria-hidden="true"><path d="M6 6l12 12M18 6 6 18"/></svg>'),
};
