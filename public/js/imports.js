import { api, html, raw, dialog, store, toast, CHANNEL } from "./core.js";

const ROLE = { client: "Клиент", operator: "Оператор", bot: "Бот", system: "Событие" };
const dialogs = n => `${n} ${n % 10 === 1 && n % 100 !== 11 ? "диалог" : n % 10 >= 2 && n % 10 <= 4 && !(n % 100 >= 12 && n % 100 <= 14) ? "диалога" : "диалогов"}`;

export async function importDialog(initial = "file") {
  const st = await api("/state");
  const d = dialog("");
  d.classList.add("transfer-dialog");
  let source = initial, name = "", target = "", pasted = "", group = "", file = null, preview = null;

  function inputStep(error = "") {
    preview = null;
    d.innerHTML = String(html`<header><div><h2>Добавить диалоги</h2><p class="muted">Сначала проверим данные и покажем, что будет загружено.</p></div><button class="btn ghost" data-close aria-label="Закрыть импорт">Закрыть</button></header>
      <div class="content">
        <div class="transfer-tabs" role="group" aria-label="Источник диалогов">${[["file", "Файл JSON"], ["paste", "Вставить JSON"], ["api", "API организаторов"]].map(([k, t]) => html`<button class="btn small" data-source="${k}" aria-pressed="${String(source === k)}">${t}</button>`)}</div>
        ${source === "file" ? html`<label class="import-file">Файл с диалогами<input class="input" type="file" accept=".json,application/json" name="file"><span class="muted">${file ? file.name : "Выберите JSON или перетащите его сюда"}</span></label>` : source === "paste" ? html`<label class="field">JSON с диалогами<textarea class="input import-json" name="json" spellcheck="false" placeholder='{"data": {"records": […]}}'>${pasted}</textarea></label>` : html`
          <p class="muted">Введите group_id, который выдали организаторы. Получение данных не запускает оценку.</p>
          ${!st.has_api_url ? html`<div class="alert warn"><b>API пока не настроен</b>Добавьте CQA_API_URL в настройках сервера или загрузите JSON.</div>` : ""}
          <label class="field">group_id<input class="input" name="group" placeholder="Например, DEV" value="${group}" autocomplete="off"></label>`}
        <div class="transfer-destination">
          <label class="field">Куда загрузить<select class="input" name="target"><option value="">Создать отдельный набор</option>${st.datasets.map(x => html`<option value="${x.id}" ${raw(target === x.id ? "selected" : "")}>Добавить в «${x.name}»</option>`)}</select></label>
          <label class="field" data-name ${raw(target ? "hidden" : "")}>Название нового набора<input class="input" name="name" maxlength="160" value="${name}" placeholder="Например, Контрольный набор"></label>
          <p class="muted" data-destination-note>${target ? "Добавление в существующий набор нужно будет подтвердить после проверки." : "Будет создан отдельный набор. Даже совпадающие ID не изменят другие наборы."}</p>
        </div>
        <p class="err-text" role="alert">${error}</p>
      </div><footer><button class="btn ghost" data-close>Отмена</button><button class="btn primary" data-preview ${raw(source === "api" && !st.has_api_url ? "disabled" : "")}>Проверить и посмотреть</button></footer>`);
    wireClose();
    const remember = () => {
      name = d.querySelector('[name="name"]').value.trim();
      target = d.querySelector('[name="target"]').value;
      if (source === "paste") pasted = d.querySelector('[name="json"]').value;
      if (source === "api") group = d.querySelector('[name="group"]').value.trim();
    };
    d.querySelectorAll("[data-source]").forEach(b => b.onclick = () => { remember(); source = b.dataset.source; inputStep(); });
    d.querySelector('[name="target"]').onchange = () => {
      remember(); d.querySelector("[data-name]").hidden = !!target;
      d.querySelector("[data-destination-note]").textContent = target ? "Добавление в существующий набор нужно будет подтвердить после проверки." : "Будет создан отдельный набор. Даже совпадающие ID не изменят другие наборы.";
    };
    if (source === "file") {
      const box = d.querySelector(".import-file");
      d.querySelector('[name="file"]').onchange = e => { file = e.target.files[0] || null; box.querySelector("span").textContent = file?.name || "Выберите JSON или перетащите его сюда"; };
      box.ondragover = e => { e.preventDefault(); box.classList.add("dragging"); };
      box.ondragleave = () => box.classList.remove("dragging");
      box.ondrop = e => { e.preventDefault(); box.classList.remove("dragging"); file = e.dataTransfer.files[0] || null; box.querySelector("span").textContent = file?.name || "Выберите JSON"; };
    }
    d.querySelector("[data-preview]").onclick = async e => {
      remember();
      const err = d.querySelector('[role="alert"]'); err.textContent = "";
      if (source === "file" && !file) { err.textContent = "Выберите файл JSON."; return; }
      if (source === "paste" && !pasted.trim()) { err.textContent = "Вставьте JSON с диалогами."; return; }
      if (source === "api" && !group) { err.textContent = "Введите group_id."; return; }
      if (file && source === "file" && file.size > 45_000_000) { err.textContent = "Файл больше 45 МБ. Разделите его на несколько наборов."; return; }
      const button = e.currentTarget; button.disabled = true; button.textContent = source === "api" ? "Получаем и проверяем…" : "Проверяем…"; d.setAttribute("aria-busy", "true");
      const controls = [...d.querySelectorAll("input, select, textarea, [data-source]")];
      controls.forEach(x => x.disabled = true);
      try {
        const body = { name: name || (source === "file" ? file.name.replace(/\.json$/i, "") : ""), append_to: target || null };
        preview = await api(source === "api" ? "/imports/fetch-preview" : "/imports/preview", source === "api" ? { ...body, group_id: group } : { ...body, payload: source === "file" ? await file.text() : pasted });
        if (d.open) previewStep();
      } catch (x) { if (d.open) { err.textContent = x.message; button.disabled = false; button.textContent = "Проверить и посмотреть"; } }
      finally { d.removeAttribute("aria-busy"); controls.forEach(x => x.disabled = false); }
    };
    d.querySelector(source === "paste" ? '[name="json"]' : source === "api" ? '[name="group"]' : '[name="file"]')?.focus();
  }

  function previewStep() {
    const p = preview;
    d.innerHTML = String(html`<header><div><h2>Проверка импорта</h2><p class="muted">${p.append_to ? `Добавление в «${p.name}»` : `Новый отдельный набор «${p.name}»`}</p></div><button class="btn ghost" data-close aria-label="Закрыть предпросмотр">Закрыть</button></header>
      <div class="content">
        <div class="transfer-summary" role="status"><b>${dialogs(p.total)}</b><span>${Object.entries(p.languages).map(([k,v]) => `${k}: ${v}`).join(" · ")}</span><span>${Object.entries(p.channels).map(([k,v]) => `${CHANNEL[k]}: ${v}`).join(" · ")}</span></div>
        ${p.source_group ? html`<p class="muted">Набор из источника: ${p.source_group}</p>` : ""}
        ${p.errors.length ? html`<div class="alert"><b>Исправьте ошибки в файле</b><ul>${p.errors.slice(0, 20).map(x => html`<li>${x}</li>`)}</ul>${p.errors.length > 20 ? html`<p>Всего ошибок: ${p.errors.length}. Показаны первые 20.</p>` : ""}</div>` : html`<p class="ok-c">Структура проверена. Тексты, роли, время и порядок реплик сохранятся как в источнике.</p>`}
        ${p.conflicts ? html`<div class="alert warn"><b>Совпадающих ID: ${p.conflicts}${p.changed ? `, с другим содержимым: ${p.changed}` : ""}</b>Существующие диалоги и их оценки сохранятся. Чтобы загрузить изменённые версии, вернитесь назад и создайте отдельный набор.</div>
          <label class="transfer-check"><input type="checkbox" data-skip>Пропустить совпадающие ID (${p.conflicts})</label>` : ""}
        <div class="import-records">${p.rows.slice(0, 30).map(r => html`<details><summary><b>${r.id}</b><span>${r.language} · ${CHANNEL[r.channel]} · реплик: ${r.messages}</span>${r.conflict ? html`<span class="tag warn">${r.changed ? "Другой текст · пропуск" : "Уже есть · пропуск"}</span>` : html`<span class="tag ok">Новый</span>`}</summary><div class="import-messages">${r.sample.map(m => html`<p><small>${m.timestamp} · ${ROLE[m.speaker]}</small><span>${m.text}</span></p>`)}</div></details>`)}</div>
        ${p.rows.length > 30 ? html`<p class="muted">Показаны первые 30 диалогов. Проверены все ${p.total}.</p>` : ""}
        ${p.append_to ? html`<label class="transfer-check"><input type="checkbox" data-append>Добавить ${dialogs(p.new)} в существующий набор «${p.name}»</label>` : ""}
        ${!p.new && !p.errors.length ? html`<p class="muted">Новых диалогов нет. Вернитесь назад, чтобы создать отдельный набор.</p>` : ""}
        <p class="err-text" role="alert"></p>
      </div><footer><button class="btn ghost" data-back>Назад</button><button class="btn primary" data-commit>Импортировать · ${p.new}</button></footer>`);
    wireClose(); d.querySelector("[data-back]").onclick = () => inputStep();
    const button = d.querySelector("[data-commit]");
    const sync = () => { button.disabled = !!p.errors.length || !p.new || (p.conflicts && !d.querySelector("[data-skip]").checked) || (p.append_to && !d.querySelector("[data-append]").checked); };
    d.querySelectorAll('input[type="checkbox"]').forEach(x => x.onchange = sync); sync();
    button.onclick = async () => {
      button.disabled = true; button.textContent = "Сохраняем…";
      try {
        const r = await api("/imports/commit", { token: p.token, skip_existing: !!d.querySelector("[data-skip]")?.checked });
        store.dataset = r.id; d.close(); toast(`Добавлено диалогов: ${r.count}${r.skipped ? ` · пропущено: ${r.skipped}` : ""}`);
        const hash = r.count === 1 ? `#/c/${encodeURIComponent(r.id)}/${encodeURIComponent(r.ids[0])}` : "#/queue";
        if (location.hash === hash) window.dispatchEvent(new HashChangeEvent("hashchange")); else location.hash = hash;
      } catch (x) { if (d.open) { d.querySelector('[role="alert"]').textContent = x.message; button.textContent = "Импортировать · " + p.new; sync(); } }
    };
    d.querySelector("[data-back]").focus();
  }
  function wireClose() { d.querySelectorAll("[data-close]").forEach(b => b.onclick = () => d.close()); }
  inputStep();
}

export const uploadDialog = () => importDialog("file");
export const fetchDialog = () => importDialog("api");
