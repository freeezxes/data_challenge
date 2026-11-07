import { factsText, checklistPicker } from "../core.js";
import { api, html, raw, hero, esc, fmtTotal, scoreClass, answerLabel, answerTone, role, toast, dialog, watchJobs, onJobs, money, CHANNEL } from "../core.js";

const ROLE = { client: "Клиент", operator: "Оператор", bot: "Бот", system: "Система" };
const VIOL = { insult: "оскорбление клиента", bypass: "предложение услуги в обход компании", disclosure: "раскрытие конфиденциальной информации" };

export const offset = (t) => { t = Math.max(0, Math.round(t)); const h = Math.floor(t / 3600), m = Math.floor((t % 3600) / 60), s = t % 60; return (h ? h + ":" + String(m).padStart(2, "0") : String(m).padStart(2, "0")) + ":" + String(s).padStart(2, "0"); };
const tsOf = (conv, m) => (conv.channel === "voice call" ? offset(m.t) : m.timestamp);
const short = (t) => t.replace(/^Корректная и полная /, "").split(/[,(]/)[0].trim();

function rangeIds(range, conv) {
  const ids = conv.messages.map((m) => m.id);
  const found = String(range || "").match(/m\d+/g) || [];
  if (found.length >= 2 && /[–—-]/.test(range)) {
    const a = ids.indexOf(found[0]), b = ids.indexOf(found[found.length - 1]);
    if (a >= 0 && b >= a) return ids.slice(a, b + 1);
  }
  return found.filter((x) => ids.includes(x));
}

export default async function card(root, params) {
  const { ds, cid } = params;
  let data = await api(`/conversations/${encodeURIComponent(ds)}/${encodeURIComponent(cid)}`);
  const versions = await api("/checklists");
  let pick = versions.find((v) => v.active)?.key;
  const variant = data.variant_of;
  let baseEv = null;
  if (variant) {
    try {
      const base = await api(`/conversations/${encodeURIComponent(variant.dataset_id)}/${encodeURIComponent(variant.conversation_id)}`);
      baseEv = base.evaluations.find((e) => e.id === variant.evaluation_id) || null;
    } catch { /* исходник удалён — покажем копию без сравнения */ }
  }
  let evIndex = 0;

  function paint() {
    const conv = data.conversation;
    const ev = data.evaluations[evIndex];
    const ok = ev && ev.result;
    const checklist = ev ? data.checklists[ev.checklist_key] : null;
    const fin = ok ? ev.final : null;
    const isCall = conv.channel === "voice call";
    const total = fin?.score.total;
    const notes = {};
    if (ok) for (const c of checklist.criteria) {
      const d = ev.result.criteria[c.id], a = fin.answers[c.id];
      for (const id of d.evidence_ids || []) (notes[id] ||= []).push({ c, a, tone: answerTone(c, a) });
    }
    const opName = (conv.messages.find((m) => m.role === "operator")?.text.match(/(?:меня зовут|я|менің атым|мен|my name is|I'?m)\s+([A-ZА-ЯЁӘҒҚҢӨҰҮҺІ][a-zа-яёәғқңөұүһі]+)/) || [])[1];

    root.innerHTML = String(html`
      ${hero({ back: { href: "#/queue", label: "Очередь" }, art: isCall ? "call" : "chat",
        title: `${CHANNEL[conv.channel] || "Диалог"} ${conv.conversation_id}`,
        tag: fin?.score.critical ? html`<span class="tag bad" style="vertical-align:middle;font-size:13px">Грубое нарушение</span>` : "",
        text: ok ? ev.result.summary : `${conv.language} · ${conv.messages.length} реплик${opName ? ` · оператор ${opName}` : ""}`,
        aside: ok ? html`<div class="big"><b class="num ${scoreClass(total)}">${fmtTotal(total)}</b><small>${total == null ? "итог не рассчитывается: нет применимых критериев с весом" : "из 100"}${fin.changed && Object.keys(fin.changed).length ? html` · до апелляции ${fmtTotal(ev.result.score.total)}` : ""}</small></div>` : "",
        actions: html`${data.evaluations.length > 1 ? html`<select class="input" id="hist" aria-label="История оценок" style="width:auto">${data.evaluations.map((e, i) => html`<option value="${i}" ${raw(i === evIndex ? "selected" : "")}>#${e.id} · ${e.checklist_key} · ${new Date(e.created_at).toLocaleString("ru-RU", { day: "2-digit", month: "2-digit", hour: "2-digit", minute: "2-digit" })}${e.status === "error" ? " · ошибка" : ""}</option>`)}</select>` : ""}
          ${checklistPicker(versions, pick)}
          <button class="btn ${ok ? "" : "primary"}" id="eval">${ok ? "Оценить заново" : "Оценить"}</button>` })}
      ${variant ? html`<div class="variant-bar"><b>Изменённая копия диалога ${variant.conversation_id}</b>
        <span>${variant.mode === "replace" ? `реплика ${variant.edited_id} заменена` : `реплика ${variant.edited_id} добавлена`}${variant.note ? ` · ${variant.note}` : ""}</span>
        <span class="delta">Итог: ${fmtTotal(variant.total)} → <b class="${scoreClass(total)}">${ok ? fmtTotal(total) : "оценивается…"}</b>${ok && total != null && variant.total != null ? html` <span class="${total >= variant.total ? "ok-c" : "bad-c"}">(${total >= variant.total ? "+" : ""}${fmtTotal(Math.round((total - variant.total) * 100) / 100)})</span>` : ""}</span>
        <a class="link" href="#/c/${encodeURIComponent(variant.dataset_id)}/${encodeURIComponent(variant.conversation_id)}">к исходному диалогу</a></div>` : ""}
      <div id="run"></div>
      <div class="card">
        <section class="panel transcript" aria-label="Транскрипт">
          <header><h2>${isCall ? "Расшифровка звонка" : "Переписка"}</h2><span class="meta">${conv.language} · ${conv.messages.length} реплик · бот и системные события — контекст</span></header>
          ${isCall ? html`<div class="player">${data.audio ? html`<audio id="audio" controls preload="metadata" src="${data.audio}"></audio><small>Нажмите время реплики, чтобы перейти к месту записи</small>` : html`<small>Записи нет</small>`}</div>` : ""}
          ${conv.messages.map((m) => {
            const n = notes[m.id] || [];
            const worst = n.some((x) => x.tone === "bad") ? "bad" : n.some((x) => x.tone === "mid") ? "mid" : n.length && m.role === "operator" ? "ok" : "";
            return html`<article class="msg ${m.role} ${worst ? "mark-" + worst : ""} ${variant?.edited_id === m.id ? "edited" : ""}" id="${m.id}" data-t="${m.t}">
              <div class="ts" title="${isCall ? `Исходная метка: ${m.timestamp}` : ""}">${isCall && data.audio ? html`<button type="button" data-seek="${m.t}" title="Слушать с этого места">${tsOf(conv, m)}</button>` : tsOf(conv, m)}</div>
              <div class="who">${ROLE[m.role] || m.role}${m.role === "operator" && opName ? " · " + opName : ""} <span class="muted" style="font-weight:400">${m.id}</span>${variant?.edited_id === m.id ? html` <span class="tag warn">изменено</span>` : ""}${m.role === "operator" && ok ? html`<button class="link edit-msg" data-edit="${m.id}" title="Изменить реплику в копии диалога и переоценить">Изменить</button>` : ""}</div>
              <div class="text">${m.text}</div>
              ${n.length ? html`<div class="notes">${n.map((x) => html`<span class="note ${x.tone === "bad" ? "bad" : x.tone === "mid" ? "mid" : ""}">${short(x.c.title)}: ${answerLabel(x.c, x.a)}</span>`)}</div>` : ""}
            </article>`;
          })}
        </section>
        <aside class="panel scores" aria-label="Оценка">${scoresPanel(conv, ev, checklist)}</aside>
      </div>`);
    wire(conv, ev, checklist);
    if (params.m) jump(params.m);
  }

  function scoresPanel(conv, ev, checklist) {
    if (!ev) return html`<header><h2>Оценка</h2></header><div class="empty"><b>Диалог ещё не оценён</b>Выберите версию чек-листа и нажмите «Оценить»: модель проверит диалог, обычно за 20–40 секунд.</div>`;
    if (!ev.result) return html`<header><h2>Оценка</h2></header><div class="alert"><b>Оценка не удалась</b>${ev.error}</div><div class="empty">Повторите попытку кнопкой «Оценить заново». Ошибка одного диалога не мешает остальным.</div>`;
    const fin = ev.final, rows = Object.fromEntries(fin.score.criteria.map((r) => [r.id, r]));
    const secs = Object.fromEntries((fin.score.sections || []).map((s) => [s.id, s]));
    const titles = Object.fromEntries((checklist.sections || []).map((s) => [s.id, s.title]));
    let cur = null;
    const parts = [];
    for (const c of checklist.criteria) {
      if (c.section !== cur && checklist.sections?.length) {
        cur = c.section; const s = secs[cur];
        parts.push(html`<div class="sec"><span>${titles[cur] || cur}</span><span class="num">${s ? `${fmtTotal(s.points)} из ${fmtTotal(s.max_points)}` : c.type === "critical" ? "отдельный флаг" : "не применяется"}</span></div>`);
      }
      parts.push(critBlock(conv, ev, c, rows[c.id]));
    }
    const appeals = ev.appeals;
    const nv = ev.result.votes || 1;
    const agreed = Object.values(ev.result.criteria).filter((d) => (d.confidence ?? 1) === 1).length;
    const meta = ev.status === "needs_review" ? "часть доказательств требует проверки"
      : nv > 1 ? `${nv} прогона модели совпали по ${agreed} из ${checklist.criteria.length} критериев` : "у каждого балла есть реплика-доказательство";
    return html`<header><h2>Баллы</h2><span class="meta ${nv > 1 && agreed < checklist.criteria.length ? "mid-c" : ""}">${meta}</span></header>
      <div class="list-c">${parts}
        ${ev.result.issues?.length ? html`<div class="alert warn"><b>Проверьте доказательства</b>${ev.result.issues.join("; ")}</div>` : ""}
      </div>
      <div class="sum"><div class="row"><span>Итог</span><b class="num ${scoreClass(fin.score.total)}">${Object.keys(fin.changed).length ? html`<span class="was">${fmtTotal(ev.result.score.total)}</span>` : ""}${fmtTotal(fin.score.total)}</b></div>
        <div class="muted" style="font-size:12px">${fin.score.total == null ? "Итог не рассчитывается: все критерии с весом не применяются. " : "Вес «Не применяется» распределён между остальными критериями. "} ${appeals.length ? `Апелляций: ${appeals.length}, решено ${appeals.filter((a) => a.status === "decided").length}.` : ""}</div></div>
      ${fin.score.critical ? html`<div class="alert"><b>Грубое нарушение</b>${checklist.criteria.filter((c) => c.type === "critical" && fin.answers[c.id] === "yes").map((c) => (ev.result.criteria[c.id].violations || []).map((v) => VIOL[v] || c.violations?.find((x) => x.id === v)?.text || v).join(", ") || c.title).join("; ")}. На баллы не влияет, отмечается отдельно.</div>` : ""}
      <div class="meta-line"><span>${ev.result.model}</span><span>чек-лист ${ev.checklist_key}</span><span>${money(ev.result.cost_usd)}</span><span>${ev.result.latency_s ? Math.round(ev.result.latency_s) + " с" : ""}</span><span>${ev.source === "live" ? "оценено в интерфейсе" : ev.source}</span><button class="link" id="trace">Как получена оценка</button></div>`;
  }

  function critBlock(conv, ev, c, row) {
    const d = ev.result.criteria[c.id], a = ev.final.answers[c.id], tone = answerTone(c, a);
    const orig = ev.result.answers[c.id];
    const byId = Object.fromEntries(conv.messages.map((m) => [m.id, m]));
    const evid = (d.evidence_ids || []).filter((id) => byId[id]);
    const ap = ev.appeals.filter((x) => x.criterion_id === c.id);
    const open = ap.find((x) => x.status === "open");
    const decided = ap.filter((x) => x.status === "decided").at(-1);
    const negative = tone !== "ok";
    let pts;
    if (c.type === "critical") pts = html`<span class="tag ${a === "yes" ? "bad" : "ok"}">${answerLabel(c, a)}</span>`;
    else if (a === "na") pts = html`<span class="na-c" style="font-size:14px">Не применяется</span>`;
    else pts = html`<span class="${tone}-c">${fmtTotal(row.points)}</span><small> / ${fmtTotal(row.effective_weight)}</small>`;
    const label = c.type === "scale" && a !== "na" ? `Ступень ${a} из ${Math.max(...c.scale.map((s) => s.value))}` : c.type === "binary" && a !== "na" ? answerLabel(c, a) : "";
    return html`<div class="crit ${c.type === "critical" && a === "yes" ? "critical-hit" : ""}" id="crit-${c.id}">
      <div class="row"><h3>${c.title}</h3>${baseEv?.final && String(baseEv.final.answers[c.id]) !== String(a) ? html`<span class="tag ${answerTone(c, a) === "ok" ? "ok" : "warn"}" title="Оценка исходного диалога">было: ${answerLabel(c, baseEv.final.answers[c.id])}</span>` : ""}${(d.votes || []).length > 1 ? html`<span class="tag ${d.confidence === 1 ? "" : "warn"}" title="Ответы прогонов: ${d.votes.map((v) => answerLabel(c, v)).join(" · ")}">${d.confidence === 1 ? `${d.votes.length}/${d.votes.length}` : c.type === "scale" && d.votes.every((v) => v !== "na") ? `разброс ${Math.min(...d.votes)}–${Math.max(...d.votes)}` : `${d.votes.filter((v) => String(v) === String(orig)).length} из ${d.votes.length}`}</span>` : ""}<span class="pts num">${pts}</span></div>
      <p class="why">${label ? html`<b class="${tone}-c">${label}.</b> ` : ""}${d.reasoning}</p>
      ${d.quote ? html`<span class="quote">«${d.quote}»</span>` : ""}
      ${d.facts ? html`<p class="facts" title="Модель назвала факты, ступень по правилам чек-листа вывел код">Факты: ${factsText(d.facts) || "сбоев и вопросов нет"}</p>` : ""}
      <div class="ev">
        ${evid.length ? html`<button class="link" data-jump="${evid[0]}" data-hl="${evid.join(",")}">Перейти к ${tsOf(conv, byId[evid[0]])}</button>` : ""}
        ${evid.length > 1 ? html`<span class="muted" style="font-size:12px">ещё ${raw(evid.slice(1).map((id) => `<button class="link" data-jump="${esc(id)}">${esc(id)}</button>`).join(", "))}</span>` : ""}
        ${(negative || a === "na") && d.checked_range ? html`<button class="link" style="color:var(--na)" data-range="${d.checked_range}">Проверенный участок ${d.checked_range}</button>` : ""}
        ${negative && a !== "na" && c.type !== "critical" ? html`<button class="link coach" data-coach="${c.id}">Как надо было ответить</button>` : ""}
        ${!open && role.get() === "operator" ? html`<button class="btn small appeal-btn" data-appeal="${c.id}">Оспорить</button>` : ""}
        ${open && role.get() === "reviewer" ? html`<a class="btn small primary appeal-btn" href="#/appeals?id=${open.id}">Решить</a>` : ""}
      </div>
      ${open ? html`<div class="changed"><b>Апелляция ждёт решения.</b> ${open.comment}</div>` : ""}
      ${decided ? html`<div class="changed"><b>${decided.action === "change" ? `Проверяющий изменил: ${answerLabel(c, orig)} → ${answerLabel(c, a)}.` : "Проверяющий подтвердил оценку."}</b> ${decided.reason}</div>` : ""}
    </div>`;
  }

  function jump(id, hl) {
    const el = document.getElementById(id);
    if (!el) return;
    document.querySelectorAll(".msg.flash,.msg.range").forEach((m) => m.classList.remove("flash", "range"));
    (hl || [id]).forEach((x) => document.getElementById(x)?.classList.add("flash"));
    el.scrollIntoView({ behavior: matchMedia("(prefers-reduced-motion: reduce)").matches ? "auto" : "smooth", block: "center" });
    const audio = document.getElementById("audio");
    if (audio) { audio.currentTime = Number(el.dataset.t) || 0; audio.play().catch(() => {}); }
    setTimeout(() => document.querySelectorAll(".msg.flash").forEach((m) => m.classList.remove("flash")), 2600);
  }

  function wire(conv, ev, checklist) {
    root.querySelector("#hist")?.addEventListener("change", (e) => { evIndex = Number(e.target.value); paint(); });
    root.querySelector("#ck").onchange = (e) => { pick = e.target.value; };
    root.querySelector("#eval").onclick = async (e) => {
      e.target.disabled = true;
      try {
        await api("/jobs", { dataset_id: ds, conversation_ids: [cid], checklist_key: pick, votes: Number(localStorage.getItem("cqa-votes")) || undefined });
        toast("Оценка запущена"); watchJobs();
      }
      catch (x) { toast(x.message, true); e.target.disabled = false; }
    };
    root.querySelectorAll("[data-jump]").forEach((b) => (b.onclick = () => jump(b.dataset.jump, b.dataset.hl?.split(","))));
    root.querySelectorAll("[data-range]").forEach((b) => (b.onclick = () => {
      const ids = rangeIds(b.dataset.range, conv);
      if (!ids.length) return toast("Участок не найден в транскрипте", true);
      jump(ids[0], []);
      ids.forEach((x) => document.getElementById(x)?.classList.add("range"));
    }));
    root.querySelectorAll("[data-seek]").forEach((b) => (b.onclick = () => { const a = document.getElementById("audio"); a.currentTime = Number(b.dataset.seek); a.play().catch(() => {}); }));
    root.querySelectorAll("[data-appeal]").forEach((b) => (b.onclick = () => appealDialog(ev, checklist, b.dataset.appeal)));
    root.querySelector("#trace")?.addEventListener("click", () => traceDialog(ev, checklist));
    root.querySelectorAll("[data-coach]").forEach((b) => (b.onclick = () => coachDialog(ev, checklist, b.dataset.coach)));
    root.querySelectorAll("[data-edit]").forEach((b) => (b.onclick = () => {
      const m = conv.messages.find((x) => x.id === b.dataset.edit);
      editDialog(ev, { target_id: m.id, mode: "replace", text: m.text, original_text: m.text, title: `Изменить реплику ${m.id}`,
        hint: "Правка попадёт в копию диалога в наборе «Песочница», исходный диалог не меняется. Копия сразу оценивается, и видно, какие баллы сдвинулись." });
    }));
  }

  async function traceDialog(ev, checklist) {
    const t = await api(`/evaluations/${ev.id}/trace`);
    const titles = Object.fromEntries(checklist.criteria.map((c) => [c.id, c]));
    const pretty = (txt) => { try { return JSON.stringify(JSON.parse(txt), null, 2); } catch { return txt; } };
    const u = t.usage || {};
    const d = dialog(html`<header><h2>Как получена оценка #${t.evaluation_id}</h2></header><div class="content trace">
      <p class="muted" style="margin:0">${t.source === "live" ? "Оценено в интерфейсе" : t.source} ${new Date(t.created_at).toLocaleString("ru-RU")} · ${t.model} · усилие ${t.effort} · прогонов ${t.votes} · вызовов API ${t.calls.length} · ${money(t.cost_usd)} · ${t.latency_s ? Math.round(t.latency_s) + " с" : ""}<br>
        Токены: вход ${u.input_tokens ?? "—"}, из кэша ${u.cache_read_input_tokens ?? "—"}, запись кэша ${u.cache_creation_input_tokens ?? "—"}, выход ${u.output_tokens ?? "—"} · чек-лист ${t.checklist_key} · промпт ${t.prompt_hash}</p>
      <div class="seg" role="tablist">${["Что посчитал код", "Ответы модели", "Что получила модель"].map((x, i) => html`<button type="button" data-tab="${i}" aria-pressed="${String(i === 0)}">${x}</button>`)}</div>
      <section data-pane="0">
        <p class="muted" style="margin:0 0 8px">Ступени этих критериев выводит код из фактов, которые назвала модель. Итог и перераспределение веса N/A тоже считает код.</p>
        ${Object.entries(t.derived).map(([cid, x]) => html`<div class="trace-row"><b>${titles[cid]?.title || cid}: ${answerLabel(titles[cid], x.answer)}</b>
          <span class="muted">${factsText(x.facts) || "фактов нет"}${String(x.model_answer) !== String(x.answer) ? ` · модель предлагала ${answerLabel(titles[cid], x.model_answer)}` : ""}</span></div>`)}
        <pre class="code">${JSON.stringify(t.facts, null, 2)}</pre>
        ${t.issues?.length ? html`<div class="alert warn" style="margin:8px 0 0"><b>Замечания проверки доказательств</b>${t.issues.join("; ")}</div>` : ""}
      </section>
      <section data-pane="1" hidden>${t.calls.length ? t.calls.map((c, i) => html`<details ${raw(i === 0 ? "open" : "")}><summary>Вызов ${i + 1} · ${c.model} · ${c.stop_reason} · вход ${c.usage?.input_tokens + (c.usage?.cache_read_input_tokens || 0)} / выход ${c.usage?.output_tokens} токенов</summary><pre class="code">${pretty(c.text)}</pre></details>`)
        : html`<p class="muted">Сырые ответы не сохранены: оценка импортирована из прогона CLI, его полный журнал — в data/runs.</p>`}</section>
      <section data-pane="2" hidden><details open><summary>Инструкция, собранная из чек-листа ${t.checklist_key}</summary><pre class="code">${t.system_prompt}</pre></details>
        <details><summary>Сообщение с диалогом и фактами</summary><pre class="code">${t.user_message}</pre></details></section>
      </div><footer><button class="btn" value="c">Закрыть</button></footer>`);
    d.classList.add("wide");
    d.querySelector('[value="c"]').onclick = () => d.close();
    d.querySelectorAll("[data-tab]").forEach((b) => (b.onclick = () => {
      d.querySelectorAll("[data-tab]").forEach((x) => x.setAttribute("aria-pressed", String(x === b)));
      d.querySelectorAll("[data-pane]").forEach((p) => (p.hidden = p.dataset.pane !== b.dataset.tab));
    }));
  }

  async function coachDialog(ev, checklist, critId) {
    const c = checklist.criteria.find((x) => x.id === critId);
    const d = dialog(html`<header><h2>Как надо было ответить</h2></header><div class="content">
      <p class="muted" style="margin:0">${c.title}: модель пишет реплику оператора, которая закрыла бы проблему по этому критерию.</p>
      <div class="run" role="status"><span class="dot live"></span><div><b>Пишем реплику…</b><small>5–15 секунд</small></div></div></div>
      <footer><button class="btn ghost" value="c">Закрыть</button></footer>`);
    d.querySelector('[value="c"]').onclick = () => d.close();
    try {
      const tip = await api(`/evaluations/${ev.id}/coach`, { criterion_id: critId });
      d.close();
      editDialog(ev, { ...tip, title: "Как надо было ответить", hint: tip.why, coachCost: tip.cost_usd });
    } catch (x) {
      d.querySelector(".content").innerHTML = String(html`<div class="alert"><b>Не получилось</b>${x.message}</div>`);
    }
  }

  function editDialog(ev, s) {
    const d = dialog(html`<header><h2>${s.title}</h2></header><div class="content">
      <p class="muted" style="margin:0">${s.hint}</p>
      ${s.mode === "replace" && s.original_text ? html`<div class="was-text"><span class="muted">Было (${s.target_id})</span><br>${s.original_text}</div>`
        : html`<p class="muted" style="margin:0">Новая реплика оператора — после ${s.target_id}.</p>`}
      <label class="field">${s.mode === "replace" ? "Стало" : "Реплика оператора"}<textarea class="input" name="t" style="min-height:96px">${s.text}</textarea></label>
      <p class="muted" style="margin:0;font-size:12.5px">«Проверить» оценит копию диалога с этой репликой той же версией чек-листа${s.coachCost ? ` · подсказка стоила ${money(s.coachCost)}` : ""}.</p>
      <div class="err-text"></div></div>
      <footer><button class="btn ghost" value="c">Закрыть</button><button class="btn primary" value="ok">Проверить: переоценить копию</button></footer>`);
    d.classList.add("wide");
    const err = d.querySelector(".err-text");
    d.querySelector('[value="c"]').onclick = () => d.close();
    d.querySelector('[value="ok"]').onclick = async (e) => {
      e.target.disabled = true; err.textContent = "";
      try {
        const r = await api(`/evaluations/${ev.id}/variant`, { target_id: s.target_id, mode: s.mode, text: d.querySelector("[name=t]").value,
          note: s.criterion_id ? `подсказка по критерию «${(data.checklists[ev.checklist_key].criteria.find((x) => x.id === s.criterion_id) || {}).title}»` : "ручная правка",
          votes: Number(localStorage.getItem("cqa-votes")) || undefined });
        d.close(); toast("Копия создана, идёт оценка"); watchJobs();
        location.hash = `#/c/${encodeURIComponent(r.dataset_id)}/${encodeURIComponent(r.conversation_id)}`;
      } catch (x) { err.textContent = x.message; e.target.disabled = false; }
    };
  }

  function appealDialog(ev, checklist, critId) {
    const d = dialog(html`<header><h2>Оспорить оценку</h2></header><div class="content">
      <label class="field">Критерий<select class="input" name="c">${checklist.criteria.filter((c) => !ev.appeals.some((a) => a.criterion_id === c.id && a.status === "open"))
        .map((c) => html`<option value="${c.id}" ${raw(c.id === critId ? "selected" : "")}>${c.title} — ${answerLabel(c, ev.final.answers[c.id])}</option>`)}</select></label>
      <label class="field">Что не так с оценкой<textarea class="input" name="t" placeholder="Например: оператор назвал имя в первой реплике, m4" autofocus></textarea></label>
      <p class="muted" style="margin:0;font-size:12.5px">Проверяющий увидит спорную оценку, ваш комментарий и фрагмент диалога.</p>
      <div class="err-text"></div></div>
      <footer><button class="btn ghost" value="c">Отмена</button><button class="btn primary" value="ok">Отправить апелляцию</button></footer>`);
    const err = d.querySelector(".err-text");
    d.querySelector('[value="c"]').onclick = () => d.close();
    d.querySelector('[value="ok"]').onclick = async (e) => {
      e.target.disabled = true; err.textContent = "";
      try {
        await api("/appeals", { evaluation_id: ev.id, criterion_id: d.querySelector("[name=c]").value, comment: d.querySelector("[name=t]").value });
        d.close(); toast("Апелляция отправлена проверяющему");
        window.dispatchEvent(new Event("cqa:changed"));
        data = await api(`/conversations/${encodeURIComponent(ds)}/${encodeURIComponent(cid)}`); paint();
      } catch (x) { err.textContent = x.message; e.target.disabled = false; }
    };
  }

  paint();

  let seen = null;
  const off = onJobs(async (jobs) => {
    const job = jobs.find((j) => j.dataset_id === ds && j.status === "running");
    const el = root.querySelector("#run");
    if (job && el) el.innerHTML = String(html`<div class="run" role="status"><span class="dot live"></span><div><b>Модель оценивает диалог</b><small>Обычно 20–40 секунд. Результат появится здесь сам.</small></div></div>`);
    const count = (await api(`/conversations/${encodeURIComponent(ds)}/${encodeURIComponent(cid)}`)).evaluations.length;
    if (seen !== null && count !== seen) { data = await api(`/conversations/${encodeURIComponent(ds)}/${encodeURIComponent(cid)}`); evIndex = 0; paint(); toast("Оценка готова"); }
    seen = count;
    if (!job && el) el.innerHTML = "";
  });
  watchJobs();
  return off;
}

export { esc };
