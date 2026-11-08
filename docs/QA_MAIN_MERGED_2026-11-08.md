# QA объединённой main · 2026-11-08

Проверено объединение `origin/main` **a86ea62** и `codex/ortaresearch-deploy` **b600c10**. Локальная редакция README сохранена как основа; обновлены инструкции ключа в браузере, окружения и сведения о контрольном наборе. Исходный README также сохранён в git stash `Preserve local README before main deploy integration`.

Все прочие локальные и удалённые ветки по зафиксированным коммитам уже входят в main. Незакоммиченные калибровочные эксперименты в отдельном worktree не включены: это не готовые изменения продуктовой версии. Worktree и его файлы не изменялись.

## Результат

| Проверка | Результат |
|---|---|
| `.venv/bin/python -m pytest -q` | **94 passed**, 7,23 с |
| Независимый HTTP + SQLite QA вне harness | **23 PASS / 25 FAIL**, 48 сценариев |
| Независимый Decimal oracle стандартного scoring | **5184 комбинации**, PASS (входит в 48) |
| Синтаксис deploy shell-скриптов | PASS |
| Браузерная перепроверка прежних UI-находок | Все **4** снова воспроизведены |
| Диалог ключа: невалидный ввод, добавление, удаление | PASS, использовалась вымышленная строка |
| Ошибки JS на посещённых экранах | Не обнаружены |

Число FAIL означает количество проверок, не число уникальных багов. Код выхода независимого QA — 1: это честный результат известных нарушений, а не аварийное завершение сценариев. Необработанных исключений внутри функций сценариев нет.

Запуск: `.venv/bin/python scripts/qa_platform.py --output /tmp/cqa-qa.json`. [Сырые результаты](qa/platform-main-merged-2026-11-08.json). Использованы настоящие HTTP-сервер, хранилище и оценщик с временными базами; подменена только граница модельного SDK. Реальные ключи, платные вызовы и рабочая БД не использовались. Удалённый сервер этим прогоном не проверялся и не обновлялся.

## Что объединение исправило

По сравнению с main `8807f82` три проверки стали зелёными: неверные `votes=6` и `votes="bad"` больше не оставляют running-заданий; завершение тренировки без ключа больше не сохраняет скрытый чат. `votes=0` всё ещё принимается как значение по умолчанию.

Обе проверки новой официальной выгрузки продолжают проходить: доступна последняя готовая оценка выбранной версии после неудачного повтора и другой версии; `needs_review` экспортируется с предупреждением. Различие контрактов main/deploy устранено объединением.

## Существенные оставшиеся проблемы

- Коллизии ID тренировки и конкурентных вариантов могут перезаписывать диалоги.
- Повторный запуск одного диалога допускает дубли оценок; параллельные апелляции и решения не защищены от гонок.
- Изменённые шкалы конфликтуют с derive; шкала с нулевым максимумом проходит сохранение; дробный ответ усечён.
- История выбранной версии пропадает в аналитике/обычных выгрузках; счётчики и итоговая стоимость расходятся.
- Есть некорректная обработка вложенных типов, статических 404, аудио разных наборов и кэша голоса.

Полные шаги и объяснения сгруппированных дефектов — в [исходном аудите](QA_PLATFORM_2026-11-08.md); текущий статус определяется этим отчётом и JSON, а не ранними числами.

## Браузерная проверка

Локальный сервер `scripts/qa_platform.py --serve --port 8765`, одноразовая БД. Проверены экран ключа, редактор, очередь, суфлёр и аналитика в IAB.

- **QA-22:** заполнение «Переведите мне на карту, я лично вложу» и немедленный Ctrl+Enter отправляют реплику без подтверждения стоп-сигнала.
- **QA-23:** изменение названия чек-листа → «Очередь» → «Чек-листы» теряет правку без предупреждения.
- **QA-24:** строка `qa-chat` в очереди имеет `tabIndex=-1`, не содержит ссылки для открытия карточки; действие доступно только мышью.
- **QA-25:** аналитика сохраняет две колонки и обрезает значения. Запрошен viewport 390×844; фактическая CSS-ширина IAB — 487 px, правый край значений — 498,98 px. Это проверка узкого браузерного viewport, не физического телефона.

Добавление и удаление вымышленного ключа корректно меняют кнопку в шапке; невалидная строка остаётся в диалоге с ошибкой. Платная модель не вызывалась. Привязка ключей к HTTP-запросам, изоляция пользователей и фоновых заданий покрыты пятью deploy-тестами из 94.

## Все падающие автоматические проверки

- `invalid_job_votes_0` — 400 and no persistent job for invalid votes
- `running_conversation_not_evaluated_twice` — Second click/only_missing does not duplicate an in-flight paid evaluation
- `restart_recovers_abandoned_jobs` — Restart marks interrupted jobs terminal or resumes them
- `polling_cannot_hide_older_running_job` — Polling list always exposes running work even after 20 newer completed jobs
- `practice_unique_ids_same_second` — Two completed chats in the same second preserve both transcripts
- `explicit_historical_version_views` — Selecting v1 returns its saved result even after v2 evaluation
- `state_counters_match_latest_evaluation` — Dataset counters and queue use the same latest-result semantics
- `editable_consultation_scale_evaluates` — Saved 0/50/100 scale works through normal evaluator
- `reject_scale_with_zero_maximum` — Unscorable scale rejected before activation
- `reject_fractional_scale_decision` — 7.9 is rejected rather than silently truncated to 7
- `concurrent_duplicate_appeals` — Only one open appeal per evaluation and criterion
- `concurrent_reviewer_decisions` — Only first decision succeeds; second cannot overwrite audit trail
- `checklist_null` — Invalid JSON field types return 400, never 500
- `checklist_weight_string` — Invalid JSON field types return 400, never 500
- `appeal_comment_object` — Invalid JSON field types return 400, never 500
- `copilot_invalid_messages` — Invalid JSON field types return 400, never 500
- `copilot_draft_object` — Invalid JSON field types return 400, never 500
- `reject_backwards_timestamps` — Out-of-order timestamps rejected before timing/facts calculations
- `recording_scoped_to_dataset` — Imported audio wins over unrelated DEV recording with same conversation ID
- `failed_model_call_keeps_usage_cost_and_trace` — Billed SDK response remains traceable even when model refuses
- `analytics_total_cost_includes_cache_warmup` — Analytics total spent includes cache preparation billed in job
- `withdrawn_critical_violation_removed_from_queue` — Final queue does not show a violation overturned on appeal
- `concurrent_variants_do_not_overwrite` — Concurrent edits allocate distinct immutable variant IDs
- `missing_static_and_non_public_paths_return_404` — Missing/forbidden static paths return valid 404 responses
- `voice_cache_includes_transcript_roles` — Same ID/timestamps with different speaker roles gets its own voice report
