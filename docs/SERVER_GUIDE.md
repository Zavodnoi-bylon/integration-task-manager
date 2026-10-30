# Team Activity Explorer — сервер, данные и карта файлов

**Рабочая точка отсчёта: 30 октября 2026.** Начинать навигацию с этого документа.
Короткая ссылка: `/home/teamactivity/START_HERE.md`.
Основной файл: `/home/teamactivity/team-activity-explorer/docs/SERVER_GUIDE.md`.

Новые задачи создаются двумя Telegram task-manager ботами. Прежний collector сохранён
отдельно и пока не удалён; он больше не является целевым источником новых тикетов.

## Действующее развёртывание

VPS `172.105.249.196` хранит сообщения разрешённых Telegram-источников, предложения
с доказательствами и принятые задачи отдела. UI показывает поступление, завершение,
известный открытый остаток, динамику и смены. Это не аналитика рекламного трафика.

| Компонент | Адрес / путь | Роль |
|---|---|---|
| Проект | /home/teamactivity/team-activity-explorer | API, UI, importer, SQL, исходники collector |
| Ticket bots | /home/teamactivity/integration-task-manager/source | Git checkout и Compose для Kalisto/Mephisto |
| Bot containers | integration-task-manager-kalisto-1; integration-task-manager-mephisto-1 | Один образ, два изолированных профиля |
| Конфигурация ботов | /home/teamactivity/.config/integration-task-manager | Раздельные bot env и общий закрытый database.env |
| AIO domain monitor | /home/teamactivity/aio-domain-monitor/source | Docker/Compose source, deployed commit `6f7e9ce` |
| Domain containers | aio-domain-monitor-bot-1; aio-domain-monitor-refresh-1 | Telegram delivery и пятиминутное обновление AIO |
| Domain state | /var/lib/aio-domain-monitor/state.sqlite3 | Снимок доменов, дневной baseline и delivery outbox |
| Рабочий collector | /home/teamactivity/team-activity-ingest/current | Отдельный процесс сбора Telegram |
| PostgreSQL | team-activity-trial-postgres-1; БД activity_trial | Текущий источник задач и отчёта |
| Web/API | team-activity-trial-web-1; 127.0.0.1:8481 | Метрики и проверенные изменения статуса |
| PostgreSQL-порт | 127.0.0.1:55432 | Только локальные подключения |
| HTTPS | team-activity-access-caddy-1 | Внешний доступ с авторизацией |
| Дашборд | https://team-bi.172-105-249-196.sslip.io/ | Интерфейс пользователя |
| Раскладка | /home/teamactivity/ui-state/layout.json | Общая настройка панелей |
| Резервные данные | /home/teamactivity/migration-trial | Дампы и результаты восстановления |
| Архив обслуживания | /home/teamactivity/maintenance/20261026-baseline | Материалы до новой точки отсчёта |
| SQLite backup cutover | /home/teamactivity/integration-task-manager/backups/20261029T094646Z/database.db | Согласованная резервная копия прежней task DB |

Имена `team-activity-trial` и `activity_trial` исторические, но это действующее развёртывание.
Не переименовывать их при уборке: они участвуют в подключениях и выборе volume.
Docker volume БД — `team-activity-trial_activity_pgdata`. Таблицы просматриваются клиентом
PostgreSQL, не редактированием файлов volume через SFTP.
Боты подключены к внутренней `team-activity-trial_backend` для БД и отдельной egress-сети
для Telegram. Порты не опубликованы. Legacy systemd units отключены, но не удалены.
Штатное управление из checkout: `sudo bash deploy/compose.sh ps`, `logs`, `restart`
или другой Docker Compose subcommand. Wrapper читает точный активный тег из
`/home/teamactivity/integration-task-manager/runtime.env`; секретов в нём нет.

## Доступ и внешняя зависимость

Владелец входит как root по SSH/SFTP с ключом. teamactivity — рабочий пользователь без sudo
и Docker group. Полный доступ владельца уже работает; парольный SSH для этого не требуется.
Не менять всё дерево на chmod 777. В UI activity_viewer — admin; viver читает все панели;
tech_lead пока без разрешённых панелей. Подробности — [ACCESS](ACCESS.md).

Asana-уведомления приходят от отдельного бота на `167.99.36.155`. Это действующая внешняя
зависимость проекта, а не альтернативный сервер дашборда. Переход Review BDM означает
завершение интеграции. Оператор подтвердил обновление бота и health; первый реальный переход
после обновления ещё нужно проверить. Внешние сервисы этой уборкой не изменяются.

## Аккаунты и данные

| Слот | Тенант / аккаунт | Разрешённые источники |
|---|---|---|
| account-1 | Traffic Angels / Kalisto | Tech support; Alert from asana |
| account-2 | TheRaw / mephisto | КОНТОРА / Таски (9); Nelli TheRaw; TheRaw \| запросы офферов |

Источники заданы numeric ID/topic в защищённых config.json. Arina TL BDM и CBDO загружены
разово для Traffic Angels и не входят в ежедневный список. Аккаунты и входы анализа разделены;
UI объединяет принятые задачи обоих тенантов.

Новые боты пишут в `ticketing.*`; UI должен переходить на стабильные витрины `reporting.*`.
`raw.*`, `work.*`, `ops.*` — сохранённый прежний контур сообщений, proposals и решений;
он не переиспользуется под другой смысл и пока остаётся доступен старому dashboard.
Базы `activity_archive_20261025`, `activity_tenants_verify_20261024` и `activity` сохранены;
не использовать их вместо activity_trial и не удалять в рамках уборки.

## Ежедневный сбор

**Включено 27 октября 2026: 00:10 Europe/Moscow**, один timer для двух последовательных аккаунтов.
Проверенные запуски и следующий срок — в [STATUS](STATUS.md).
Граница пакета — начало текущего московского дня. Только что закончившийся день включён;
первые 10 минут нового дня попадут в следующий пакет.

Checkpoint переносится после проверки пяти источников и двух account inputs. При сбое
продолжается тот же пакет, после пропуска дней догружается период. Повтор за ту же дату
не создаёт новый пакет. Для наблюдения правок перечитываются 7 дней перед checkpoint;
правки более старых сообщений могут потребовать отдельного сбора. Удаления и прошлые
редакции Telegram не восстанавливаются. Отдельный очищенный model view ещё не готов.

Ограничения процесса: 512 MiB RAM, до 50% одного CPU, 45 минут на попытку. При ошибке
до трёх попыток за 6 часов с интервалом 15 минут, соблюдается Telegram FloodWait.
Ошибки сохраняются в checkpoint/journal; уведомляющего бота пока нет.

От root:

```sh
systemctl status team-activity-collect.timer --no-pager
systemctl list-timers team-activity-collect.timer --no-pager
journalctl -u team-activity-collect.service -n 60 --no-pager
```

Checkpoint без подключения к Telegram:

```sh
runuser -u teamactivity -- /home/teamactivity/team-activity-ingest/.venv/bin/python /home/teamactivity/team-activity-ingest/current/daily.py --status
```

Ручной повтор при необходимости: `systemctl start team-activity-collect.service`.
После успеха oneshot становится inactive/dead с Result=success — это нормально.
Отключение будущих запусков: `systemctl disable --now team-activity-collect.timer`.
Уже работающий сбор останавливается отдельно командой stop service. Все capture-процессы
используют общий lock и отдельные блокировки сессий.
Таймер не запускает ИИ, не пишет задачи в PostgreSQL и не принимает proposals.

## Файлы данных и системные настройки

Пути ниже относительно `/home/teamactivity`, кроме явно абсолютных. Однотипные файлы
пакетов/зависимостей/резервных копий сгруппированы масками: они растут ежедневно и не являются
отдельными инструкциями. Значения секретов не включены в карту.

| Файл / каталог | Назначение |
|---|---|
| START_HERE.md | Ссылка на этот документ |
| team-activity-explorer/.env | Административная конфигурация БД/приложения/ролей, root 0600 |
| team-activity-explorer/.env.client | Подключение reader/writer для importer, teamactivity 0600 |
| team-activity-explorer/.venv/ | Зависимости Python приложения |
| integration-task-manager/source | Git checkout `Zavodnoi-bylon/integration-task-manager`, production commit `836bca3` |
| integration-task-manager/source/compose.yaml | Контейнеры Kalisto/Mephisto, ресурсы и сети |
| integration-task-manager/runtime.env | Несекретный активный image tag и Compose network/project state |
| integration-task-manager/current | Legacy symlink на release `20261029Tticketing-v7`, только rollback |
| integration-task-manager/.venv/ | Legacy Python-окружение systemd, только rollback |
| integration-task-manager/data/database.db | Прежняя SQLite; сохранена для rollback, не каноническая после cutover |
| integration-task-manager/backups/*/database.db | Согласованные SQLite backups перед переключением |
| .config/integration-task-manager/kalisto.env | Закрытая конфигурация Kalisto |
| .config/integration-task-manager/mephisto.env | Закрытая конфигурация Mephisto |
| .config/integration-task-manager/database.env | Общий закрытый доступ ботов к PostgreSQL |
| aio-domain-monitor/source/ | Исходники и Compose AIO domain monitor; production marker `6f7e9ce` |
| aio-domain-monitor/runtime.env | Несекретный active image tag и Compose runtime state |
| /etc/aio-domain-monitor.env | Закрытые AIO/Telegram credentials и ID приватного alert-канала |
| /var/lib/aio-domain-monitor/state.sqlite3 | Текущий snapshot, дневной baseline и Telegram outbox |
| /var/lib/aio-domain-monitor/domains.json | Прежний baseline, импортирован в SQLite и сохранён для отката |
| maintenance/20261030T131900Z-aio-channel-before-6f7e9ce/ | Backup source/env/state перед channel-flow deploy |
| team-activity-ingest/.venv/ | Python/Telethon действующего collector |
| team-activity-ingest/telegram-app.json | Секретные реквизиты Telegram-приложения |
| team-activity-ingest/accounts/account-N/config.json | Credentials, tenant, chat/topic IDs |
| team-activity-ingest/accounts/account-N/identity.json | Проверенная привязка слота к Telegram ID |
| team-activity-ingest/accounts/account-N/targets-resolved.json | Названия и ID подтверждённых источников |
| team-activity-ingest/accounts/account-N/config.before-*.json | Закрытые резервные конфигурации |
| team-activity-ingest/accounts/account-2/source-discovery.json | Первоначальный поиск согласованных источников |
| team-activity-ingest/accounts/account-2/mephisto-target.json | Подтверждение выбранного чата запросов офферов |
| team-activity-ingest/accounts/account-N/data/session/user.session* | Авторизованные Telegram-сессии, не task DB |
| team-activity-ingest/accounts/account-N/data/run.lock | Блокировка отдельной сессии |
| team-activity-ingest/accounts/authorization.lock | Защита назначения identity двум слотам |
| team-activity-ingest/bundle-capture.lock | Блокировка общего capture/daily процесса |
| team-activity-ingest/bundles/ID/request.json | Неизменяемый период и источники |
| team-activity-ingest/bundles/ID/progress.json | Попытки и завершённые источники для resume |
| team-activity-ingest/bundles/ID/sources/*.jsonl | Оригиналы сообщений и наблюдаемые редакции |
| team-activity-ingest/bundles/ID/manifest.json | Итоговые counts и SHA256 |
| team-activity-ingest/bundles/ID/account-inputs/account-1.json | Вход Traffic Angels |
| team-activity-ingest/bundles/ID/account-inputs/account-2.json | Вход TheRaw |
| team-activity-ingest/bundles/ID/account-inputs/manifest.json | Контрольные суммы разделённых входов |
| team-activity-ingest/bundles/ID/last-error.json, audit-summary.json | Диагностика отдельных проверок, если есть |
| team-activity-ingest/bundles/ID/.account-inputs-*/ | Сохранённые staging-попытки при прерывании публикации |
| team-activity-ingest/last-complete-bundle.json | Последний raw-пакет, не checkpoint анализа |
| team-activity-ingest/daily/state.json | Checkpoint, активный bundle, безопасный тип ошибки/FloodWait |
| team-activity-ingest/daily/runs/ID.json | Квитанция сбора; analysis_status=not_started |
| team-activity-ingest/processing/*/account-N.json | Исторические входы с разовыми ручными экспортами |
| team-activity-ingest/processing/*/normalized-account-N.json | Нормализованные сообщения с importer evidence IDs |
| team-activity-ingest/processing/*/manifest.json | Проверка состава пакета обработки |
| ui-state/layout.json | Общая раскладка; принадлежит UID процесса web |
| ui-state/layout-before-*.json | Предыдущие раскладки для отката |
| migration-trial/snapshot-*/ | Исходный dump, checksums и контрольные отчёты |
| migration-trial/tenant-change-*/ | Dump и проверки tenant-миграции |
| migration-trial/before-clean-report-*/ | Backup перед пересчётом задач |
| maintenance/20261026-baseline/instructions-before.tar.gz | Прежние инструкции/runtime-скрипты |
| maintenance/20261026-baseline/original-sha256.json | Хеши сохранённых оригиналов |
| maintenance/20261026-baseline/legacy-helpers/ | Одноразовые discovery/configure/audit скрипты, исключены из current |
| maintenance/20261026-baseline/deployment-packages/ | Прежние tar.gz установок/откатов, убраны из домашней папки |
| maintenance/20261026-baseline/collector-test/ | Изолированные тестовые исходники, без настоящих сессий |
| maintenance/20261026-baseline/collector-tests.log | Linux-прогон тестов |
| maintenance/20261026-baseline/daily-collector-test.tar.gz | Пакет тестового прогона |
| maintenance/20261026-baseline/baseline-update.tar.gz | Применённый пакет инструкций/runner |
| maintenance/20261026-baseline/applied.json | Перечень обновлений, хеши и архивированные пути |
| maintenance/20261026-baseline/apply_server_baseline.py | Одноразовое применение уборки с проверкой хешей; повтор отклоняется |
| maintenance/20261026-baseline/timer-verification.json | Контрольный пакет, период, счётчики аккаунтов и проверка повтора |
| /etc/systemd/system/team-activity-collect.service | Запуск, ограничения и повторы |
| /etc/systemd/system/team-activity-collect.timer | Ежедневное расписание и Persistent catch-up |
| /etc/systemd/system/integration-task-manager-kalisto.service | Отключённый rollback для Kalisto |
| /etc/systemd/system/integration-task-manager-mephisto.service | Отключённый rollback для Mephisto |
| /etc/team-activity-access/Caddyfile | Рабочий HTTPS proxy |
| /etc/team-activity-access/access.env | Hostname, auth hashes и proxy token, root-only |
| /etc/team-activity-access/*credentials*.txt | Закрытые реквизиты доступа |
| /etc/team-activity-access/before-*/ | Резервная конфигурация доступа |
| /etc/ssh/sshd_config.d/00-team-activity.conf | Политика SSH по ключам |
| /var/lib/docker/volumes/team-activity-trial_activity_pgdata/ | PostgreSQL: обслуживать средствами БД |
| /var/lib/docker/volumes/team-activity-access_caddy_data/ | Сертификаты/ACME state |
| /var/lib/docker/volumes/team-activity-access_caddy_config/ | Служебное состояние Caddy |

Стандартные файлы ОС, shell, authorized_keys, Docker layers, .venv, node_modules,
__pycache__ и dist — системные/генерируемые файлы, не дополнительные инструкции.
Они не удаляются как «мусор». Архив прежних инструкций не является действующим handoff.

## Следующие этапы

Подключить API/UI к `reporting.*`, затем переработать раскладку и набор метрик.
Collector/timer отключать только отдельным решением после подтверждения, что архивный
контур больше не нужен. Полный перечень — STATUS.md.

## Исполняемые файлы collector

Относительно `/home/teamactivity/team-activity-ingest/current`. Это отдельная рабочая копия.

| Файл | Назначение |
|---|---|
| `README.txt` | Вход в актуальную инструкцию collector |
| `accounts.py` | Раздельные логины, identity, проверка сессий и targets |
| `accounts.sh` | Запуск account-команд из интерактивного терминала teamactivity |
| `bundle.py` | Ограниченный исходный capture, resume и проверка manifest |
| `daily.py` | Суточный checkpoint, повтор, catch-up и FloodWait двух аккаунтов |
| `ingest.py` | Общие функции/блокировки и прежний SQLite collector |
| `partition.py` | Публикация раздельных account inputs и разовых manual exports |
| `requirements.txt` | Зависимости collector (Telethon) |
| `test_accounts.py` | Тесты раздельной авторизации и источников |
| `test_bundle.py` | Тесты неизменяемых пакетов, topics и resume |
| `test_daily.py` | Тесты суточного checkpoint, ошибок, catch-up и повторов без сети |
| `test_ingest.py` | Тесты импорта, ревизий и источников |
| `test_partition.py` | Тесты разделения inputs и manual exports |

## Обновление карты исходников

Ниже — фактические файлы проекта. Назначения заданы в scripts/build-file-map.py;
неизвестный новый файл останавливает генерацию. Обновление: `python3 scripts/build-file-map.py`
из корня проекта. При смене runtime-путей обновлять и таблицу выше.

<!-- FILE_MAP_START -->
## Карта исходников действующего проекта

Все пути в этой таблице относительно `/home/teamactivity/team-activity-explorer`.

| Файл | Назначение |
|---|---|
| `.dockerignore` | Исключения контекста Docker: секреты, данные и лишние файлы |
| `.env.example` | Шаблон переменных без рабочих секретов |
| `.gitattributes` | Правила текстовых файлов и окончаний строк Git |
| `.gitignore` | Запрет попадания частных данных и зависимостей в Git |
| `AGENTS.md` | Правила работы агента с проектом |
| `Dockerfile` | Сборка frontend и runtime-образа backend |
| `PROJECT_HANDOFF.md` | Короткая точка продолжения и ссылки на текущий контракт |
| `README.md` | Описание проекта и вход в документацию |
| `activity/__init__.py` | Пакет backend |
| `activity/access.py` | Роли, разрешения панелей и хранение общей раскладки |
| `activity/account_input.py` | Проверка и адаптация разделённых Telegram API inputs |
| `activity/asana_completion.py` | Проверяемое правило совпадения нейминга и заказанных вариантов |
| `activity/demo.py` | Загрузка явно включённого синтетического демо |
| `activity/domain.py` | Жизненный цикл, фильтры и расчёт метрик |
| `activity/ingest.py` | Нормализация и импорт исходных сообщений/ревизий |
| `activity/proposals.py` | Валидация предложений, импорт, принятие и журнал решений |
| `activity/repository.py` | Чтение PostgreSQL в согласованный snapshot |
| `activity/task_status.py` | Аудируемое ручное завершение/переоткрытие из UI |
| `activity/tenants.py` | Идентификаторы/ограничения тенантов и аккаунтов |
| `app.py` | FastAPI: маршруты, авторизация, отчёт, задачи, статусы и static files |
| `collector/.gitignore` | Исключения локальных данных/секретов collector |
| `collector/README.txt` | Вход в актуальную инструкцию collector |
| `collector/accounts.py` | Раздельные логины, identity, проверка сессий и targets |
| `collector/accounts.sh` | Запуск account-команд из интерактивного терминала teamactivity |
| `collector/backup_daily.py` | Прежний helper резервирования SQLite; не активный timer |
| `collector/bundle.py` | Ограниченный исходный capture, resume и проверка manifest |
| `collector/daily.py` | Суточный checkpoint, повтор, catch-up и FloodWait двух аккаунтов |
| `collector/ingest.py` | Общие функции/блокировки и прежний SQLite collector |
| `collector/install.sh` | Прежний односессионный installer с cron; НЕ использовать для двух аккаунтов |
| `collector/partition.py` | Публикация раздельных account inputs и разовых manual exports |
| `collector/requirements.txt` | Зависимости collector (Telethon) |
| `collector/run.sh` | Прежняя односессионная обёртка; не текущий запуск |
| `collector/setup-telegram.sh` | Прежняя односессионная настройка; использовать accounts.sh |
| `collector/targets.example.json` | Пример разрешённых источников, не рабочий config |
| `collector/test_accounts.py` | Тесты раздельной авторизации и источников |
| `collector/test_bundle.py` | Тесты неизменяемых пакетов, topics и resume |
| `collector/test_daily.py` | Тесты суточного checkpoint, ошибок, catch-up и повторов без сети |
| `collector/test_ingest.py` | Тесты импорта, ревизий и источников |
| `collector/test_partition.py` | Тесты разделения inputs и manual exports |
| `compose.yaml` | Базовые сервисы; на этом VPS обязательно дополнение compose.trial.yaml |
| `deploy/Caddyfile.dashboard` | Шаблон текущего HTTPS/Basic Auth proxy |
| `deploy/Caddyfile.example` | Общий пример proxy, не действующая конфигурация |
| `deploy/compose.dashboard-access.yaml` | Отдельный Compose HTTPS-прокси |
| `deploy/compose.proxy.example.yaml` | Пример proxy для другой установки, не рабочий Compose |
| `deploy/compose.trial.yaml` | Обязательные overrides текущего VPS: БД, порты, статусные записи |
| `deploy/team-activity-collect.service` | Исходник systemd service двух аккаунтов |
| `deploy/team-activity-collect.timer` | Исходник календаря 00:10 Europe/Moscow |
| `docs/ACCESS.md` | SSH/SFTP и роли дашборда |
| `docs/ARCHITECTURE.md` | Архитектура, таблицы и инварианты |
| `docs/BACKEND_SEMANTICS.md` | Точный смысл метрик, дат, фильтров и лимитов |
| `docs/DEPLOYMENT.md` | Обслуживание текущего Compose |
| `docs/EXTRACTION_PROMPT.md` | Правила ИИ-анализа и доказательства завершения |
| `docs/IMPLEMENTATION_CONTRACT.md` | Ссылки на единственные действующие контракты |
| `docs/IMPORT_VERIFICATION.md` | Порядок проверки импорта/review |
| `docs/LAYOUT_EDITOR.md` | Раскладка, хранение и известные дефекты |
| `docs/LOCAL_DEVELOPMENT.md` | Переносимые команды разработки без личных путей |
| `docs/MIGRATION.md` | Сохранённые архивы и требования будущего переноса |
| `docs/SERVER_GUIDE.md` | Основная серверная инструкция и карта файлов |
| `docs/SHIFT_HANDOFF.md` | Правила передачи смены |
| `docs/SHIFT_SCHEDULE.md` | Подтверждённый календарь 2/2 |
| `docs/STATUS.md` | Проверенное текущее состояние и незавершённые этапы |
| `docs/TASK_STATUS.md` | Смена статуса и аудит БД |
| `docs/TELEGRAM_ACCOUNTS.md` | Авторизация и разрешённые источники двух аккаунтов |
| `docs/TELEGRAM_BUNDLES.md` | Пакеты, checkpoints, resume и ограничения охвата |
| `docs/TENANTS.md` | Разделение аккаунтов/тенантов и объединение отчёта |
| `docs/TICKET_BOT_RESEARCH.md` | Статическое сравнение Telegram ticket-ботов и новая концепция, без развёртывания |
| `docs/VERIFICATION.md` | Команды и границы проверок |
| `docs/WORKFLOW.md` | Сбор → анализ → предложения → БД → UI |
| `examples/demo_dataset.json` | Синтетические задачи и события демо |
| `examples/task-proposals.example.json` | Пример формы предложения с вымышленными evidence |
| `examples/task-proposals.schema.json` | Действующий JSON-контракт proposals/events/evidence |
| `index.html` | HTML-каркас дашборда |
| `init/00-users.sh` | Первичная настройка SQL-ролей для пустого volume |
| `init/10-schema.sql` | Таблицы raw/work/ops, ограничения, индексы и права |
| `init/20-tenants.sql` | Аддитивная tenant-миграция и ограничения evidence |
| `init/30-ticketing.sql` | Каноническая PostgreSQL-модель тикетов, событий, диалогов и отчётных витрин |
| `integrations/asana-review-bdm/README.md` | Пакет обновления внешнего Asana-бота, не локальный runtime |
| `integrations/asana-review-bdm/apply.sh` | Проверяемое применение пакета на хосте внешнего бота |
| `integrations/asana-review-bdm/clients.py` | Asana client с чтением секций/полей и обработкой 403/404 |
| `integrations/asana-review-bdm/inspect-live.sh` | Экспорт кода внешнего бота для сверки без секретов |
| `integrations/asana-review-bdm/original-sha256.json` | Отпечатки проверенной исходной версии внешнего бота |
| `integrations/asana-review-bdm/service.py` | Внешний бот: уведомление о переходе в Review BDM |
| `integrations/asana-review-bdm/test_review_bdm.py` | Регрессии переходов Review BDM и совместимости бот-кода |
| `package-lock.json` | Зафиксированные версии frontend-зависимостей |
| `package.json` | Команды и зависимости frontend |
| `requirements-dev.in` | Дополнительные зависимости разработки Python |
| `requirements-dev.lock` | Зафиксированные зависимости разработки Python |
| `requirements.in` | Прямые зависимости Python runtime |
| `requirements.lock` | Зафиксированные зависимости Python runtime |
| `scripts/activate-clean-report.sh` | Одноразовое переключение при согласованном пересчёте; не запускать при обслуживании |
| `scripts/apply-ticketing-migration.sh` | Резервная копия, restore-rehearsal и применение ticketing-схемы |
| `scripts/backup.sh` | Общий pg_dump helper; defaults не заменяют явные параметры текущего VPS |
| `scripts/build-file-map.py` | Генерация этой карты по фактическим файлам |
| `scripts/check-tenant-migration.sh` | Проверка tenant-миграции в отдельной восстановленной БД |
| `scripts/check-trial-access.py` | Проверка SQL-доступа reader/writer |
| `scripts/create-env.py` | Первичная генерация env, не замена рабочих credentials |
| `scripts/dashboard-tunnel.ps1` | Необязательный туннель для диагностики с Windows |
| `scripts/dev.mjs` | Локальный запуск демо/backend/frontend |
| `scripts/dev.ps1` | Windows-обёртка локальной разработки |
| `scripts/export-migration-trial.sh` | Исторический согласованный export; не часть текущего расписания |
| `scripts/preflight.ps1` | Проверка локального Windows checkout и собственного SSH |
| `scripts/prepare-clean-report.sh` | Одноразовая подготовка пересчёта в отдельной БД; не ежедневный runner |
| `scripts/restore-migration-trial.sh` | Восстановление в новую проверочную БД; не перезаписывает рабочую |
| `scripts/start-vps.sh` | Исторический bootstrap другого развёртывания; НЕ запускать на действующем VPS |
| `scripts/trial-sync.sh` | Importer от teamactivity с .env.client и явной рабочей БД |
| `src/PROVENANCE.md` | Происхождение переиспользованных frontend-фрагментов |
| `src/base.css` | Базовое оформление интерфейса |
| `src/charts.js` | Отрисовка графиков и переходы по выбранным периодам |
| `src/layout-model.js` | Схема раскладки, размеры и размещение блоков |
| `src/layout.js` | Редактор: drag/resize, черновик и сохранение |
| `src/main.js` | Загрузка данных, фильтры, реестр, карточки и действия UI |
| `src/schedule.js` | Календарь смен 2/2 и подсказки |
| `src/style.css` | Стили Team Activity Explorer и редактора раскладки |
| `src/utils.js` | Форматирование и безопасный вывод текста |
| `sync.py` | CLI нормализации, импорта сообщений/proposals и review |
| `tests/frontend.test.mjs` | Регрессии метрик UI, календаря и модели раскладки |
| `tests/integration/README.md` | Изолированная PGlite проверка; не запускать на рабочем порту 55432 |
| `tests/integration/check_roundtrip.py` | Проверка импорта/review/прав на синтетической БД |
| `tests/integration/check_tenants.py` | Проверка tenant/evidence ограничений на отдельной БД |
| `tests/integration/check_ticketing.py` | Проверка ticketing-схемы, ролей, идемпотентности и отчётных витрин |
| `tests/integration/package.json` | Зависимости изолированного тестового SQL runtime |
| `tests/integration/server.mjs` | Временный SQL-сервер с синтетическими данными |
| `tests/test_access.py` | Тесты ролей и раскладки |
| `tests/test_api.py` | Тесты маршрутов и защиты API |
| `tests/test_api_revisions.py` | Тесты исходных API-редакций и транспортных изменений |
| `tests/test_asana_completion.py` | Тесты правил завершения Asana |
| `tests/test_domain.py` | Тесты жизненного цикла и метрик |
| `tests/test_ingest.py` | Тесты импорта, ревизий и источников |
| `tests/test_proposals.py` | Тесты валидации/принятия предложений |
| `tests/test_repository.py` | Тесты SQL snapshots и лимитов |
| `tests/test_task_status.py` | Тесты завершения/переоткрытия и идемпотентности |
| `tests/test_tenants.py` | Тесты tenant/account ограничений |
| `vite.config.js` | Настройка сборки и локального frontend-сервера |

Всего файлов исходников проекта: **133**. Секреты и генерируемые данные описаны отдельной таблицей выше.
<!-- FILE_MAP_END -->
