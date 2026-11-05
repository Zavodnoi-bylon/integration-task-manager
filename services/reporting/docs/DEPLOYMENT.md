# Обслуживание действующего развёртывания

Все команды выполняются на VPS. Основная карта — [SERVER_GUIDE](SERVER_GUIDE.md).
Корень: /home/teamactivity/team-activity-explorer. Compose project: team-activity-trial.

```sh
cd /home/teamactivity/team-activity-explorer
docker compose -p team-activity-trial --env-file .env -f compose.yaml -f deploy/compose.trial.yaml ps
```

Рабочие имена с trial исторические; менять их как часть уборки нельзя: это влияет на БД/volume.
Перед обновлением кода сохранить исходники, образ и отдельный pg_dump.
После проверок frontend/backend обновлять только web с теми же явными Compose-файлами.
Не запускать общий compose без override: базовые defaults отличаются от текущей установки.
Не переинициализировать БД и не выполнять down -v.

```sh
docker compose -p team-activity-trial --env-file .env -f compose.yaml -f deploy/compose.trial.yaml up -d --no-deps --build web
```

Это команда применения уже проверенного изменения, а не диагностическая команда.
Проверить health контейнера и авторизованные API/UI. При сбое вернуть сохранённый образ/код,
не трогая данные. Схема БД меняется отдельной миграцией после dump и реального restore-check.

Caddy управляется отдельным project team-activity-access и deploy/compose.dashboard-access.yaml.
Рабочие файлы: /etc/team-activity-access/. PostgreSQL и web доступны только на loopback,
наружу опубликован HTTPS через Caddy. Таймер коллектора не требует пересборки web.

Read-only Mini App техлида запускается отдельным сервисом и не требует пересоздания web:

```sh
cd /home/teamactivity/team-activity-explorer
docker compose -p team-activity-trial --env-file .env \
  -f compose.yaml -f deploy/compose.trial.yaml -f deploy/compose.lead.yaml \
  up -d --no-deps --build lead
```

Закрытая конфигурация `/etc/team-activity-lead/lead.env` содержит токен входного бота и
allowlist Telegram ID, имеет режим 0600 и не копируется в checkout. Сервис использует только
reader credentials, публикуется на `127.0.0.1:8482`; Caddy снимает префикс `/lead/`.
