# Сбор и неизменяемые пакеты

Действующий запуск — current/daily.py под пользователем teamactivity.
Systemd unit team-activity-collect.service обслуживает оба аккаунта последовательно;
team-activity-collect.timer назначает 00:10 Europe/Moscow и догоняет пропущенный запуск
после включения сервера. Инструкция и состояние — SERVER_GUIDE / STATUS.

Граница сбора — начало текущего московского дня, исключительно. Checkpoint переносится
только после проверки всех источников и обоих account-input файлов. Начальная история
проекта — 1 октября 2026. При первом запуске проверенный существующий bundle может стать
исходной точкой; при смене источников требуется пересмотр охвата.

Предыдущие 7 дней перед checkpoint перечитываются для наблюдения правок. Более старые
правки, удаления и прошлые редакции Telegram автоматически не восстанавливаются.
Пропущенные дни входят в следующую выборку. Частичный bundle сохраняется и возобновляется.
Перезапуск после успешного сбора за ту же дату не обращается к Telegram.

| Путь относительно collector home | Назначение |
|---|---|
| bundles/ID/request.json | Источники, аккаунты и неизменяемый период |
| bundles/ID/sources/*.jsonl | Исходные сообщения, entities, reply/topic, версии и timestamps |
| bundles/ID/progress.json | Завершённые источники и попытки для resume |
| bundles/ID/manifest.json | Проверенные counts и SHA256 всех источников |
| bundles/ID/account-inputs/account-1.json | Вход только Traffic Angels |
| bundles/ID/account-inputs/account-2.json | Вход только TheRaw |
| bundles/ID/account-inputs/manifest.json | Контрольные суммы двух входов |
| last-complete-bundle.json | Последний проверенный raw bundle; не импорт в БД |
| daily/state.json | Checkpoint, незавершённый bundle, безопасный тип ошибки и FloodWait |
| daily/runs/ID.json | Квитанция сбора; analysis_status остаётся not_started |

Raw сохраняется без обрезки, включая ссылки/entities и media metadata; вложения не скачиваются.
Raw hash не является revision_hash importer. Разделение/адаптер: TENANTS.md.
Ошибки не превращаются в успешное усечение. Telegram FloodWait соблюдается до нового обращения.
Файлы закрыты правами 0600, каталоги 0700. Это исходные данные, не очищенный model view.

Ручная диагностика без сети: `.venv/bin/python current/daily.py --status`.
Проверка конкретного raw bundle: `.venv/bin/python current/bundle.py --verify BUNDLE_ID`.
Явный повторный сбор периода: bundle.py --from-date YYYY-MM-DD; использовать только
по задаче, он не заменяет дневной checkpoint. Все запуски используют общий bundle-capture.lock.
