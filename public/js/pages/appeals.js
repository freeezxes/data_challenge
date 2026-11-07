import { api, html, raw, hero, fmtTotal, answerLabel, allowedAnswers, role, toast, scoreClass } from "../core.js";
import { offset } from "./card.js";

const ROLE = { client: "Клиент", operator: "Оператор", bot: "Бот", system: "Система" };

export default async function appeals(root, params) {
  let tab = params.tab || "open";
  const cache = {};
  const conv = async (ds, cid) => (cache[ds + "/" + cid] ||= await api(`/conversations/${encodeURIComponent(ds)}/${encodeURIComponent(cid)}`));

  async function paint() {
    const all = await api("/appeals");
    const list = all.filter((a) => (tab === "open" ? a.status === "open" : a.status === "decided"));
    const reviewer = role.get() === "reviewer";
    const cards = await Promise.all(list.map(async (a) => {
      const data = await conv(a.dataset_id, a.conversation_id);
      const ev = data.evaluations.find((e) => e.id === a.evaluation_id);
      const checklist = data.checklists[a.checklist_key];
      const c = checklist.criteria.find((x) => x.id === a.criterion_id);
      return { a, data, ev, c };
    }));
    root.innerHTML = String(html`
      ${hero({ title: "Апелляции", art: "appeal",
        text: reviewer ? "Изучите доказательства, подтвердите или измените оценку и укажите причину. Итог пересчитается по весам чек-листа." : "Здесь видны все поданные апелляции. Решение принимает проверяющий — переключите роль вверху справа.",
        actions: html`<div class="seg" role="group" aria-label="Фильтр"><button data-tab="open" aria-pressed="${String(tab === "open")}">Ждут решения · ${all.filter((a) => a.status === "open").length}</button><button data-tab="decided" aria-pressed="${String(tab === "decided")}">Решённые · ${all.filter((a) => a.status === "decided").length}</button></div>` })}
      <div class="panel">${cards.length ? cards.map((x) => block(x, reviewer)) : html`<div class="empty"><b>${tab === "open" ? "Нет апелляций, ждущих решения" : "Решённых апелляций пока нет"}</b>Апелляцию подаёт оператор из карточки диалога кнопкой «Оспорить» у критерия.<br><a class="btn" href="#/queue">Открыть очередь</a></div>`}</div>`);
    root.querySelectorAll("[data-tab]").forEach((b) => (b.onclick = () => { tab = b.dataset.tab; paint(); }));
    cards.forEach((x) => wire(x));
    if (params.id) document.getElementById("ap-" + params.id)?.scrollIntoView({ block: "center" });
  }

  function fragment(data, ev, c) {
    const msgs = data.conversation.messages;
    const d = ev.result.criteria[c.id];
    const ids = new Set(d.evidence_ids);
    const idx = msgs.map((m, i) => (ids.has(m.id) ? i : -1)).filter((i) => i >= 0);
    const keep = new Set(idx.flatMap((i) => [i - 1, i, i + 1]).filter((i) => i >= 0 && i < msgs.length));
    const shown = [...keep].sort((a, b) => a - b).slice(0, 8);
    const call = data.conversation.channel === "voice call";
    return html`<div class="fragment">${shown.map((i) => { const m = msgs[i]; return html`<div class="msg ${m.role} ${ids.has(m.id) ? "mark-mid" : ""}"><div class="ts">${call ? offset(m.t) : m.timestamp}</div><div class="who">${ROLE[m.role]} · ${m.id}</div><div class="text">${m.text}</div></div>`; })}</div>`;
  }

  function block({ a, data, ev, c }, reviewer) {
    const d = ev.result.criteria[c.id];
    const current = ev.final.answers[c.id];
    const link = `#/c/${encodeURIComponent(a.dataset_id)}/${encodeURIComponent(a.conversation_id)}?m=${d.evidence_ids[0] || ""}`;
    const decided = a.status === "decided";
    return html`<article class="appeal" id="ap-${a.id}">
      <div class="l">
        <div style="display:flex;gap:10px;align-items:baseline"><h3>${c.title}</h3><a class="link" href="${link}">${a.conversation_id} →</a>
          <span class="muted" style="margin-left:auto;font-size:12px">${new Date(a.created_at).toLocaleString("ru-RU")}</span></div>
        <div class="muted" style="font-size:13px">Оценка модели: <b style="color:var(--ink)">${answerLabel(c, a.original_answer)}</b> · итог диалога ${fmtTotal(a.original_total)}</div>
        <p style="margin:8px 0 0;color:var(--ink2);font-size:13.5px">${d.reasoning}</p>
        <div class="comment"><span class="muted" style="font-size:12px">Комментарий оператора</span><br>${a.comment}</div>
        ${fragment(data, ev, c)}
      </div>
      <div class="r">${decided ? html`
          <div class="tag ${a.action === "change" ? "warn" : "ok"}">${a.action === "change" ? "Оценка изменена" : "Оценка подтверждена"}</div>
          <p class="delta" style="margin:10px 0">${a.action === "change" ? html`${answerLabel(c, a.original_answer)} → <b>${answerLabel(c, a.new_answer === "na" ? "na" : isNaN(+a.new_answer) ? a.new_answer : +a.new_answer)}</b>` : html`Осталось: <b>${answerLabel(c, a.original_answer)}</b>`}</p>
          <p style="margin:0 0 10px;color:var(--ink2)">${a.reason}</p>
          <div class="delta">Итог диалога: ${fmtTotal(a.original_total)} → <b class="${scoreClass(a.final_total)}">${fmtTotal(a.final_total)}</b></div>`
        : html`
          ${reviewer ? opinion(a, c) : ""}
          <div class="muted" style="font-size:12.5px;margin-bottom:8px">Решение проверяющего</div>
          <div class="answers" role="group" aria-label="Оценка">${allowedAnswers(c).map((v) => html`<button type="button" data-v="${v}" class="${String(v) === String(current) ? "current" : ""}" aria-pressed="${String(String(v) === String(current))}" ${raw(reviewer ? "" : "disabled")}>${answerLabel(c, v)}</button>`)}</div>
          <p class="delta" data-delta style="margin:10px 0">Итог останется ${fmtTotal(ev.final.score.total)}</p>
          <label class="field">Причина решения<textarea class="input" data-reason placeholder="Почему оценка верна или почему её нужно изменить" ${raw(reviewer ? "" : "disabled")}></textarea></label>
          <div class="err-text" data-err></div>
          <div style="display:flex;gap:8px;margin-top:8px">${reviewer ? html`<button class="btn" data-act="confirm">Подтвердить оценку</button><button class="btn primary" data-act="change" disabled>Изменить оценку</button>` : html`<span class="muted" style="font-size:13px">Решение принимает проверяющий. Переключите роль вверху справа.</span>`}</div>`}
      </div></article>`;
  }

  function opinion(a, c) {
    const op = a.assist ? JSON.parse(a.assist) : null;
    if (!op) return html`<div class="opinion empty-op"><div><b>Второе мнение модели</b><span class="muted">Модель перечитает диалог и правила критерия с учётом комментария оператора. Решение остаётся за вами.</span></div>
      <button class="btn small" data-ask="${a.id}">Спросить · ≈ $0,03</button></div>`;
    const sugg = op.suggested_answer === "na" ? "na" : isNaN(+op.suggested_answer) ? op.suggested_answer : +op.suggested_answer;
    const same = String(op.suggested_answer) === String(a.original_answer);
    // ярлык по сути предложения: довод оператора может не подтвердиться, а ступень всё равно смениться
    const [tone, label] = op.verdict === "unclear" ? ["warn", "Спорный случай"]
      : same ? ["bad", "Оценка верна, апелляция не обоснована"]
      : op.verdict === "appeal_valid" ? ["ok", "Апелляция обоснована"] : ["warn", "Довод не подтвердился, но оценку стоит поправить"];
    return html`<div class="opinion"><div class="op-head"><b>Второе мнение модели</b><span class="tag ${tone}">${label}</span></div>
      <p style="margin:6px 0">${op.reasoning}</p>
      ${op.quote ? html`<span class="quote">«${op.quote}»</span>` : ""}
      <div style="display:flex;gap:10px;align-items:center;flex-wrap:wrap"><span class="muted" style="font-size:12.5px">Предлагает: <b style="color:var(--ink)">${answerLabel(c, sugg)}</b>${op.evidence_ids?.length ? ` · ${op.evidence_ids.join(", ")}` : ""}</span>
        <button class="link" data-take="${a.id}" data-sugg="${op.suggested_answer}">Взять предложение</button></div></div>`;
  }

  function wire({ a, ev, c }) {
    const el = document.getElementById("ap-" + a.id);
    if (!el || a.status !== "open") return;
    let pick = ev.final.answers[c.id];
    el.querySelector("[data-ask]")?.addEventListener("click", async (e) => {
      e.target.disabled = true; e.target.textContent = "Модель читает диалог…";
      try { await api(`/appeals/${a.id}/assist`, {}); paint(); }
      catch (x) { toast(x.message, true); e.target.disabled = false; e.target.textContent = "Спросить ещё раз"; }
    });
    const delta = el.querySelector("[data-delta]"), err = el.querySelector("[data-err]");
    const changeBtn = el.querySelector('[data-act="change"]');
    el.querySelectorAll(".answers [data-v]").forEach((b) => (b.onclick = async () => {
      pick = b.dataset.v;
      el.querySelectorAll(".answers [data-v]").forEach((x) => x.setAttribute("aria-pressed", String(x === b)));
      const same = String(pick) === String(ev.final.answers[c.id]);
      if (changeBtn) changeBtn.disabled = same;
      if (same) { delta.innerHTML = `Итог останется ${fmtTotal(ev.final.score.total)}`; return; }
      try {
        const s = await api(`/evaluations/${ev.id}/preview`, { criterion_id: c.id, new_answer: pick });
        delta.innerHTML = String(html`Итог диалога: ${fmtTotal(ev.final.score.total)} → <b class="${scoreClass(s.total)}">${fmtTotal(s.total)}</b>`);
      } catch (x) { err.textContent = x.message; }
    }));
    el.querySelector("[data-take]")?.addEventListener("click", (e) => {
      const op = JSON.parse(a.assist);
      el.querySelector(`.answers [data-v="${CSS.escape(e.target.dataset.sugg)}"]`)?.click();
      const r = el.querySelector("[data-reason]");
      if (!r.value.trim()) r.value = op.reasoning;
      r.focus();
    });
    el.querySelectorAll("[data-act]").forEach((b) => (b.onclick = async () => {
      err.textContent = "";
      b.disabled = true;
      try {
        await api(`/appeals/${a.id}/decision`, { action: b.dataset.act, new_answer: pick, reason: el.querySelector("[data-reason]").value });
        toast(b.dataset.act === "change" ? "Оценка изменена, итог пересчитан" : "Оценка подтверждена");
        window.dispatchEvent(new Event("cqa:changed"));
        delete cache[a.dataset_id + "/" + a.conversation_id];
        paint();
      } catch (x) { err.textContent = x.message; b.disabled = false; }
    }));
  }

  await paint();
}
