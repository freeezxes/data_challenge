import { api, role, esc, apiKeyDialog, hasApiKey, initializeSession, refreshSession, preferences, toast } from "./core.js";
import home from "./pages/home.js";
import queue from "./pages/queue.js";
import card from "./pages/card.js";
import appeals from "./pages/appeals.js";
import checklists from "./pages/checklists.js";
import analytics from "./pages/analytics.js";
import copilot from "./pages/copilot.js";
import operators from "./pages/operators.js";

const PAGES = { home, queue, card, appeals, checklists, analytics, copilot, operators };
const view = document.getElementById("view");
let cleanup = null;
let ready = false;

function parse() {
  const [path, query = ""] = location.hash.replace(/^#/, "").split("?");
  const parts = path.split("/").filter(Boolean).map(decodeURIComponent);
  const params = Object.fromEntries(new URLSearchParams(query));
  if (!parts.length) return { page: "home", params };
  if (parts[0] === "c") return { page: "card", nav: "queue", params: { ...params, ds: parts[1], cid: parts[2] } };
  if (parts[0] === "checklists") return { page: "checklists", params: { ...params, key: parts[1] } };
  return { page: PAGES[parts[0]] ? parts[0] : "home", params };
}

async function render() {
  const { page, nav, params } = parse();
  cleanup?.(); cleanup = null;
  document.querySelectorAll(".tab").forEach((t) => (t.dataset.route === (nav || page) ? t.setAttribute("aria-current", "page") : t.removeAttribute("aria-current")));
  try {
    cleanup = (await PAGES[page](view, params)) || null;
  } catch (e) {
    view.innerHTML = `<div class="panel"><div class="empty"><b>Не удалось открыть страницу</b>${esc(e.message)}<br><a class="btn" href="#/">На главную</a></div></div>`;
  }
  if (!params.m) window.scrollTo(0, 0);
  refreshBadge();
}

export async function refreshBadge() {
  try {
    const st = await api("/state");
    const b = document.querySelector('.tab[data-route="appeals"] .count');
    b.hidden = !st.open_appeals; b.textContent = st.open_appeals;
  } catch { /* нет сервера — бейдж просто не показываем */ }
}

function paintRole() {
  document.querySelectorAll(".role button").forEach((b) => b.setAttribute("aria-pressed", String(b.dataset.role === role.get())));
}
document.querySelectorAll(".role button").forEach((b) => (b.onclick = () => role.set(b.dataset.role)));
window.addEventListener("rolechange", () => { if (ready) { paintRole(); render(); } });
window.addEventListener("hashchange", () => { if (ready) render(); });
window.addEventListener("focus", () => { if (ready) refreshSession().catch(() => {}); });
document.addEventListener("visibilitychange", () => {
  if (ready && document.visibilityState === "visible") refreshSession().catch(() => {});
});
window.addEventListener("pageshow", (event) => { if (event.persisted) location.reload(); });
window.addEventListener("cqa:changed", refreshBadge);
document.getElementById("api-key-settings").onclick = apiKeyDialog;
document.addEventListener("click", (event) => {
  if (event.target.closest("[data-add-api-key]")) apiKeyDialog();
});
window.addEventListener("cqa:keychange", () => {
  const button = document.getElementById("api-key-settings");
  button.textContent = hasApiKey() ? "API-ключ добавлен" : "API-ключ";
  document.querySelectorAll("[data-key-notice]").forEach((notice) => { notice.hidden = hasApiKey(); });
});
document.getElementById("account-logout").onclick = async (event) => {
  event.target.disabled = true;
  try {
    await preferences.flush();
    await api("/auth/logout", {});
    location.replace("/login");
  } catch (error) { toast(error.message, true); event.target.disabled = false; }
};
initializeSession().then((session) => {
  ready = true;
  document.getElementById("account-name").textContent = session.user.username;
  if (session.guest) {
    const button = document.getElementById("account-logout");
    button.textContent = "Войти";
    button.onclick = () => location.assign("/login");
  }
  paintRole(); render();
}).catch((error) => { view.textContent = error.message; });
