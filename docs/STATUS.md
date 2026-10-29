# Проверенное состояние — 29 октября 2026

Новая рабочая точка отсчёта. Карта и команды — [SERVER_GUIDE](SERVER_GUIDE.md).

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
