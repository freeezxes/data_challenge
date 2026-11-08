import { preferences, api, html, raw, hero, toast, money, store } from "../core.js";

const SYS = {
  identity: { RU: "Личность клиента подтверждена.", KZ: "Клиенттің жеке басы расталды.", EN: "Customer identity verified." },
  left: { RU: "Клиент покинул чат.", KZ: "Клиент чаттан шықты.", EN: "Customer left the chat." },
};
const WHO = { client: "Клиент", operator: "Вы", bot: "Бот", system: "" };
const SEV = { stop: "Стоп", warn: "Важно", info: "Совет" };

export default async function copilot(root, params = {}) {
  const scenarios = await api("/copilot/scenarios");
  const s = {
    mode: preferences.getItem("cqa-cp-mode") || "sim", base: null, language: "RU", messages: [], beats: [],
    name: preferences.getItem("cqa-cp-name") || "Алина", started: 0, ended: false, busy: false, cost: 0,
    hints: null, instant: [], typing: false, armed: false, draftTimer: null,
  };
  const fromLink = params.sc && scenarios.find((x) => `${x.dataset_id}|${x.conversation_id}` === params.sc);
  const pickDefault = fromLink || scenarios.find((x) => x.conversation_id === "p1-ru-009") || scenarios[0];

  root.innerHTML = String(html`
    ${hero({ title: "Суфлёр", art: "chat",
      text: "Оператор ведёт чат, а система подсказывает по чек-листу на ходу: что сделать сейчас и три готовых варианта ответа. В тренажёре клиента играет модель, в конце чат оценивается как обычный диалог." })}
    <div class="evalbar" id="setup">
      <b>Новый чат</b>
      <div class="seg" role="group" aria-label="Кто клиент">
        <button type="button" data-mode="sim" aria-pressed="${String(s.mode === "sim")}">Клиент-симулятор</button>
        <button type="button" data-mode="replay" aria-pressed="${String(s.mode === "replay")}">Клиент из диалога</button></div>
      <label>Сценарий <select class="input" id="sc" style="max-width:420px">${scenarios.map((x) => html`<option value="${x.dataset_id}|${x.conversation_id}" ${raw(x === pickDefault ? "selected" : "")}>${x.conversation_id} · ${x.language} · ${x.topic.slice(0, 60)}</option>`)}</select></label>
      <label>Ваше имя <input class="input" id="nm" value="${s.name}" style="width:120px"></label>
      <button class="btn primary" id="go">Начать чат</button>
    </div>
    <div class="cp">
      <section class="panel cp-chat" aria-label="Чат">
        <header><h2 id="chat-title">Чат с клиентом</h2><span class="meta" id="chat-meta">выберите сценарий и нажмите «Начать чат»</span></header>
        <div class="bubbles" id="bubbles" aria-live="polite"><div class="empty"><b>Здесь будет чат</b>Клиент напишет первым. Суфлёр справа подскажет, с чего начать.</div></div>
        <div class="composer">
          <div class="instant" id="instant"></div>
          <textarea class="input" id="draft" placeholder="Ваш ответ клиенту…" disabled></textarea>
          <div class="composer-actions">
            <button class="btn small" id="ident" disabled title="Как в CRM: система подтверждает личность клиента">Подтвердить личность</button>
            <button class="btn small ghost" id="check" disabled>Проверить черновик</button>
            <span class="spacer"></span>
            <button class="btn small" id="finish" disabled>Завершить и оценить</button>
            <button class="btn primary" id="send" disabled>Отправить</button>
          </div>
        </div>
      </section>
      <aside class="panel cp-help" aria-label="Суфлёр">
        <header><h2>Подсказки</h2><span class="meta" id="help-meta">Claude Haiku 5.5 + проверки кодом</span></header>
        <div id="help" class="help"><div class="empty">Подсказки появятся, когда клиент напишет.</div></div>
      </aside>
    </div>`);

  const $ = (q) => root.querySelector(q);
  const elapsed = () => { const t = Math.floor((Date.now() - s.started) / 1000); return `${String(Math.floor(t / 60)).padStart(2, "0")}:${String(t % 60).padStart(2, "0")}`; };

  function paintChat() {
    const b = $("#bubbles");
    if (!b) return;
    b.innerHTML = String(html`${s.messages.map((m) => html`<div class="bubble ${m.speaker}">${m.speaker !== "system" ? html`<span class="by">${m.speaker === "operator" ? s.name : WHO[m.speaker]} · ${m.timestamp}</span>` : ""}<div>${m.text}</div></div>`)}
      ${s.typing ? html`<div class="bubble client typing"><span class="dots"><i></i><i></i><i></i></span></div>` : ""}`);
    b.scrollTop = b.scrollHeight;
    const live = s.started && !s.ended;
    ["#draft", "#send", "#ident", "#check"].forEach((q) => ($(q).disabled = !live || s.busy));
    $("#finish").disabled = !s.messages.some((m) => m.speaker === "operator") || s.busy;
    $("#chat-meta").textContent = !s.started ? "" : s.ended ? "чат завершён — оцените его" : s.typing ? "клиент печатает…" : `ваш ход · ${elapsed()}`;
  }

  function paintHelp(loading) {
    const h = $("#help");
    if (!h) return;
    // код и модель часто говорят об одном: по критерию оставляем подсказку модели, «стоп» кода — всегда
    const key = (c) => String(c || "").toLowerCase().split(/[\s,]/)[0];
    const model = ((s.hints?.hints) || []).map((x) => ({ ...x, src: "Haiku" }));
    const covered = new Set(model.map((x) => key(x.criterion)));
    const items = [...s.instant.filter((x) => x.severity === "stop" || !covered.has(key(x.criterion))).map((x) => ({ ...x, src: "код" })), ...model];
    const order = { stop: 0, warn: 1, info: 2 };
    items.sort((a, b) => order[a.severity] - order[b.severity]);
    const hint = (x) => html`<div class="hint ${x.severity}"><span class="sev">${SEV[x.severity]}</span><div><b>${x.text}</b><small>${x.criterion} · ${x.src}</small></div></div>`;
    const stops = items.filter((x) => x.severity === "stop"), rest = items.filter((x) => x.severity !== "stop");
    h.innerHTML = String(html`
      ${stops.length ? html`<div class="hint-list">${stops.map(hint)}</div>` : ""}
      <div class="variants-head"><b>Варианты ответа</b>${loading ? html`<span class="muted">Haiku думает…</span>` : s.hints ? html`<span class="muted">${s.hints.latency_s} с · ${money(s.hints.cost_usd)}</span>` : ""}</div>
      ${loading ? html`<div class="variant pending"><span class="skel wide"></span></div><div class="variant pending"><span class="skel wide"></span></div><div class="variant pending"><span class="skel wide"></span></div>`
        : s.hints?.replies?.length ? s.hints.replies.map((r, i) => html`<button class="variant" data-v="${i}"><span class="lab">${r.label}</span><span>${r.text}</span></button>`)
        : html`<div class="muted" style="font-size:13px">${s.ended ? "Чат завершён." : "Нажмите «Проверить черновик», чтобы получить варианты."}</div>`}
      ${rest.length ? html`<div class="variants-head"><b>Что важно сейчас</b></div><div class="hint-list">${rest.map(hint)}</div>` : ""}`);
    h.querySelectorAll("[data-v]").forEach((b) => (b.onclick = () => {
      $("#draft").value = s.hints.replies[Number(b.dataset.v)].text; $("#draft").focus(); onDraft();
    }));
    $("#help-meta").textContent = `Claude Haiku 5.5 + проверки кодом · за чат ${money(s.cost)}`;
  }

  async function requestHints(draft = "") {
    if (s.ended) return;
    paintHelp(true);
    try {
      const r = await api("/copilot/hint", { messages: s.messages, language: s.language, operator_name: s.name, draft });
      s.hints = r; s.instant = r.instant; s.cost += r.cost_usd || 0;
    } catch (x) { toast(x.message, true); s.hints = null; }
    paintHelp(false);
  }

  async function onDraft() {
    clearTimeout(s.draftTimer);
    s.armed = false; $("#send").textContent = "Отправить"; $("#send").classList.remove("danger");
    s.draftTimer = setTimeout(async () => {
      if (!$("#draft")) return;  // ушли со страницы
      const r = await api("/copilot/check", { messages: s.messages, draft: $("#draft").value, language: s.language }).catch(() => null);
      if (!r || !$("#instant")) return;
      s.instant = r.instant;
      const stops = r.instant.filter((x) => x.severity === "stop");
      $("#instant").innerHTML = String(html`${stops.map((x) => html`<div class="hint stop"><span class="sev">Стоп</span><div><b>${x.text}</b></div></div>`)}`);
      paintHelp(false);
    }, 250);
  }

  async function clientTurn() {
    s.typing = true; s.busy = true; paintChat();
    try {
      if (s.mode === "replay") {
        await new Promise((r) => setTimeout(r, 900));
        const beat = s.beats.shift();
        if (!beat) { s.messages.push({ speaker: "system", text: SYS.left[s.language] || SYS.left.RU, timestamp: elapsed() }); s.ended = true; }
        else for (const m of beat) {
          s.messages.push({ ...m, timestamp: elapsed() });
          if (m.speaker === "system" && /покинул|шықты|left/i.test(m.text)) s.ended = true;
          paintChat(); await new Promise((r) => setTimeout(r, 500));
        }
      } else {
        const r = await api("/copilot/client", { dataset_id: s.base.dataset_id, conversation_id: s.base.conversation_id, messages: s.messages, language: s.language });
        s.cost += r.cost_usd || 0;
        s.messages.push({ speaker: "client", text: r.text, timestamp: elapsed() });
        if (r.leaves) { s.messages.push({ speaker: "system", text: SYS.left[s.language] || SYS.left.RU, timestamp: elapsed() }); s.ended = true; }
      }
    } catch (x) { toast(x.message, true); }
    s.typing = false; s.busy = false; paintChat();
    if (!s.ended) { requestHints(); $("#draft").focus(); } else paintHelp(false);
  }

  async function send() {
    const text = $("#draft").value.trim();
    if (!text) return;
    if (s.instant.some((x) => x.severity === "stop") && !s.armed) {
      s.armed = true; $("#send").textContent = "Нарушение — отправить всё равно"; $("#send").classList.add("danger"); return;
    }
    s.messages.push({ speaker: "operator", text, timestamp: elapsed() });
    $("#draft").value = ""; $("#instant").innerHTML = ""; s.instant = []; s.hints = null; s.armed = false;
    $("#send").textContent = "Отправить"; $("#send").classList.remove("danger");
    paintHelp(false);
    await clientTurn();
  }

  root.querySelectorAll("[data-mode]").forEach((b) => (b.onclick = () => {
    s.mode = b.dataset.mode; preferences.setItem("cqa-cp-mode", s.mode);
    root.querySelectorAll("[data-mode]").forEach((x) => x.setAttribute("aria-pressed", String(x === b)));
  }));
  $("#go").onclick = async () => {
    const [ds, cid] = $("#sc").value.split("|");
    s.name = $("#nm").value.trim() || "Алина"; preferences.setItem("cqa-cp-name", s.name);
    const r = await api("/copilot/start", { dataset_id: ds, conversation_id: cid });
    Object.assign(s, { base: { dataset_id: ds, conversation_id: cid }, language: r.language || "RU", beats: r.beats, started: Date.now(),
      ended: false, hints: null, instant: [], cost: 0, messages: r.opening.map((m) => ({ ...m, timestamp: "00:00" })) });
    $("#chat-title").textContent = `Чат с клиентом · ${s.language} · ${s.mode === "sim" ? "клиент-симулятор" : "сценарий " + cid}`;
    $("#go").textContent = "Начать заново";
    paintChat(); requestHints(); $("#draft").focus();
  };
  $("#draft").addEventListener("input", onDraft);
  $("#draft").addEventListener("keydown", (e) => { if (e.key === "Enter" && (e.ctrlKey || e.metaKey)) { e.preventDefault(); send(); } });
  $("#send").onclick = send;
  $("#check").onclick = () => requestHints($("#draft").value);
  $("#ident").onclick = () => { s.messages.push({ speaker: "system", text: SYS.identity[s.language] || SYS.identity.RU, timestamp: elapsed() }); paintChat(); onDraft(); };
  $("#finish").onclick = async () => {
    $("#finish").disabled = true;
    try {
      const r = await api("/copilot/finish", { messages: s.messages, language: s.language, mode: s.mode, base: s.base, operator_name: s.name });
      store.dataset = r.dataset_id; toast("Чат отправлен на оценку");
      location.hash = `#/c/${encodeURIComponent(r.dataset_id)}/${encodeURIComponent(r.conversation_id)}`;
    } catch (x) { toast(x.message, true); $("#finish").disabled = false; }
  };
  paintChat();
  return () => clearTimeout(s.draftTimer);
}
