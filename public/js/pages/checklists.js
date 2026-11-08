import { api, html, raw, hero, toast, dialog, store, confirmDialog } from "../core.js";

const TYPES = [["binary", "Да или нет"], ["scale", "Шкала"], ["critical", "Грубая ошибка"]];
const clone = (x) => JSON.parse(JSON.stringify(x));

function problems(body) {
  const out = [];
  const sum = body.criteria.filter((c) => c.type !== "critical").reduce((s, c) => s + (Number(c.weight) || 0), 0);
  if (Math.abs(sum - 100) > 1e-9) out.push(`сумма весов ${sum}, должна быть 100`);
  if (!body.name?.trim()) out.push("нет названия чек-листа");
  for (const c of body.criteria) {
    const n = c.title?.trim() || c.id;
    if (!c.title?.trim()) out.push(`${c.id}: нет названия`);
    if (c.na_allowed && !c.na_when?.trim()) out.push(`${n}: опишите, когда критерий не применяется`);
    if (c.type === "scale") {
      const v = c.scale.map((s) => s.value);
      if (v.length < 2) out.push(`${n}: у шкалы нужно минимум две ступени`);
      if (v.some((x) => !Number.isInteger(x))) out.push(`${n}: ступени шкалы — целые числа`);
      if (new Set(v).size !== v.length) out.push(`${n}: ступени шкалы повторяются`);
    }
    if (c.type === "binary" && !c.yes_when?.trim() && !c.guidance?.length) out.push(`${n}: опишите, когда ставить «Да»`);
    if (c.type === "critical" && !c.violations?.length) out.push(`${n}: перечислите нарушения`);
  }
  return out;
}

export default async function checklists(root, params) {
  let versions = await api("/checklists");
  const active = versions.find((v) => v.active);
  let key = params.key || active.key;
  let saved = await api("/checklists/" + encodeURIComponent(key));
  let body = clone(saved);
  let sel = body.criteria[0]?.id;
  const dirty = () => JSON.stringify(body) !== JSON.stringify(saved);

  function paint() {
    const isActive = versions.find((v) => v.key === key)?.active;
    const sum = body.criteria.filter((c) => c.type !== "critical").reduce((s, c) => s + (Number(c.weight) || 0), 0);
    const sections = body.sections?.length ? body.sections : [{ id: null, title: "Критерии" }];
    root.innerHTML = String(html`
      ${hero({ title: body.name || "Чек-лист", art: "checklist",
        tag: html`<span class="tag ${isActive ? "ok" : ""}" style="vertical-align:middle;font-size:13px">${dirty() ? "черновик новой версии" : isActive ? `версия ${saved.version}, активна` : `версия ${saved.version}`}</span>`,
        text: "Сохранение создаёт новую версию. Старые оценки остаются со своей версией — их видно в карточке и можно переоценить.",
        actions: html`<button class="btn ghost" id="prompt" title="Инструкция для модели, собранная из сохранённой версии">Что получит модель</button><button class="btn" id="add">Новый критерий</button><button class="btn primary" id="apply" ${raw(dirty() ? "disabled title='Сначала сохраните версию'" : "")}>Применить к набору</button>` })}
      <div class="cl">
        <div class="grid">
          <section class="panel cl-list" aria-label="Критерии">
            ${sections.map((s) => html`<div class="group">${s.title}</div>${body.criteria.filter((c) => !s.id || c.section === s.id).map((c) => html`
              <button class="cl-item" data-id="${c.id}" aria-current="${String(c.id === sel)}"><span class="t"><b>${c.title || "Без названия"}</b>
                <small>${c.type === "scale" ? `шкала ${Math.min(...c.scale.map((x) => x.value))}–${Math.max(...c.scale.map((x) => x.value))}` : c.type === "critical" ? "флаг без баллов" : "да или нет"}${c.na_allowed ? ", можно не применять" : ""}</small></span>
                <span class="w num">${c.type === "critical" ? "—" : c.weight}</span></button>`)}`)}
            <div class="cl-sum"><span>Сумма весов</span><b class="num ${sum === 100 ? "ok-c" : "bad-c"}">${sum}</b></div>
          </section>
          <section class="panel"><header><h2>Версии</h2></header><div class="versions">${versions.map((v) => html`
            <div class="version"><div style="flex:1"><b>${v.name}</b> <span class="muted">v${v.version}</span><br><small class="muted">${v.note || ""}${v.note ? " · " : ""}оценок ${v.evaluations}</small></div>
            ${v.active ? html`<span class="tag ok">активна</span>` : html`<button class="btn small" data-activate="${v.key}">Сделать активной</button>`}
            ${v.key !== key ? html`<a class="link" href="#/checklists/${encodeURIComponent(v.key)}">открыть</a>` : ""}</div>`)}</div></section>
        </div>
        <div class="grid">
          <section class="panel"><header><h2>Чек-лист</h2></header><div class="body form">
            <label class="field">Название<input class="input" data-k="name" value="${body.name}"></label>
          </div></section>
          <section class="panel" id="editor">${editor()}</section>
        </div>
      </div>
      <div id="bar"></div>`);
    wire();
    paintBar();
  }

  function editor() {
    const c = body.criteria.find((x) => x.id === sel);
    if (!c) return html`<div class="empty"><b>Нет критериев</b><button class="btn primary" id="add2">Добавить критерий</button></div>`;
    return html`<header><h2>${c.title || "Новый критерий"}</h2><span class="meta">${c.id}</span></header><div class="body form">
      <label class="field">Название<input class="input" data-c="title" value="${c.title}" placeholder="Например: Предложил мобильное приложение"></label>
      ${body.sections?.length ? html`<label class="field">Секция<select class="input" data-c="section">${body.sections.map((s) => html`<option value="${s.id}" ${raw(s.id === c.section ? "selected" : "")}>${s.title}</option>`)}</select></label>` : ""}
      <div class="two">
        <div class="field"><span>Тип ответа</span><div class="seg" role="group" aria-label="Тип ответа">${TYPES.map(([t, l]) => html`<button type="button" data-type="${t}" aria-pressed="${String(c.type === t)}">${l}</button>`)}</div></div>
        <label class="field">Вес<input class="input num" type="number" min="0" step="1" data-c="weight" value="${c.type === "critical" ? 0 : c.weight}" ${raw(c.type === "critical" ? "disabled" : "")}></label>
        <div class="field"><span>Можно не применять</span><label class="switch"><input type="checkbox" data-c="na_allowed" ${raw(c.na_allowed ? "checked" : "")} ${raw(c.type === "critical" ? "disabled" : "")}>${c.na_allowed ? "да" : "нет"}</label></div>
      </div>
      ${c.type === "binary" ? html`
        <label class="field">Когда «Да»<textarea class="input" data-c="yes_when">${c.yes_when || ""}</textarea></label>
        <label class="field">Когда «Нет»<textarea class="input" data-c="no_when">${c.no_when || ""}</textarea></label>` : ""}
      ${c.type === "scale" ? html`<div class="field"><span>Ступени шкалы: балл и когда его ставить</span><div class="steps">${c.scale.map((s, i) => html`
        <div class="step"><input class="input num" type="number" step="1" data-step="${i}" data-f="value" value="${s.value}" aria-label="Балл"><input class="input" data-step="${i}" data-f="text" value="${s.text}" aria-label="Когда ставить"><button class="icon-btn" data-delstep="${i}" aria-label="Убрать ступень">×</button></div>`)}
        <div><button class="btn small" id="addstep">Добавить ступень</button></div></div></div>` : ""}
      ${c.type === "critical" ? html`<div class="field"><span>Нарушения: любое из них — грубая ошибка</span><div class="lines">${(c.violations || []).map((v, i) => html`
        <div class="line"><input class="input" data-viol="${i}" value="${v.text}"><button class="icon-btn" data-delviol="${i}" aria-label="Убрать">×</button></div>`)}
        <div><button class="btn small" id="addviol">Добавить нарушение</button></div></div></div>` : ""}
      ${c.na_allowed && c.type !== "critical" ? html`<label class="field">Когда не применяется<textarea class="input" data-c="na_when">${c.na_when || ""}</textarea></label>` : ""}
      <div class="field"><span>Правила толкования для модели — по одному на строку</span><textarea class="input" data-c="guidance" style="min-height:110px">${(c.guidance || []).join("\n")}</textarea></div>
      <div style="display:flex;justify-content:space-between;gap:8px"><span class="muted" style="font-size:12.5px">Модель получает формулировки критерия дословно. Пишите так, как объяснили бы новому проверяющему.</span>
        <button class="btn small danger" id="del">Удалить критерий</button></div>
    </div>`;
  }

  function paintBar() {
    const bar = root.querySelector("#bar");
    if (!dirty()) { bar.innerHTML = ""; return; }
    const errs = problems(body);
    bar.innerHTML = String(html`<div class="dirty-bar" role="region" aria-label="Несохранённые изменения">
      <div class="err-text" style="color:${errs.length ? "var(--red)" : "var(--ink2)"}">${errs.length ? "Чтобы сохранить: " + errs.join("; ") : "Изменения не сохранены. Новая версия станет активной для следующих оценок."}</div>
      <input class="input" id="note" placeholder="Что изменилось (необязательно)" style="max-width:280px">
      <button class="btn ghost" id="reset">Отменить</button><button class="btn primary" id="save" ${raw(errs.length ? "disabled" : "")}>Сохранить версию</button></div>`);
    bar.querySelector("#reset").onclick = () => { body = clone(saved); sel = body.criteria.find((c) => c.id === sel)?.id || body.criteria[0]?.id; paint(); };
    bar.querySelector("#save").onclick = save;
  }

  async function save() {
    try {
      const r = await api("/checklists", { body, note: root.querySelector("#note").value, activate: true });
      versions = await api("/checklists");
      key = r.key; saved = r.checklist; body = clone(saved);
      history.replaceState(null, "", "#/checklists/" + encodeURIComponent(key));
      paint();
      toast(`Сохранена версия ${saved.version}, она активна`);
      applyDialog();
    } catch (e) { toast(e.message, true); }
  }

  async function applyDialog() {
    const st = await api("/state");
    const d = dialog(html`<header><h2>Применить версию ${saved.version}</h2></header><div class="content">
      <p class="muted" style="margin:0">Модель заново оценит диалоги набора по новому чек-листу. Прежние оценки останутся в истории карточки.</p>
      <label class="field">Набор<select class="input" name="ds">${st.datasets.map((x) => html`<option value="${x.id}" ${raw(x.id === store.dataset ? "selected" : "")}>${x.name}</option>`)}</select></label>
      <label class="field">Какие диалоги<select class="input" name="scope"><option value="missing">Ещё не оценённые этой версией</option><option value="evaluated">Только уже оценённые раньше</option></select></label>
      ${!st.has_key ? html`<div class="alert warn" style="margin:0" data-key-notice><b>Для оценки нужен ваш ключ Claude API</b>При запуске откроется поле для ввода ключа.</div>` : ""}
      <div class="err-text"></div></div>
      <footer><button class="btn ghost" value="c">Позже</button><button class="btn primary" value="ok">Оценить</button></footer>`);
    const err = d.querySelector(".err-text");
    d.querySelector('[value="c"]').onclick = () => d.close();
    d.querySelector('[value="ok"]').onclick = async (e) => {
      e.target.disabled = true; err.textContent = "";
      const ds = d.querySelector("[name=ds]").value;
      try {
        let ids = null;
        if (d.querySelector("[name=scope]").value === "evaluated") {
          const rows = await api(`/datasets/${encodeURIComponent(ds)}/conversations`);
          ids = rows.filter((r) => r.evaluation && r.evaluation.checklist_key !== key).map((r) => r.conversation_id);
          if (!ids.length) throw new Error("В наборе нет диалогов, оценённых другой версией");
        }
        await api("/jobs", { dataset_id: ds, conversation_ids: ids, checklist_key: key, only_missing: true });
        store.dataset = ds; d.close(); toast("Оценка по новой версии запущена"); location.hash = "#/queue";
      } catch (x) { err.textContent = x.message; e.target.disabled = false; }
    };
  }

  function nextId() {
    const n = Math.max(0, ...body.criteria.map((c) => Number(String(c.id).replace(/\D/g, "")) || 0)) + 1;
    return "c" + String(n).padStart(2, "0");
  }

  function wire() {
    root.querySelectorAll(".cl-item").forEach((b) => (b.onclick = () => { sel = b.dataset.id; paint(); }));
    const add = () => {
      const c = { id: nextId(), section: body.sections?.at(-2)?.id || body.sections?.[0]?.id, title: "", type: "binary", weight: 0, na_allowed: false,
        yes_when: "", no_when: "", na_when: null, guidance: [] };
      body.criteria.splice(body.criteria.filter((x) => x.type !== "critical").length, 0, c);
      sel = c.id; paint(); root.querySelector('[data-c="title"]')?.focus();
    };
    root.querySelector("#add").onclick = add;
    root.querySelector("#add2")?.addEventListener("click", add);
    root.querySelector("#apply").onclick = applyDialog;
    root.querySelector("#prompt").onclick = async () => {
      const r = await api(`/checklists/${encodeURIComponent(key)}/prompt`);
      const d = dialog(html`<header><h2>Что получит модель · версия ${saved.version}</h2></header><div class="content">
        <p class="muted" style="margin:0">Инструкция собирается из сохранённой версии чек-листа при каждой оценке: новый или изменённый критерий попадает сюда и в схему ответа без правки кода.${dirty() ? " Несохранённые правки сюда ещё не вошли." : ""}</p>
        <pre class="code">${r.system_prompt}</pre></div><footer><button class="btn" value="c">Закрыть</button></footer>`);
      d.classList.add("wide");
      d.querySelector('[value="c"]').onclick = () => d.close();
    };
    root.querySelector('[data-k="name"]').oninput = (e) => { body.name = e.target.value; paintBar(); };
    root.querySelectorAll("[data-activate]").forEach((b) => (b.onclick = async () => {
      await api(`/checklists/${encodeURIComponent(b.dataset.activate)}/activate`, {}); versions = await api("/checklists"); paint(); toast("Активная версия изменена");
    }));
    const c = body.criteria.find((x) => x.id === sel);
    if (!c) return;
    root.querySelectorAll("[data-c]").forEach((el) => {
      const k = el.dataset.c;
      const ev = el.type === "checkbox" || el.tagName === "SELECT" ? "change" : "input";
      el.addEventListener(ev, () => {
        if (k === "na_allowed") { c.na_allowed = el.checked; if (c.na_allowed && !c.na_when) c.na_when = ""; return paint(); }
        if (k === "weight") c.weight = el.value === "" ? 0 : Number(el.value);
        else if (k === "guidance") c.guidance = el.value.split("\n").map((s) => s.trim()).filter(Boolean);
        else c[k] = el.value;
        if (k === "title") { root.querySelector(`.cl-item[data-id="${c.id}"] b`).textContent = c.title || "Без названия"; root.querySelector("#editor h2").textContent = c.title || "Новый критерий"; }
        if (k === "weight") { const s = body.criteria.filter((x) => x.type !== "critical").reduce((a, x) => a + (Number(x.weight) || 0), 0); const sb = root.querySelector(".cl-sum b"); sb.textContent = s; sb.className = "num " + (s === 100 ? "ok-c" : "bad-c"); root.querySelector(`.cl-item[data-id="${c.id}"] .w`).textContent = c.weight; }
        paintBar();
      });
    });
    root.querySelectorAll("[data-type]").forEach((b) => (b.onclick = () => {
      const t = b.dataset.type; if (t === c.type) return;
      c.type = t;
      if (t === "scale" && !c.scale?.length) c.scale = [{ value: c.weight || 10, text: "Полностью выполнено." }, { value: Math.round((c.weight || 10) / 2), text: "Выполнено частично." }, { value: 0, text: "Не выполнено." }];
      if (t !== "scale") delete c.scale;
      if (t === "critical") { c.weight = 0; c.na_allowed = false; c.na_when = null; c.violations ||= [{ id: "v1", text: "" }]; c.yes_when ||= "Выявлено хотя бы одно из нарушений."; c.no_when ||= "Нарушений нет."; }
      else delete c.violations;
      paint();
    }));
    root.querySelectorAll("[data-step]").forEach((el) => (el.oninput = () => {
      const s = c.scale[Number(el.dataset.step)];
      s[el.dataset.f] = el.dataset.f === "value" ? (el.value === "" ? NaN : Number(el.value)) : el.value; paintBar();
    }));
    root.querySelectorAll("[data-delstep]").forEach((b) => (b.onclick = () => { c.scale.splice(Number(b.dataset.delstep), 1); paint(); }));
    root.querySelector("#addstep")?.addEventListener("click", () => { c.scale.push({ value: 0, text: "" }); paint(); });
    root.querySelectorAll("[data-viol]").forEach((el) => (el.oninput = () => { c.violations[Number(el.dataset.viol)].text = el.value; paintBar(); }));
    root.querySelectorAll("[data-delviol]").forEach((b) => (b.onclick = () => { c.violations.splice(Number(b.dataset.delviol), 1); paint(); }));
    root.querySelector("#addviol")?.addEventListener("click", () => { c.violations.push({ id: "v" + (c.violations.length + 1), text: "" }); paint(); });
    root.querySelector("#del").onclick = async () => {
      if (!(await confirmDialog("Удалить критерий?", `«${c.title || c.id}» исчезнет из новой версии. Сохранённые оценки не изменятся.`, "Удалить"))) return;
      body.criteria = body.criteria.filter((x) => x.id !== c.id); sel = body.criteria[0]?.id; paint();
    };
  }

  paint();
  const guard = (e) => { if (dirty()) { e.preventDefault(); e.returnValue = ""; } };
  window.addEventListener("beforeunload", guard);
  return () => window.removeEventListener("beforeunload", guard);
}
