import { api, html, hero, money, store, toast, dialog } from "../core.js";

export default async function home(root) {
  const st = await api("/state");
  const perDialog = st.price_per_mtok ? "≈ $0,04 за диалог" : "";
  root.innerHTML = String(html`
    ${hero({ title: "Контроль качества диалогов", art: "home",
      text: "Модель проверяет каждый чат и звонок по чек-листу, показывает реплику-доказательство для каждого балла. Спорную оценку можно оспорить, проверяющий решает и итог пересчитывается.",
      actions: html`<a class="btn" href="#/checklists">Чек-лист</a><a class="btn primary" href="#/queue">Открыть очередь</a>` })}
    ${!st.has_key ? html`<div class="alert warn" style="margin:0 0 20px"><b>Оценка выключена: нет ключа Claude API</b>Добавьте ANTHROPIC_API_KEY в файл .env и перезапустите сервер. Сохранённые оценки, апелляции и чек-листы работают и без него.</div>` : ""}
    <div class="home">
      <section class="panel"><header><h2>Наборы диалогов</h2></header><div class="body">
        ${st.datasets.map((d) => html`<div class="dsrow"><div class="t"><b>${d.name}</b><small>оценено ${d.evaluated} из ${d.count}</small></div>
          <a class="btn small" href="#/queue" data-ds="${d.id}">Открыть</a></div>`)}
        <div style="display:flex;gap:8px;flex-wrap:wrap;margin-top:6px">
          <button class="btn small" id="fetch">Загрузить по group_id</button>
          <button class="btn small" id="upload">Загрузить файл JSON</button>
        </div>
      </div></section>
      <section class="panel"><header><h2>Активный чек-лист</h2><span class="meta">${st.active_checklist}</span></header><div class="body">
        <div class="stat" style="font-size:20px">${st.active_checklist_name}</div>
        <p class="muted" style="margin:0">Новые оценки идут по этой версии. Старые результаты сохраняют свою версию, их можно переоценить.</p>
        <div><a class="btn small" href="#/checklists">Изменить критерии</a></div>
      </div></section>
      <section class="panel"><header><h2>Модель</h2></header><div class="body">
        <div class="stat" style="font-size:20px">${st.model}</div>
        <p class="muted" style="margin:0">Усилие ${st.effort}, ${st.votes} ${st.votes === 1 ? "прогон" : "прогона"} на диалог, ${perDialog}. Ступени потребности, консультации и ведения диалога считает код по фактам из ответа модели, итог — по весам чек-листа.</p>
        <div><a class="btn small" href="#/analytics">Точность и стоимость</a></div>
      </div></section>
    </div>
    <section class="panel" style="margin-top:20px"><header><h2>Как проверить работу за пять минут</h2></header><div class="body">
      <ol class="steps-demo">
        <li>Откройте очередь, выберите диалог и нажмите «Оценить». Через 20–40 секунд появятся баллы и обоснования.</li>
        <li>Нажмите «Перейти к 01:51» у любого критерия — транскрипт прокрутится к реплике, у звонка запись перемотается на это место.</li>
        <li>В роли оператора нажмите «Оспорить» у критерия и опишите, что не так.</li>
        <li>Переключитесь на «Проверяющий», откройте «Апелляции», подтвердите или измените оценку — итог пересчитается.</li>
        <li>В «Чек-листах» замените критерий, сохраните новую версию и примените её к набору.</li>
      </ol></div></section>`);

  root.querySelectorAll("[data-ds]").forEach((a) => (a.onclick = () => { store.dataset = a.dataset.ds; }));
  root.querySelector("#fetch").onclick = () => fetchDialog(st);
  root.querySelector("#upload").onclick = () => uploadDialog();
}

export function fetchDialog(st) {
  const d = dialog(html`<header><h2>Набор из API организаторов</h2></header><div class="content">
    <p class="muted" style="margin:0">Запрос идёт через сервер: адрес с подписью хранится в .env (CQA_API_URL), браузер его не видит.</p>
    ${!st.has_api_url ? html`<div class="alert warn" style="margin:0"><b>CQA_API_URL не задан</b>Вставьте адрес кнопки «Открыть GET для DEV» в .env или загрузите набор файлом.</div>` : ""}
    <label class="field">group_id<input class="input" name="g" placeholder="например, CONTROL" autofocus></label>
    <div class="err-text"></div></div>
    <footer><button class="btn ghost" value="c">Отмена</button><button class="btn primary" value="ok">Загрузить</button></footer>`);
  const err = d.querySelector(".err-text");
  d.querySelector('[value="c"]').onclick = () => d.close();
  d.querySelector('[value="ok"]').onclick = async (e) => {
    e.target.disabled = true; err.textContent = "";
    try {
      const r = await api("/datasets/fetch", { group_id: d.querySelector("[name=g]").value });
      store.dataset = r.id; d.close(); toast(`Загружено ${r.count} диалогов`); location.hash = "#/queue";
    } catch (x) { err.textContent = x.message; e.target.disabled = false; }
  };
}

export function uploadDialog() {
  const d = dialog(html`<header><h2>Загрузить диалоги</h2></header><div class="content">
    <p class="muted" style="margin:0">Файл в формате API организаторов: целиком ответ, объект data или массив records. Метки времени и роли сохраняются как есть.</p>
    <label class="field">Название набора<input class="input" name="n" placeholder="Контрольный набор"></label>
    <label class="field">Файл<input class="input" type="file" name="f" accept=".json,application/json"></label>
    <div class="err-text"></div></div>
    <footer><button class="btn ghost" value="c">Отмена</button><button class="btn primary" value="ok">Загрузить</button></footer>`);
  const err = d.querySelector(".err-text");
  d.querySelector('[value="c"]').onclick = () => d.close();
  d.querySelector('[value="ok"]').onclick = async (e) => {
    const file = d.querySelector("[name=f]").files[0];
    if (!file) { err.textContent = "Выберите файл"; return; }
    e.target.disabled = true; err.textContent = "";
    try {
      const name = d.querySelector("[name=n]").value.trim();
      const r = await api("/datasets", { name: name || file.name.replace(/\.json$/i, ""), id: name || undefined, payload: await file.text() });
      store.dataset = r.id; d.close(); toast(`Загружено ${r.count} диалогов`); location.hash = "#/queue";
    } catch (x) { err.textContent = x.message; e.target.disabled = false; }
  };
}

export { money };
