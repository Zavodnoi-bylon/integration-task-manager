# Проверенное состояние — 4 ноября 2026

Новая рабочая точка отсчёта. Карта и команды — [SERVER_GUIDE](SERVER_GUIDE.md).

## Клиентско-техническая Mini App — production включён 4 ноября

- На VPS установлен релиз `dest_bot_miniapp`: текущий source commit
  `e6a3741`, image `dest-bot-miniapp:20261104Tnav-e6a3741`. Он использует каноническую
  `activity_trial`, отдельные persistent sessions/idempotency и transactional outbox.
- Миграция `0002_miniapp_workflow.sql` применена успешно после backup БД:
  `/home/teamactivity/migration-trial/ticketing/20261104Tminiapp-before/`.
  Существующие task-manager, dashboard и read-only analytics Mini App продолжают работать.
- Оба production-профиля запущены: `kalisto-web`/`kalisto-worker` и
  `mephisto-web`/`mephisto-worker` healthy. Публичные URL
  `https://kalisto-mini.172-105-249-196.sslip.io/` и
  `https://mephisto-mini.172-105-249-196.sslip.io/` возвращают успешный health-check.
- Общие и доступные персональные кнопки `Открыть задачи` подтверждены Telegram API у
  рабочих `@IntegrationTECH_bot` и `@Integrationtechs_bot`. Тестовые
  `@experementa1_bot` и `@testmaniken_bot` возвращены к обычному command menu.
  Один настроенный сотрудник Mephisto ещё не запускал рабочего бота (`user not found`);
  после первого `/start` он получит общую Web App-кнопку без изменения конфигурации.
- Список тикетов приведён к компактным однострочным строкам по образцу аналитической
  Mini App; двухколоночные карточки, роль в шапке и буквенные псевдоиконки нижнего меню
  удалены. Длинные нейминги больше не создают горизонтальный overflow. Возврат из
  созданного/открытого тикета снова показывает нижнее меню на основных страницах.
- Перед cutover пользователь провёл ручную проверку обоих тестовых ботов; новых замечаний
  по основной механике не выявлено. Ручной smoke-test уже через production-ботов ещё
  должен быть выполнен пользователем.
- Перед сервером подтверждены 57/57 unit/API-тестов, JavaScript syntax check и 8/8
  PostgreSQL integration tests под ограниченной ролью `ticketing_writer`.
  Аналитика остаётся отдельным read-only сервисом и не использует task-worker.
- Перед переключением сохранён и проверен custom-format dump БД, закрытые test-env и
  runtime: `/home/teamactivity/maintenance/20261104T170659Z-miniapp-production-cutover/`.
  Накопленных outbox-сообщений не было; тестовые Mini App-сессии сброшены перед заменой
  токенов, поэтому рабочие пользователи проходят новую Telegram-аутентификацию.

## Read-only Mini App аналитики — включена 4 ноября

- Отдельный контейнер `team-activity-trial-lead-1` читает рабочую `activity_trial`
  через роль `activity_reader`; root filesystem read-only, наружу порт не опубликован,
  loopback endpoint — `127.0.0.1:8482`.
- Публичная точка входа — `/lead/` на действующем HTTPS dashboard. Основной dashboard
  сохранил Basic Auth; API Mini App требует свежую Telegram `initData`, проверяет HMAC
  и разрешает только явный список техлидов.
- Интерфейс объединяет Kalisto/Mephisto: 7/30/90 дней, текущий backlog, созданные,
  уникальные закрытые, события завершения/переоткрытия, медиану выполнения, возраст
  открытых задач, типы и read-only карточки с перепиской. Telegram ID не выдаются.
- Создание тикетов, ответы, заметки и смена статуса в этом сервисе отсутствуют.
  Рабочие контейнеры Kalisto/Mephisto не менялись и не перезапускались.
- Проверено: 5/5 новых backend-тестов, 19/19 frontend-тестов; полный backend-набор на
  серверном Linux — 78/78. Локально один старый symlink-тест не исполнился из-за права Windows. Серверный image собран,
  контейнер healthy, restart count 0; внутренний и публичный подписанные API-прогоны прошли.
  Неподписанный API возвращает 401, прежний dashboard без Basic Auth — 401.
- Read-only приложение подключено к production `@tiketanalitic_bot`. Общая кнопка
  `Аналитика` и персональная кнопка текущего владельца подтверждены Telegram API;
  прежние кнопки `@experementa1_bot` сброшены. В allowlist два аккаунта, но второй
  ещё не запускал `@tiketanalitic_bot` (`user not found`) и получит общую кнопку после
  первого `/start`.
- Резервная копия до изменения Caddy/Dockerfile:
  `/home/teamactivity/maintenance/20261104Tlead-miniapp-before`.
- Закрытый config перед подключением production-бота:
  `/home/teamactivity/maintenance/20261104T173347Z-analytics-bot-cutover/lead.env`.

## Destination-задачи — каталог и капа включены 31 октября

- Production task-manager commit `e68b51f`: сценарий
  `Новая задача → тип → Advertiser → капа → оффер`. Kalisto показывает 53 advertiser,
  Mephisto — 54; доступны популярные варианты, поиск, страницы и новый advertiser.
- Клиент видит только форму и номер созданной задачи. Название advertiser, капа и исходная
  заявка сохраняются первым публичным сообщением; URL-хвост остаётся backend-конфигурацией.
  Внутренние ошибки клиенту не раскрываются: тикет сохраняется, технарь получает заметку.
  Для нового advertiser заметка — `Новый рекл`, для `V1VA` — выбор advertiser по GEO.
- Тип тикета сделан отдельным отчётным измерением `general` / `destination`, а не полем
  свободного JSON. PostgreSQL-миграция — commit `4d1635e`.
- Restore-check выполнен на отдельной восстановленной PostgreSQL 16, затем схема применена
  к `activity_trial`. Все 5 прежних тикетов сохранены как `general`; добавлены справочник
  типов, внешний ключ, индекс и поля `ticket_type`/`ticket_type_name` в отчётной витрине.
- Уведомление технарю о новом тикете не показывает Telegram ID автора; ID сохраняется
  только как внутренняя связь и аудит в PostgreSQL.
- Оба бота работают на image `integration-task-manager:20261031Tno-tgid-e68b51f`,
  restart count 0; Telegram `getMe` прошёл для обоих. В Linux test image прошли 44/44
  теста; каталоги внутри контейнеров проверены как 53/54 записи.
- DB backup:
  `/home/teamactivity/migration-trial/ticketing/20261031T003453Z/activity_trial-before-ticketing.dump`.
  Source rollback:
  `/home/teamactivity/maintenance/20261031Tdestination-task-before-2493f73`.
  Последний rollback перед каталогом/капой:
  `/home/teamactivity/maintenance/20261031Tcap-before-260bf11`.
  Rollback перед удалением Telegram ID из уведомления:
  `/home/teamactivity/maintenance/20261031Tno-tgid-before-e68b51f`.

## AIO Domain Monitor — приватный канал включён 30 октября

- Production развёрнут из локального commit `2921669`, image
  `aio-domain-monitor:20261030T134052Z`. Канал `AIO · Domain Monitor` проверен через
  Telegram `getChat`; Bot API ID `-1004408071139`.
- Новые проблемные и явно восстановившиеся домены отправляются сразу. Исчезнувший из
  ответа домен не считается восстановившимся, потому что он мог быть архивирован.
  Общая сводка отправляется один раз в день после 10:00 Europe/Moscow; первый baseline
  создан 30 октября без массового алерта.
- Состояние перенесено из `domains.json` в SQLite: текущий snapshot, дневной baseline
  и Telegram delivery outbox записываются атомарно. Старый JSON сохранён для отката.
  После deploy: 424 current, 424 daily, pending outbox 0; тестовое сообщение прошло
  полный путь outbox → bot → приватный канал и отмечено delivered.
- Оба контейнера running, refresh healthy, restart count 0. Проверено 13/13 тестов
  локально и в изолированном Linux image; Telegram и AIO preflight прошли до cutover.
  Старый command-chat сохранён до явной настройки списка личных Telegram ID.
- Polling AIO изменён с пяти минут на один час: `AIO_REFRESH_INTERVAL_SECONDS=3600`,
  запуски выравниваются по началу часа. Это же значение зафиксировано в локальном source
  и проверено внутри production-контейнера. Healthcheck допускает интервал плюс 15 минут,
  поэтому корректный часовой цикл не помечается как устаревший.
- Backup до переключения:
  `/home/teamactivity/maintenance/20261030T131900Z-aio-channel-before-6f7e9ce`.
  Backup перед часовым расписанием:
  `/home/teamactivity/maintenance/20261030T134051Z-aio-hourly-before-44bca9e`.
  Backup перед обновлением healthcheck:
  `/home/teamactivity/maintenance/20261030T134405Z-aio-healthcheck-before-2921669`.
  Серверный source синхронизирован с локальной веткой `codex/channel-alert-flow`;
  отдельный GitHub remote для этого проекта пока не настроен.

## Task-manager и единая БД — включено 29 октября

- Два production-бота работают в контейнерах из одного образа
  `integration-task-manager:20261029T140735Z` и используют общую PostgreSQL
  `activity_trial`. Контейнеры `kalisto` и `mephisto` запущены без опубликованных
  портов, с read-only root filesystem, UID `10001`, `cap_drop=ALL`, лимитами
  0.5 CPU/256 MiB и нулём рестартов. Оба polling-процесса подтверждены в логах.
- Реализован PostgreSQL-слой `ticketing` и отчётные витрины `reporting`:
  bot profiles, пользователи и memberships, тикеты, публичные/внутренние сообщения,
  вложения, неизменяемые события, входные update keys и outbox.
- Роли автора сохраняются на момент события. ID тикетов глобальны; повторные Telegram-
  действия защищены idempotency keys. `activity_reader` получает только чтение,
  приложение пишет через отдельную роль `ticketing_writer` и функции БД.
- Restore-first миграция прошла на PostgreSQL 16; backup перед финальным DDL:
  `/home/teamactivity/migration-trial/ticketing/20261029T093840Z/` (root-only).
  Production SQLite перенесена и оставлена на месте; отдельная согласованная копия:
  `/home/teamactivity/integration-task-manager/backups/20261029T094646Z/database.db`.
- После переноса подтверждено: 7 memberships, 3 тикета, 1 внутренняя заметка,
  3 статусных и 3 created events. Kalisto — 2 тикета, Mephisto — 1. Проверка выполнена
  повторно уже через production PostgreSQL adapter; после контейнеризации оба
  application name видны в `pg_stat_activity` по два соединения каждый.
- У старого тикета Kalisto №1 обнаружено исторически неверное назначение на пользователя,
  не состоящего в профиле. Исполнитель не угадан: в PostgreSQL он `null`, исходная ссылка
  сохранена в `metadata` с `migration_anomaly=assigned_user_not_staff_in_profile`.
- Реальный контрольный тикет №4 в Kalisto прошёл полный цикл после cutover: три публичных
  сообщения от двух сторон, одна внутренняя заметка, завершение и переоткрытие. Итоговый
  статус `open`; 7/7 event idempotency keys и 4/4 message keys уникальны, дублей нет.
- При одном открытии карточки Telegram не ответил на `answerCallbackQuery` за 60 секунд.
  Ошибка относится к сетевому подтверждению кнопки, не к записи БД; следующие updates
  обработаны, прежний systemd не перезапускал процесс (`NRestarts=0`).
- Task-manager опубликован в `Zavodnoi-bylon/integration-task-manager`, production
  checkout — `/home/teamactivity/integration-task-manager/source`, commit `836bca3`.
  Секреты остались только в закрытых server env и не вошли в Git/образ.
- Первый контейнерный cutover намеренно откатился из-за ложного результата проверки
  лога; rollback вернул оба systemd-сервиса. После исправления проверка повторена:
  контейнеры стабильны, legacy units `inactive/disabled` и сохранены для отката.
- Проверки перед Git push: task-manager 18/18 в изолированном test image и на Linux;
  production Compose собран, схема БД проверена через закрытую Docker-сеть; backend 73/73
  на Linux; collector 30/30 на Linux; frontend 19/19 и Vite build. Локальный Windows-
  запуск backend зависал на несовместимой связке `Starlette TestClient/httpx`, поэтому
  итоговый backend-результат подтверждён в серверном Python 3.12 окружении.
- SQL-контракт отдельно проверен backup/restore на PostgreSQL 16.

## Остальные работающие компоненты

- VPS `172.105.249.196`, Ubuntu 24.04; собственный SSH/SFTP проверен.
- Dashboard/API, PostgreSQL и HTTPS-прокси Caddy запущены. Историческое имя рабочей
  БД `activity_trial` сохраняется. Старые `raw/work/ops` не удалены и не смешаны с ticketing.
- В прежнем отчётном контуре сохранено 141 задача, 184 предложения (40 pending),
  2955 исходных сообщений и 276 событий по контрольному снимку 27 октября.
- Общая раскладка и календарь 2/2 работают. Редактор раскладки всё ещё имеет лишние
  отступы, скачки drag и недостаточное уплотнение блоков.
- `viver` читает все панели; состав экрана `tech_lead` ещё не согласован.
- Внешний Asana-бот отправляет Review BDM вместо комментариев; первый реальный переход
  после обновления отдельно не подтверждён.

## Прежний collector

`team-activity-collect.timer` был включён 27 октября на 00:10 Europe/Moscow и прошёл
Linux-тесты/ручной запуск двух аккаунтов. После перехода на task-manager он больше не
является целевым источником новых тикетов, но timer и сохранённые Telegram-сессии этой
миграцией не отключались и не удалялись. Решение об остановке принять отдельно после
приёмки ботов; до этого пакеты считаются архивным входом, а не данными новой отчётности.

## Следующие шаги

1. Перевести backend/UI с прежних `work.*` на витрины `reporting.*`; согласовать метрики
   и панели tech lead.
2. Переделать layout editor на сетку с предсказуемым snap/compact.
3. Настроить внешнюю резервную копию PostgreSQL с периодической restore-проверкой.
4. После приёмки решить, отключать ли прежний collector/timer и архивировать ли его runtime.

GitHub: production-code baseline `team-activity-explorer` — `1ba0a28` (этот status
обновлён последующими docs-коммитами); `integration-task-manager` — `836bca3`.
Рабочие секреты, SQLite, dumps и пользовательские данные не опубликованы.
