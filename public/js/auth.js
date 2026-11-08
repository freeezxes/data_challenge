const form = document.getElementById("auth-form");
const password = document.getElementById("password");
const error = document.getElementById("auth-error");
const submit = document.getElementById("auth-submit");
const toggle = document.getElementById("auth-switch");
let registering = false;
toggle.onclick = () => {
  registering = !registering;
  error.textContent = "";
  document.getElementById("auth-title").textContent = registering ? "Создать аккаунт" : "Войти в CQA";
  document.getElementById("auth-description").textContent = registering
    ? "У вас будет отдельное пространство для диалогов, оценок и своего API-ключа."
    : "Ваши диалоги, оценки, чек-листы и API-ключ сохраняются в аккаунте.";
  document.getElementById("auth-help").hidden = !registering;
  document.getElementById("auth-switch-label").textContent = registering ? "Уже есть аккаунт?" : "Нет аккаунта?";
  toggle.textContent = registering ? "Войти" : "Создать аккаунт";
  submit.textContent = registering ? "Создать аккаунт" : "Войти";
  password.autocomplete = registering ? "new-password" : "current-password";
  password.minLength = registering ? 12 : 1;
  password.value = "";
};
form.onsubmit = async (event) => {
  event.preventDefault(); error.textContent = "";
  const label = submit.textContent;
  submit.disabled = true; toggle.disabled = true; submit.textContent = "Проверяем…";
  try {
    const response = await fetch("/api/auth/" + (registering ? "register" : "login"), {
      method: "POST", credentials: "same-origin",
      headers: { "Content-Type": "application/json", "X-CQA-Request": "1" },
      body: JSON.stringify({ username: form.username.value, password: password.value }),
    });
    const data = await response.json();
    if (!response.ok) throw new Error(data.error || "Не удалось войти. Повторите попытку.");
    password.value = "";
    location.replace("/");
  } catch (e) { error.textContent = e.message || "Проверьте соединение и повторите попытку."; }
  finally { submit.disabled = false; toggle.disabled = false; submit.textContent = label; }
};
fetch("/api/auth/session", { credentials: "same-origin" })
  .then(async (response) => { if (response.ok && !(await response.json()).guest) location.replace("/"); }).catch(() => {});
