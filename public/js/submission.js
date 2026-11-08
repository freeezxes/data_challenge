import { preferences, API, api, html, raw, dialog, store, checklistPicker } from "./core.js";

export async function submissionDialog(checklistKey) {
  const dataset = store.dataset;
  const [versions, state] = await Promise.all([api("/checklists"), api("/state")]);
  const selected = state.datasets.find(x => x.id === dataset);
  const d = dialog(html`<header><div><h2>Выгрузить для сдачи</h2><p class="muted">JSON 1.0 по формату организаторов. Проверим каждый диалог.</p></div><button class="btn ghost" data-close aria-label="Закрыть выгрузку">Закрыть</button></header>
    <div class="content">
      <p><b>Набор: ${selected?.name || dataset}</b>${selected ? ` · диалогов: ${selected.count}` : ""}</p>
      <label class="field">Название команды<input class="input" name="team" maxlength="160" value="${preferences.getItem("cqa-team") || ""}" placeholder="Название из заявки" autocomplete="organization"></label>
      <div class="transfer-two"><label class="field">Набор для сдачи<select class="input" name="kind"><option value="prod" ${raw(dataset === "DEV" ? "" : "selected")}>Контроль · 10 чатов</option><option value="dev" ${raw(dataset === "DEV" ? "selected" : "")}>DEV · 65 диалогов</option></select></label>
        <label class="field">Версия чек-листа ${checklistPicker(versions, checklistKey || versions.find(v => v.active)?.key, "submission-checklist")}</label></div>
      <p class="muted">Скачивается текущий набор из очереди. Решения по апелляциям включаются в итог; исходные оценки сохраняются в приложении.</p>
      <div data-report aria-live="polite"></div><p class="err-text" role="alert"></p>
    </div><footer><button class="btn ghost" data-close>Отмена</button><button class="btn primary" data-check>Проверить выгрузку</button><a class="btn primary" data-download download="results.json" hidden>Скачать results.json</a></footer>`);
  d.classList.add("transfer-dialog");
  const team = d.querySelector('[name="team"]'), kind = d.querySelector('[name="kind"]'), ck = d.querySelector("#submission-checklist");
  const button = d.querySelector("[data-check]"), download = d.querySelector("[data-download]"), report = d.querySelector("[data-report]"), error = d.querySelector('[role="alert"]');
  const reset = () => { report.innerHTML = ""; error.textContent = ""; download.hidden = true; button.hidden = false; };
  for (const el of [team, kind, ck]) el.addEventListener("input", reset);
  d.querySelectorAll("[data-close]").forEach(b => b.onclick = () => d.close());
  button.onclick = async () => {
    error.textContent = ""; button.disabled = true; button.textContent = "Проверяем…";
    const params = new URLSearchParams({ team_name: team.value.trim(), kind: kind.value, checklist: ck.value });
    for (const el of [team, kind, ck]) el.disabled = true;
    try {
      const r = await api(`/datasets/${encodeURIComponent(dataset)}/submission?${params}`);
      if (!d.open) return;
      preferences.setItem("cqa-team", team.value.trim());
      report.innerHTML = String(html`<div class="transfer-summary"><b>Готово ${r.ready} из ${r.total}</b><span>${r.name}</span></div>
        ${r.blockers.length ? html`<div class="alert"><b>Выгрузка заблокирована</b><ul>${r.blockers.map(x => html`<li>${x}</li>`)}</ul></div>` : html`<p>Сохраните файл в репозитории: <strong>${r.path}</strong></p>`}
        ${r.errors ? html`<div class="alert warn"><b>Диалогов с ошибкой: ${r.errors}</b>Они останутся в файле с status=error, пустыми критериями и score=null.</div>
          <ul class="submission-errors">${r.rows.filter(x => x.error).map(x => html`<li><b>${x.id}</b><span>${x.error}</span></li>`)}</ul>
          ${!r.blockers.length ? html`<label class="transfer-check"><input type="checkbox" data-errors>Выгрузить также ${r.errors} диалогов с ошибками</label>` : ""}` : html`<p class="ok-c">Все оценки, значения, обоснования и ссылки на реплики проверены.</p>`}
        <p class="muted">После скачивания нужны commit и push. Приложение не отправляет результаты организаторам автоматически.</p>`);
      const sync = () => {
        const allowed = !r.blockers.length && (!r.errors || !!d.querySelector("[data-errors]")?.checked);
        download.hidden = !allowed;
        const out = new URLSearchParams(params); out.set("download", "1");
        if (r.errors) out.set("allow_errors", "1");
        download.href = `${API}/api/datasets/${encodeURIComponent(r.dataset)}/submission?${out}`;
      };
      d.querySelector("[data-errors]")?.addEventListener("change", sync);
      sync(); button.hidden = true;
    } catch (x) { if (d.open) error.textContent = x.message; }
    finally { button.disabled = false; button.textContent = "Проверить выгрузку"; for (const el of [team, kind, ck]) el.disabled = false; }
  };
  team.focus();
}
