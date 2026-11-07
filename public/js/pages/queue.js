import { API, api, html, raw, hero, fmtTotal, scoreClass, CHANNEL, store, toast, onJobs, watchJobs, icon, money } from "../core.js";
import { fetchDialog, uploadDialog } from "./home.js";

const FILTERS = [
  ["all", "Все", () => true],
  ["todo", "Не оценены", (r) => !r.evaluation || r.evaluation.status === "error"],
  ["appeal", "Ждут решения", (r) => r.evaluation?.open_appeals],
  ["critical", "Грубые нарушения", (r) => r.evaluation?.critical],
  ["calls", "Звонки", (r) => r.channel === "voice call"],
  ["low", "Ниже 60", (r) => r.evaluation?.total != null && r.evaluation.total < 60],
  ["unsure", "Модель сомневается", (r) => r.evaluation?.uncertain > 0],
  ["check", "Нужна проверка", (r) => ["needs_review", "error"].includes(r.evaluation?.status)],
];
const VIOL = { insult: "Оскорбление", bypass: "Обход компании", disclosure: "Раскрытие данных" };

export default async function queue(root, params) {
  let st = await api("/state");
  if (!st.datasets.some((d) => d.id === store.dataset)) store.dataset = st.datasets[0]?.id || "DEV";
  let rows = [], filter = params.f || "all", q = "", selected = new Set();
  let votes = Number(localStorage.getItem("cqa-votes") || st.votes || 3);

  async function load() { rows = await api(`/datasets/${encodeURIComponent(store.dataset)}/conversations`); }
  await load();

  root.innerHTML = String(html`
    <div id="hero"></div><div id="run"></div>
    <div class="toolbar" id="filters"></div>
    <div class="panel"><table class="list"><thead><tr>
      <th class="sel"><input type="checkbox" id="all" aria-label="Выбрать все видимые"></th><th>Диалог</th><th>Канал</th><th class="hide-m">Язык</th>
      <th class="hide-m">О чём</th><th>Итог</th><th class="hide-m">Нарушение</th><th>Статус</th><th></th></tr></thead>
      <tbody id="rows"></tbody></table></div>`);

  const visible = () => rows.filter(FILTERS.find((f) => f[0] === filter)[2])
    .filter((r) => !q || (r.conversation_id + " " + r.topic).toLowerCase().includes(q));

  function paintHero() {
    const ds = st.datasets.find((d) => d.id === store.dataset);
    const done = rows.filter((r) => r.evaluation && r.evaluation.status !== "error");
    const totals = done.map((r) => r.evaluation.total).filter((t) => t != null);
    const avg = totals.length ? totals.reduce((a, b) => a + b, 0) / totals.length : null;
    const open = rows.reduce((n, r) => n + (r.evaluation?.open_appeals || 0), 0);
    root.querySelector("#hero").innerHTML = String(hero({
      title: "Очередь", art: "queue",
      text: `${rows.length} диалогов, оценено ${done.length}` + (avg != null ? `, средний итог ${fmtTotal(Math.round(avg * 10) / 10)}` : "") + (open ? `. Апелляций ждут решения: ${open}.` : "."),
      actions: html`<select class="input" id="ds" aria-label="Набор" style="width:auto">${st.datasets.map((d) => html`<option value="${d.id}" ${raw(d.id === store.dataset ? "selected" : "")}>${d.name}</option>`)}</select>
        <button class="btn" id="load">Загрузить набор</button>
        <a class="btn" href="${API}/api/datasets/${encodeURIComponent(store.dataset)}/export" download title="CSV: ответы по каждому критерию, итог, грубые нарушения">Скачать результаты</a>
        <select class="input" id="votes" aria-label="Режим оценки" style="width:auto" title="Несколько прогонов модели одновременно; ответ — по большинству, у шкал — по медиане">
          <option value="3" ${raw(votes === 3 ? "selected" : "")}>3 прогона · точнее · ≈ $0,12</option>
          <option value="1" ${raw(votes === 1 ? "selected" : "")}>1 прогон · быстрее · ≈ $0,04</option></select>
        <button class="btn primary" id="eval">${selected.size ? `Оценить выбранные · ${selected.size}` : "Оценить неоценённые"}</button>`,
    }));
    const h = root.querySelector("#hero");
    h.querySelector("#ds").onchange = async (e) => { store.dataset = e.target.value; selected.clear(); await load(); paint(); };
    h.querySelector("#load").onclick = (e) => {
      const m = document.createElement("div");
      m.style.cssText = "display:flex;gap:8px";
      e.target.replaceWith(Object.assign(document.createElement("span"), { innerHTML: '<button class="btn" id="b1">По group_id</button> <button class="btn" id="b2">Файлом</button>' }));
      h.querySelector("#b1").onclick = () => fetchDialog(st);
      h.querySelector("#b2").onclick = () => uploadDialog();
    };
    h.querySelector("#eval").onclick = () => start();
    h.querySelector("#votes").onchange = (e) => { votes = Number(e.target.value); localStorage.setItem("cqa-votes", votes); };
    void ds;
  }

  async function start() {
    const ids = [...selected];
    try {
      await api("/jobs", { dataset_id: store.dataset, conversation_ids: ids.length ? ids : null, only_missing: !ids.length, votes });
      selected.clear(); toast("Оценка запущена"); watchJobs();
    } catch (e) { toast(e.message, true); }
  }

  function paintFilters() {
    const el = root.querySelector("#filters");
    el.innerHTML = String(html`${FILTERS.map(([k, label, fn]) => html`<button class="chip" data-f="${k}" aria-pressed="${String(filter === k)}">${label} <span>${rows.filter(fn).length}</span></button>`)}
      <label class="search"><span class="sr">Поиск</span>${icon.search}<input class="input" id="q" placeholder="Номер диалога или фраза клиента" value="${q}"></label>`);
    el.querySelectorAll(".chip").forEach((b) => (b.onclick = () => { filter = b.dataset.f; paint(); }));
    const qi = el.querySelector("#q");
    qi.oninput = () => { q = qi.value.trim().toLowerCase(); paintRows(); };
  }

  function status(ev) {
    if (!ev) return html`<span class="tag">Не оценён</span>`;
    if (ev.status === "error") return html`<span class="tag bad" title="${ev.error}">Ошибка оценки</span>`;
    if (ev.open_appeals) return html`<span class="tag warn">Апелляция</span>`;
    if (ev.decided_appeals) return html`<span class="tag ok">Пересмотрен</span>`;
    if (ev.critical) return html`<span class="tag bad">Разобрать нарушение</span>`;
    if (ev.status === "needs_review") return html`<span class="tag warn" title="Доказательства не прошли проверку">Проверить</span>`;
    if (ev.uncertain) return html`<span class="tag warn" title="Прогоны модели разошлись по ${ev.uncertain} критериям">Модель сомневается</span>`;
    return html`<span class="tag ok">Оценён</span>`;
  }

  function paintRows() {
    const list = visible();
    const tb = root.querySelector("#rows");
    if (!list.length) {
      tb.innerHTML = `<tr><td colspan="9"><div class="empty"><b>${rows.length ? "Ничего не нашлось" : "В наборе нет диалогов"}</b>${rows.length ? "Снимите фильтр или измените запрос." : "Загрузите набор по group_id или файлом."}</div></td></tr>`;
      return;
    }
    tb.innerHTML = String(html`${list.map((r) => {
      const ev = r.evaluation;
      const old = ev && ev.checklist_key !== st.active_checklist;
      return html`<tr data-id="${r.conversation_id}">
        <td class="sel"><input type="checkbox" aria-label="Выбрать ${r.conversation_id}" ${raw(selected.has(r.conversation_id) ? "checked" : "")}></td>
        <td class="id">${r.conversation_id}</td>
        <td>${CHANNEL[r.channel] || r.channel}${r.channel === "voice call" ? raw('<span class="wave" aria-hidden="true"></span>') : ""}</td>
        <td class="hide-m muted">${r.language}</td>
        <td class="hide-m topic" title="${r.topic}">${r.topic}</td>
        <td class="total num ${scoreClass(ev?.total)}">${ev && ev.total != null ? fmtTotal(ev.total) : "—"}${ev && ev.original_total !== ev.total ? html` <small class="muted" title="до апелляции">был ${fmtTotal(ev.original_total)}</small>` : ""}</td>
        <td class="hide-m">${ev?.violations?.length ? html`<span class="bad-c">${ev.violations.map((v) => VIOL[v] || v).join(", ")}</span>` : html`<span class="muted">—</span>`}</td>
        <td>${status(ev)}${old ? html` <span class="tag" title="Оценён версией ${ev.checklist_key}">старая версия</span>` : ""}</td>
        <td class="chev">${icon.chev}</td></tr>`;
    })}`);
    tb.querySelectorAll("tr[data-id]").forEach((tr) => {
      const cb = tr.querySelector("input");
      cb.onclick = (e) => { e.stopPropagation(); cb.checked ? selected.add(tr.dataset.id) : selected.delete(tr.dataset.id); paintHero(); };
      tr.onclick = () => (location.hash = `#/c/${encodeURIComponent(store.dataset)}/${encodeURIComponent(tr.dataset.id)}`);
    });
    const all = root.querySelector("#all");
    all.checked = list.every((r) => selected.has(r.conversation_id));
    all.onclick = () => { list.forEach((r) => (all.checked ? selected.add(r.conversation_id) : selected.delete(r.conversation_id))); paintHero(); paintRows(); };
  }

  function paint() { paintHero(); paintFilters(); paintRows(); }
  paint();

  let lastDone = -1;
  const off = onJobs(async (jobs) => {
    const job = jobs.find((j) => j.dataset_id === store.dataset);
    const el = root.querySelector("#run");
    if (!el) return;
    if (!job || (job.status !== "running" && Date.now() - Date.parse(job.finished_at || 0) > 120000)) { el.innerHTML = ""; return; }
    const n = job.done + job.failed, pct = Math.round((n / job.total) * 100);
    const live = job.status === "running";
    el.innerHTML = String(html`<div class="run ${job.failed && !live ? "err" : ""}" role="status">
      <span class="dot ${live ? "live" : ""}"></span>
      <div><b>${live ? `Оценка идёт: ${n} из ${job.total}` : `Оценка завершена: ${job.done} из ${job.total}`}${job.failed ? `, ошибок ${job.failed}` : ""}</b>
      <small>Чек-лист ${job.checklist_key} · потрачено ${money(job.cost_usd)} · каждый диалог оценивается вживую и сохраняется сразу</small></div>
      <div class="bar" aria-hidden="true"><i style="width:${pct}%"></i></div>
      ${job.failed && !live ? html`<button class="btn small" id="retry">Повторить ошибки</button>` : ""}</div>`);
    el.querySelector("#retry")?.addEventListener("click", start);
    if (n !== lastDone) { lastDone = n; await load(); st = await api("/state"); paintHero(); paintFilters(); paintRows(); }
  });
  watchJobs();
  return off;
}
