// Общие помощники интерфейса. Без сборки и зависимостей: модуль можно встроить
// как веб-ресурс Dynamics 365 — адрес API задаётся атрибутом data-api у <body>.

export const API = (document.body.dataset.api || "").replace(/\/$/, "");
let account = null;
let guest = false;
let guestKey = "";
let csrf = "";
let savedKey = false;
let keyPrompt = null;
let prefValues = {};
let preferenceSave = Promise.resolve();
let preferenceError = null;
const modelRoute = /^\/(jobs|operators\/plan|copilot\/(hint|client|finish)|evaluations\/\d+\/(coach|variant)|appeals\/\d+\/assist)$/;

export function hasApiKey() { return savedKey; }
export function currentAccount() { return account; }
export async function initializeSession() {
  const session = await api("/auth/session");
  guest = Boolean(session.guest);
  account = session.user; csrf = session.csrf; savedKey = session.has_key;
  prefValues = session.preferences || {};
  if (guest) { try { prefValues = JSON.parse(localStorage.getItem("cqa-guest-preferences") || "{}"); } catch {} }
  window.dispatchEvent(new Event("cqa:keychange"));
  return session;
}
export async function refreshSession() {
  if (guest) return;
  const session = await api("/auth/session");
  csrf = session.csrf; savedKey = session.has_key;
  window.dispatchEvent(new Event("cqa:keychange"));
}

export const preferences = {
  getItem: (key) => prefValues[key] ?? null,
  setItem(key, value) {
    prefValues[key] = String(value);
    if (guest) { localStorage.setItem("cqa-guest-preferences", JSON.stringify(prefValues)); return; }
    const values = { ...prefValues };
    preferenceSave = preferenceSave.then(() => api("/auth/preferences", { values }))
      .then(() => { preferenceError = null; })
      .catch((error) => { preferenceError = error; toast("Не удалось сохранить настройки: " + error.message, true); });
  },
  async flush() { await preferenceSave; if (preferenceError) throw preferenceError; },
};

export function apiKeyDialog() {
  if (keyPrompt) return keyPrompt;
  keyPrompt = new Promise((resolve) => {
    const d = dialog(html`<header><h2 id="api-key-title">Свой ключ Claude API</h2></header>
      <form><div class="content">
        <p class="muted">Ключ используется для оценки, суфлёра и подсказок. Стоимость вызовов оплачивается с вашего аккаунта Anthropic.</p>
        <label class="field" for="api-key-input">API-ключ<input class="input" id="api-key-input" type="password" autocomplete="off" autocapitalize="none" spellcheck="false" placeholder="sk-ant-…" aria-describedby="api-key-help api-key-error" required></label>
        <p class="muted" id="api-key-help">${guest ? "Ключ действует в этой вкладке до перезагрузки и передаётся только с вашими запросами. Гостевые импорты и результаты общие для всех посетителей." : "Ключ сохраняется в вашем аккаунте в зашифрованном виде. Он будет доступен после повторного входа и на другом устройстве. Полный ключ обратно в браузер не передаётся."}</p>
        ${savedKey ? html`<p class="muted">Ключ уже сохранён. Введите новый для замены или удалите сохранённый ключ. Уже запущенные оценки продолжатся.</p>` : ""}
        <p class="err-text" id="api-key-error" role="alert"></p>
      </div><footer>${savedKey ? html`<button class="btn ghost" type="button" data-clear>Удалить ключ</button>` : ""}
        <button class="btn ghost" type="button" data-cancel>Отмена</button><button class="btn primary" type="submit">${savedKey ? "Заменить ключ" : "Сохранить ключ"}</button>
      </footer></form>`);
    d.classList.add("api-key-dialog"); d.setAttribute("aria-labelledby", "api-key-title");
    const input = d.querySelector("input");
    let accepted = false, busy = false;
    const save = async (value) => {
      busy = true; d.querySelectorAll("button").forEach((button) => { button.disabled = true; });
      try {
        const session = guest ? (guestKey = value, {has_key: Boolean(value)}) : await api("/auth/key", { key: value });
        savedKey = session.has_key; accepted = Boolean(value);
        window.dispatchEvent(new Event("cqa:keychange"));
        d.close(); toast(guest ? (value ? "Ключ добавлен для этой вкладки" : "Ключ удалён") : (value ? "Ключ сохранён в аккаунте" : "Ключ удалён из аккаунта"));
      } catch (error) { d.querySelector(".err-text").textContent = error.message; }
      finally { busy = false; d.querySelectorAll("button").forEach((button) => { button.disabled = false; }); }
    };
    d.addEventListener("cancel", (event) => { if (busy) event.preventDefault(); });
    d.querySelector("[data-cancel]").onclick = () => d.close();
    d.querySelector("[data-clear]")?.addEventListener("click", () => save(""));
    d.querySelector("form").onsubmit = (event) => {
      event.preventDefault();
      const value = input.value.trim();
      if (!/^sk-ant-[A-Za-z0-9_-]{20,500}$/.test(value)) {
        d.querySelector(".err-text").textContent = "Проверьте ключ: он должен начинаться с sk-ant- и не содержать пробелов.";
        input.setAttribute("aria-invalid", "true"); input.focus(); return;
      }
      save(value);
    };
    d.addEventListener("close", () => { input.value = ""; resolve(accepted); });
    input.focus();
  }).finally(() => { keyPrompt = null; });
  return keyPrompt;
}

export async function api(path, body) {
  const paid = body !== undefined && modelRoute.test(path);
  if (paid && !savedKey) {
    if (path === "/copilot/hint") throw new Error("Для подсказок сохраните свой ключ кнопкой «API-ключ».");
    if (!await apiKeyDialog()) throw new Error("Запуск отменён: добавьте свой ключ Claude API.");
  }
  const opt = { credentials: "same-origin", headers: {} };
  if (account) opt.headers["X-CQA-Account"] = account.id;
  if (guest && guestKey) opt.headers["X-CQA-API-Key"] = guestKey;
  if (body !== undefined) {
    const target = new URL(API || location.origin, location.href);
    if (target.protocol !== "https:" && !["localhost", "127.0.0.1", "[::1]"].includes(target.hostname))
      throw new Error("Откройте сайт по HTTPS.");
    opt.method = "POST"; opt.body = JSON.stringify(body);
    Object.assign(opt.headers, { "Content-Type": "application/json", "X-CQA-Request": "1", "X-CQA-CSRF": csrf });
  }
  let res;
  try { res = await fetch(API + "/api" + path, opt); }
  catch { throw new Error("Сервер не отвечает. Проверьте соединение и повторите действие."); }
  const data = await res.json().catch(() => ({}));
  if (res.status === 401 || res.status === 409) location.replace("/login");
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
export const money = (v) => (v == null ? "—" : v > 0 && v < 0.001 ? "< $0.001" : "$" + Number(v).toFixed(v < 0.1 ? 3 : 2));
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
  get: () => preferences.getItem("cqa-role") || "operator",
  set: (r) => { preferences.setItem("cqa-role", r); window.dispatchEvent(new Event("rolechange")); },
};

export const store = {
  get dataset() { return preferences.getItem("cqa-dataset") || "DEV"; },
  set dataset(v) { preferences.setItem("cqa-dataset", v); },
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

// метки фактов из ответа модели → по-русски (ступени из них выводит cqa/derive.py)
export const FACT_LABEL = {
  needed: "нужное уточнение", known_repeat: "переспросил известное",
  essential_closed: "суть закрыта", essential_partial: "суть — общая подсказка", essential_ignored: "суть без ответа", essential_wrong: "ошибочный ответ",
  minor_closed: "деталь закрыта", minor_open: "деталь не закрыта",
  missed_question: "пропущен вопрос", repeat_known: "повтор известного", off_topic: "не по теме", unclear_wording: "неясная формулировка",
  confusing_order: "путаный порядок", unwarned_wait: "ожидание без предупреждения", contradiction: "противоречие",
};
const FACT_FIELD = { operator_questions: "вопросы оператора", client_questions: "вопросы клиента", glitches: "сбои",
  answered_by_guess: "ответил наугад", ignored_request: "проигнорировал запрос", client_confused: "клиент не понял ответ" };
export function factsText(facts) {
  return Object.entries(facts || {}).map(([k, v]) => {
    if (Array.isArray(v)) {
      if (!v.length) return `${FACT_FIELD[k] || k}: нет`;
      const n = {}; v.forEach((x) => (n[x] = (n[x] || 0) + 1));
      return `${FACT_FIELD[k] || k}: ` + Object.entries(n).map(([x, c]) => (FACT_LABEL[x] || x) + (c > 1 ? ` ×${c}` : "")).join(", ");
    }
    return v === true ? FACT_FIELD[k] || k : null;
  }).filter(Boolean).join(" · ");
}

// выбранная для запуска версия чек-листа (по умолчанию — активная)
export function checklistPicker(versions, current, id = "ck") {
  return html`<select class="input" id="${id}" aria-label="Чек-лист для оценки" title="Какой версией чек-листа оценивать" style="width:auto">${versions.map((v) =>
    html`<option value="${v.key}" ${raw(v.key === current ? "selected" : "")}>${v.name} · v${v.version}${v.active ? " · активная" : ""}</option>`)}</select>`;
}
