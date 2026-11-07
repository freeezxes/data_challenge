import { api, html, raw, hero, fmtTotal, money, store } from "../core.js";

const pct = (v) => (v == null ? "—" : Math.round(v * 100) + "%");
const tone = (v, hi = 0.85) => (v == null ? "" : v >= hi ? "" : v >= 0.6 ? "mid" : "lo");

export default async function analytics(root) {
  const st = await api("/state");
  if (!st.datasets.some((d) => d.id === store.dataset)) store.dataset = st.datasets[0]?.id;
  const versions = await api("/checklists");
  let ck = st.active_checklist;

  async function paint() {
    const a = await api(`/analytics/${encodeURIComponent(store.dataset)}?checklist=${encodeURIComponent(ck)}`);
    root.innerHTML = String(html`
      ${hero({ title: "Аналитика", art: "analytics",
        text: `${a.n_evaluated} из ${a.n_total} диалогов оценено версией ${a.checklist_key}. Итоги — после решений по апелляциям.`,
        actions: html`<select class="input" id="ds" aria-label="Набор" style="width:auto">${st.datasets.map((d) => html`<option value="${d.id}" ${raw(d.id === store.dataset ? "selected" : "")}>${d.name}</option>`)}</select>
          <select class="input" id="ck" aria-label="Версия чек-листа" style="width:auto">${versions.map((v) => html`<option value="${v.key}" ${raw(v.key === ck ? "selected" : "")}>${v.name} v${v.version}</option>`)}</select>` })}
      ${!a.n_evaluated ? html`<div class="panel"><div class="empty"><b>Нет оценок этой версией чек-листа</b>Запустите оценку в очереди — показатели появятся по мере ответов модели.<br><a class="btn primary" href="#/queue">Открыть очередь</a></div></div>` : html`
      <div class="kpis">
        <div class="kpi"><small>Средний итог</small><b class="num">${fmtTotal(a.avg_total)}</b><span>${Object.entries(a.by_language).map(([k, v]) => `${k} ${fmtTotal(v.avg)}`).join(" · ")}</span></div>
        <div class="kpi"><small>Грубые нарушения</small><b class="num ${a.critical ? "bad-c" : ""}">${a.critical}</b><span>из ${a.n_evaluated} диалогов</span></div>
        <div class="kpi"><small>Апелляции</small><b class="num">${a.appeals.open + a.appeals.confirmed + a.appeals.changed}</b><span>ждут ${a.appeals.open} · подтверждено ${a.appeals.confirmed} · изменено ${a.appeals.changed}</span></div>
        <div class="kpi"><small>Стоимость оценки</small><b class="num">${money(a.cost_per_dialog)}</b><span>за диалог, всего ${money(a.cost_usd)} · ${st.model}</span></div>
      </div>
      <div class="grid" style="grid-template-columns:minmax(0,1.4fr) minmax(0,1fr)">
        <section class="panel"><header><h2>Выполнение критериев</h2><span class="meta">доля от максимума среди применимых</span></header><div class="body bars">
          ${a.criteria.filter((c) => c.type !== "critical").map((c) => html`<div class="hbar"><span class="t" title="${c.title}">${c.title}</span><span class="track"><i class="${tone(c.rate)}" style="width:${Math.round((c.rate || 0) * 100)}%"></i></span><span class="v">${pct(c.rate)}</span></div>
            ${c.na ? html`<div class="muted" style="font-size:12px;margin:-8px 0 0">не применялся в ${c.na} из ${c.n}</div>` : ""}`)}
          ${a.criteria.filter((c) => c.type === "critical").map((c) => html`<div class="hbar"><span class="t">${c.title}</span><span class="track"><i class="lo" style="width:${Math.round((c.rate || 0) * 100)}%"></i></span><span class="v">${pct(c.rate)}</span></div>`)}
        </div></section>
        <div class="grid">
          <section class="panel"><header><h2>По каналам и языкам</h2></header><div class="body bars">
            ${Object.entries({ ...a.by_channel, ...a.by_language }).map(([k, v]) => html`<div class="hbar"><span class="t">${{ "agent chat": "Чаты", "voice call": "Звонки" }[k] || k} <span class="muted">· ${v.n}</span></span><span class="track"><i class="${tone(v.avg / 100, 0.8)}" style="width:${v.avg}%"></i></span><span class="v">${fmtTotal(v.avg)}</span></div>`)}
          </div></section>
          ${a.gold ? html`<section class="panel"><header><h2>Сверка с разметкой команды</h2><span class="meta">${a.gold.n} диалогов</span></header><div class="body">
            <div style="display:flex;gap:24px"><div><div class="stat num" style="font-size:28px;font-weight:700">${pct(a.gold.macro_accuracy)}</div><small class="muted">совпадение по критериям</small></div>
              <div><div class="num" style="font-size:28px;font-weight:700">${fmtTotal(a.gold.total_mae)}</div><small class="muted">среднее отклонение итога</small></div>
              <div><div class="num" style="font-size:28px;font-weight:700">${a.gold.all_exact}</div><small class="muted">все оценки совпали</small></div></div>
            <p class="muted" style="margin:12px 0 0;font-size:12.5px">${a.gold.note} Ответы модели до апелляций.</p>
          </div></section>` : ""}
        </div>
      </div>`}`);
    root.querySelector("#ds").onchange = (e) => { store.dataset = e.target.value; paint(); };
    root.querySelector("#ck").onchange = (e) => { ck = e.target.value; paint(); };
  }
  await paint();
}
