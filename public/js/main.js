import { api, role, esc } from "./core.js";
import home from "./pages/home.js";
import queue from "./pages/queue.js";
import card from "./pages/card.js";
import appeals from "./pages/appeals.js";
import checklists from "./pages/checklists.js";
import analytics from "./pages/analytics.js";
import copilot from "./pages/copilot.js";

const PAGES = { home, queue, card, appeals, checklists, analytics, copilot };
const view = document.getElementById("view");
let cleanup = null;

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
window.addEventListener("rolechange", () => { paintRole(); render(); });
window.addEventListener("hashchange", render);
window.addEventListener("cqa:changed", refreshBadge);
paintRole();
render();
