import { api, html, raw, hero, fmtTotal, scoreClass, store, toast, money } from "../core.js";

const pct = (v) => (v == null ? "—" : Math.round(v * 100) + "%");
const heat = (v) => (v == null ? "na" : v >= 0.9 ? "h4" : v >= 0.75 ? "h3" : v >= 0.5 ? "h2" : "h1");
const short = (t) => t.replace(/^Корректная и полная /, "").split(/[,(]/)[0].trim();

export default async function operators(root, params) {
  const st = await api("/state");
  const versions = await api("/checklists");
  let ck = st.active_checklist, ds = params.ds || "", open = params.name || null;
  const plans = {};

  async function paint() {
    const d = await api(`/operators?checklist=${encodeURIComponent(ck)}${ds ? "&ds=" + encodeURIComponent(ds) : ""}`);
    const titles = Object.fromEntries(d.criteria.map((c) => [c.id, c.title]));
    const cols = d.criteria.filter((c) => c.type !== "critical");
    root.innerHTML = String(html`
      ${hero({ title: "Операторы", art: "analytics",
        text: `Навыки каждого оператора по последним оценкам: где он сильнее команды, где проседает и что потренировать. ${d.operators.length} строк.`,
        actions: html`<select class="input" id="ds" aria-label="Набор" style="width:auto"><option value="">Все наборы</option>${st.datasets.filter((x) => x.id !== "SANDBOX").map((x) => html`<option value="${x.id}" ${raw(x.id === ds ? "selected" : "")}>${x.name}</option>`)}</select>
          <select class="input" id="ck" aria-label="Версия чек-листа" style="width:auto">${versions.map((v) => html`<option value="${v.key}" ${raw(v.key === ck ? "selected" : "")}>${v.name} v${v.version}</option>`)}</select>` })}
      ${!d.operators.length ? html`<div class="panel"><div class="empty"><b>Нет оценок этой версией чек-листа</b>Оцените диалоги в очереди — карта навыков соберётся сама.<br><a class="btn primary" href="#/queue">Открыть очередь</a></div></div>` : html`
      <div class="panel heatmap-wrap"><table class="heatmap">
        <thead><tr><th>Оператор</th><th class="num">Диалогов</th><th class="num">Итог</th>${cols.map((c) => html`<th title="${c.title}">${short(c.title)}</th>`)}<th>Грубые</th></tr>
          <tr class="team"><td>Команда</td><td></td><td></td>${cols.map((c) => html`<td><span class="cell ${heat(d.team[c.id])}">${pct(d.team[c.id])}</span></td>`)}<td></td></tr></thead>
        <tbody>${d.operators.map((o) => html`<tr data-name="${o.name}" class="${o.name === open ? "open" : ""}">
          <td class="nm">${o.name}</td><td class="num muted">${o.n}</td><td class="num ${scoreClass(o.avg_total)}"><b>${fmtTotal(o.avg_total)}</b></td>
          ${cols.map((c) => html`<td><span class="cell ${heat(o.cells[c.id])} ${o.weak.includes(c.id) ? "weak" : ""}" title="${c.title}: ${pct(o.cells[c.id])} · команда ${pct(d.team[c.id])}">${pct(o.cells[c.id])}</span></td>`)}
          <td>${o.critical ? html`<span class="tag bad">${o.critical}</span>` : html`<span class="muted">—</span>`}</td></tr>
          ${o.name === open ? html`<tr class="drill"><td colspan="${cols.length + 4}">${drill(o, titles)}</td></tr>` : ""}`)}</tbody></table></div>
      <p class="muted" style="font-size:12.5px">${d.note} Доля — от максимума по критерию среди диалогов, где он применим; рамкой отмечены слабые места относительно команды.</p>`}`);
    root.querySelector("#ds").onchange = (e) => { ds = e.target.value; paint(); };
    root.querySelector("#ck").onchange = (e) => { ck = e.target.value; paint(); };
    root.querySelectorAll("tr[data-name]").forEach((tr) => (tr.onclick = () => { open = open === tr.dataset.name ? null : tr.dataset.name; paint(); }));
    root.querySelector("[data-plan]")?.addEventListener("click", async (e) => {
      e.stopPropagation(); e.target.disabled = true; e.target.textContent = "Haiku пишет план…";
      try { plans[open] = await api("/operators/plan", { name: open, checklist: ck, ds: ds || undefined }); paint(); }
      catch (x) { toast(x.message, true); e.target.disabled = false; e.target.textContent = "План развития"; }
    });
  }

  function drill(o, titles) {
    const p = plans[o.name];
    const worst = o.dialogs.find((x) => x.channel === "agent chat");
    return html`<div class="drill-grid">
      <div><h3>Слабые места</h3>${o.weak.length ? o.weak.map((k) => html`<div class="weak-row"><b>${titles[k]}</b> <span class="muted">${pct(o.cells[k])}</span>
          ${o.fails.filter((f) => f.criterion === k).slice(0, 2).map((f) => html`<p>${f.reasoning} <a class="link" href="#/c/${encodeURIComponent(f.dataset_id)}/${encodeURIComponent(f.conversation_id)}${f.evidence ? "?m=" + f.evidence : ""}">${f.conversation_id}</a></p>`)}</div>`)
        : html`<p class="muted">Все применимые критерии выполнены полностью.</p>`}
        <h3 style="margin-top:14px">Диалоги</h3><div class="dlist">${o.dialogs.map((x) => html`<a class="chip" href="#/c/${encodeURIComponent(x.dataset_id)}/${encodeURIComponent(x.conversation_id)}">${x.conversation_id} <span class="${scoreClass(x.total)}">${fmtTotal(x.total)}</span></a>`)}</div></div>
      <div class="plan">${p ? html`<h3>План развития</h3><p>${p.summary}</p>${p.focus.map((f) => html`<div class="focus"><b>${f.criterion}</b><p>${f.why}</p><p><span class="muted">Практика:</span> ${f.practice}</p><p class="phrase">«${f.phrase}»</p></div>`)}<p class="muted" style="font-size:12px">Claude Haiku 5.5 · ${p.latency_s} с · ${money(p.cost_usd)}</p>`
        : html`<h3>План развития</h3><p class="muted">Короткий план на неделю по слабым местам и реальным примерам из оценок.</p><button class="btn small primary" data-plan>План развития</button>`}
        ${worst ? html`<div style="margin-top:14px"><a class="btn small" href="#/copilot?sc=${encodeURIComponent(worst.dataset_id + "|" + worst.conversation_id)}">Потренировать на худшем чате · ${worst.conversation_id}</a></div>` : ""}</div>
    </div>`;
  }

  await paint();
  if (open) root.querySelector("tr.open")?.scrollIntoView({ block: "center" });
}
