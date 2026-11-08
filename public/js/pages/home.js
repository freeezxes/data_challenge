import { api, html, hero, money, store } from "../core.js";
import { importDialog } from "../imports.js";
export { fetchDialog, uploadDialog } from "../imports.js";

export default async function home(root) {
  const st = await api("/state");
  const perDialog = st.votes > 1 ? "≈ $0,12–0,19 за диалог (прогоны идут одновременно)" : "≈ $0,04 за диалог";
  root.innerHTML = String(html`
    ${hero({ title: "Контроль качества диалогов", art: "home",
      text: "Модель проверяет каждый чат и звонок по чек-листу, показывает реплику-доказательство для каждого балла. Спорную оценку можно оспорить, проверяющий решает и итог пересчитывается.",
      actions: html`<a class="btn" href="#/checklists">Чек-лист</a><a class="btn primary" href="#/queue">Открыть очередь</a>` })}
    <div class="alert warn key-notice" data-key-notice ${st.has_key ? html`hidden` : ""}><div><b>Перед запуском добавьте свой ключ Claude API</b>Сохранённые оценки, апелляции и чек-листы доступны без ключа.</div><button class="btn small" type="button" data-add-api-key>Добавить ключ</button></div>
    <div class="home">
      <section class="panel"><header><h2>Наборы диалогов</h2></header><div class="body">
        ${st.datasets.map((d) => html`<div class="dsrow"><div class="t"><b>${d.name}</b><small>оценено ${d.evaluated} из ${d.count}</small></div>
          <a class="btn small" href="#/queue" data-ds="${d.id}">Открыть</a></div>`)}
        <div style="display:flex;gap:8px;flex-wrap:wrap;margin-top:6px">
          <button class="btn small primary" id="upload">Добавить диалоги</button>
        </div>
      </div></section>
      <section class="panel"><header><h2>Активный чек-лист</h2><span class="meta">${st.active_checklist}</span></header><div class="body">
        <div class="stat" style="font-size:20px">${st.active_checklist_name}</div>
        <p class="muted" style="margin:0">Новые оценки идут по этой версии. Старые результаты сохраняют свою версию, их можно переоценить.</p>
        <div><a class="btn small" href="#/checklists">Изменить критерии</a></div>
      </div></section>
      <section class="panel"><header><h2>Модель</h2></header><div class="body">
        <div class="stat" style="font-size:20px">${st.model}</div>
        <p class="muted" style="margin:0">Усилие ${st.effort}, ${st.votes} ${st.votes === 1 ? "прогон" : "прогона"} на диалог, ответ по большинству, ${perDialog}. Ступени потребности, консультации и ведения диалога считает код по фактам из ответа модели, итог — по весам чек-листа.</p>
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
  root.querySelector("#upload").onclick = () => importDialog();
}

export { money };
