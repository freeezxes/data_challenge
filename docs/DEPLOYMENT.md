# Отдельная версия для ortaresearch.com

Ветка `codex/ortaresearch-deploy`, рабочая папка `data_challenge-deploy`.
Основная ветка и её `.env` не меняются.

Серверный вариант запускается без ключа. Кнопка «API-ключ» доступна в шапке;
при запуске оценки без ключа открывается то же поле. Принимается ключ Anthropic.
Ключ хранится только в памяти вкладки; обновление страницы его удаляет.
Он передаётся по HTTPS в заголовке `X-CQA-API-Key`, затем используется
клиентом Anthropic в памяти сервера до завершения запроса или фонового задания.
В настройки, SQLite, cookies и файлы ключ не записывается. Удаление или замена
ключа влияет на новые запросы; запущенные задания заканчиваются со своим ключом.
Ключи из `.env`, `ANTHROPIC_API_KEY` и адрес Anthropic из окружения веб-сервер
игнорирует. Внешние запросы идут в `https://api.anthropic.com`.

Оценщик, голосование, проверка доказательств, harness, CLI и тесты поставляются
вместе с продуктом. Прежние результаты и локальная база не копируются: новая база
создаётся из DEV и чек-листа в репозитории. Это общий демонстрационный стенд:
наборы, оценки и апелляции видны всем посетителям; ключи между ними не разделяются.

## Локальная проверка без расходов

```bash
HOST=127.0.0.1 PORT=8100 CQA_DB=/tmp/cqa-deploy-preview.db /path/to/python -m app.server
python -m pytest -q
python scripts/run_eval.py --dry-run
```

`.env` для веб-сервера не нужен. CLI `scripts/run_eval.py` остаётся отдельным
инструментом и требует своего ключа из окружения при платном запуске. Запуск CLI
на VPS при необходимости: `cd /srv/cqa/current`, затем `.venv/bin/python scripts/run_eval.py --help`.
Не вписывайте ключ в аргументы shell или файлы; для платного CLI в интерактивном
терминале используйте `read -rs ANTHROPIC_API_KEY; export ANTHROPIC_API_KEY`,
после прогона — `unset ANTHROPIC_API_KEY`.

## Установка на подтверждённый сервер

Подтверждённый сервер AstanaGB: `root@100.75.211.119` через Tailscale,
публичный IP `92.38.49.113`, hostname `vds32206.vpsza500.kz`.
ED25519: `SHA256:hvz+sn86NI6Dv7sUXidRcnuysBet/QG/hL0shEuA7Zg`.
Старый адрес ortaresearch.com — `92.38.48.227`, это другая машина.
Не отключайте проверку SSH host key ради деплоя.

Нужны Ubuntu, Python 3.11+ с модулем venv, nginx и curl. На подтверждённой машине
проверен Python 3.12. Установщик поддерживает первый запуск без TLS: приложение
слушает только localhost, HTTP пока отдаёт ответ обслуживания и ACME-проверку.

1. После тестов закоммитьте изменения в отдельной ветке.
2. `bash deploy/package.sh /tmp/cqa-deploy.tar.gz` создаёт архив только из
   перечисленных исходников git: без `.env`, `.git`, виртуального окружения,
   `data/raw`, `data/runs`, локальных баз и подписанных адресов организаторов.
3. Скопируйте архив на подтверждённый хост через `scp` и распакуйте в новую папку.
4. Выполните `sudo bash /path/to/unpacked-release/deploy/install.sh`.

Установщик сначала собирает окружение отдельного сервиса `cqa.service` на
127.0.0.1:8100 и проверяет состояние без ключа. После этого подключает только
виртуальный хост ortaresearch.com. Другие конфигурации nginx, приложения и базы
не меняет; старый `ortaresearch.service` на другой машине не останавливает.
Автоматический откат при ошибке установки восстанавливает nginx и прежнюю
версию CQA; резервная копия конфигурации — `/srv/cqa/shared/before-<дата>`.

Для первого подключения домена измените в GoDaddy только A-запись `@` на
`92.38.49.113`; `www` остаётся CNAME на `ortaresearch.com`. После обновления DNS:

```bash
certbot certonly --non-interactive --webroot -w /var/www/cqa-acme \
  --cert-name ortaresearch.com -d ortaresearch.com -d www.ortaresearch.com
install -m 644 /srv/cqa/current/deploy/ortaresearch.conf /etc/nginx/sites-available/ortaresearch.conf
nginx -t && systemctl reload nginx
```

Используется существующий ACME-аккаунт сервера. Для продления сертификата
`/.well-known/acme-challenge/` остаётся доступен через HTTP; nginx обслуживает
остальной трафик через HTTPS. Переключать DNS следует после проверки backend,
чтобы прежние проекты сервера продолжали работать.

Код: `/srv/cqa/releases/<дата>`, ссылка `/srv/cqa/current`, база:
`/srv/cqa/shared/cqa.db`. Логи: `journalctl -u cqa.service`. HTTP API не логирует
заголовки/тела, а сообщения ошибок скрывают ключи. Не включайте debug-логирование
HTTP SDK и заголовков nginx на стенде с пользовательскими ключами.

## Проверка и ручной возврат старого сайта

```bash
systemctl status cqa.service
nginx -t
curl --fail https://ortaresearch.com/api/state
```

Ожидается `credential_mode: "per_request"`, `has_key: false`, набор DEV из
65 диалогов. В браузере проверьте ввод, отмену, замену, удаление ключа и обновление
страницы. Подключение ключа не делает запрос к модели; платная оценка начинается
только после запуска пользователем.

Для возврата предыдущей версии CQA восстановите `ortaresearch.conf` из нужной папки
`before-*` в `/etc/nginx/sites-available/ortaresearch.conf`, выполните `nginx -t`,
и `systemctl reload nginx`.
Затем можно остановить CQA: `systemctl disable --now cqa.service`.
Для возврата прежнего сайта со старой машины отдельно верните A-запись домена
на сохранённый прежний адрес; его файлы и сервисы этим деплоем не меняются.
