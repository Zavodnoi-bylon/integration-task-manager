# Отдельный запуск на VPS — команды для пользователя

> Историческая инструкция первичного пилота от 22 октября. Проект уже работает
> на старом VPS. Для переноса существующих данных читать MIGRATION.md и STATUS.md;
> не запускать этот bootstrap повторно поверх работающей установки.

**Статус:** эти команды подготовлены для проверки и самостоятельного выполнения.
В ходе аудита они не выполнялись на VPS. Sudo-пароль вводится только пользователем
в своём терминале; его не нужно передавать в чат. Существующие проекты не заменяются.

Начальный запуск доступен через SSH-туннель. Публичный домен и изменение Caddy
сознательно выделены в последующий этап, поскольку активная конфигурация proxy
не входит в предоставленный архив.

## 1. Read-only проверка сервера перед любыми изменениями

Подключиться из PowerShell:

```powershell
ssh -o BatchMode=yes -o ConnectTimeout=8 -i "$env:USERPROFILE\.ssh\vaultwarden_codex" vaultadmin@167.99.36.155
```

На сервере:

```bash
id
df -h / /opt /home
free -h
ss -ltn '( sport = :8480 )'
test ! -e /opt/team-activity-explorer
```

Последняя команда должна завершиться с кодом 0. Если каталог уже существует,
нужно сначала проверить его содержимое и владельца; не копировать поверх.
Если порт 8480 занят, выбрать отдельный loopback-порт в новом compose и туннеле.

Команды с sudo — только для чтения состояния Docker:

```bash
sudo docker compose version
sudo docker compose ls
sudo docker ps --format 'table {{.Names}}\t{{.Status}}\t{{.Ports}}'
sudo docker volume ls --filter label=com.docker.compose.project=team-activity-explorer
sudo docker network ls --filter label=com.docker.compose.project=team-activity-explorer
```

Проверить, что новый project name и его ресурсы ещё не используются. Не выводить
`docker inspect` целиком, `docker compose config` без `--quiet`, `.env` или `/proc/*/environ`:
они могут раскрыть credentials. Недостаток ресурсов — основание отложить запуск,
а не останавливать AIO/TheRaw/Vaultwarden для освобождения памяти.

## 2. Резервная копия предоставленных исходников

Сначала сохранить исходный очищенный ZIP локально. Его SHA-256:

```text
44022ba5f33383f45916d689d081a8c716861fec89e25ff35a98b3aa95b69f92
```

Для новой очищенной копии кода на VPS можно использовать явный список файлов
из исходного архива. Не применять `tar /opt/...` без проверенного allowlist:
рядом могут находиться `.env`, state и ключи. В этом аудите исходный ZIP и все
распакованные файлы сохранены неизменными; новые файлы находятся в другом каталоге.
Новая БД перед первым запуском ещё не существует, поэтому её резервировать пока нечего.

## 3. Передать и распаковать новый проект в домашнем каталоге

Скачать `team-activity-explorer.zip` на свой компьютер. В PowerShell подставить
путь к скачанному файлу:

```powershell
scp -i "$env:USERPROFILE\.ssh\vaultwarden_codex" "$env:USERPROFILE\Downloads\team-activity-explorer.zip" vaultadmin@167.99.36.155:/home/vaultadmin/team-activity-explorer.zip
```

На сервере распаковать **новый** архив сначала в staging. Он содержит верхний каталог
`team-activity-explorer/`, исходные TheRaw/AIO в него не включены.

```bash
(
  set -eu
  test ! -e /home/vaultadmin/activity-staging
  mkdir -m 700 /home/vaultadmin/activity-staging
  python3 -m zipfile -e /home/vaultadmin/team-activity-explorer.zip /home/vaultadmin/activity-staging
)
```

Просмотреть README, compose и Dockerfile в staging. Файл `.env.example` содержит
только новые placeholders; реального `.env` в архиве нет.

## 4. Создать отдельный каталог — первые изменяющие sudo-команды

Выполнить только после успешных проверок раздела 1:

```bash
(
  set -eu
  test ! -e /opt/team-activity-explorer
  sudo install -d -m 0750 -o vaultadmin -g vaultadmin /opt/team-activity-explorer
  sudo cp -a /home/vaultadmin/activity-staging/team-activity-explorer/. /opt/team-activity-explorer/
  sudo chown -R vaultadmin:vaultadmin /opt/team-activity-explorer
)
```

Команды касаются исключительно нового пути. Затем без sudo:

```bash
cd /opt/team-activity-explorer
python3 scripts/create-env.py
mkdir -p data/inbox
chmod 700 data data/inbox
```

Генератор создаёт новые разные пароли в `.env` с правами 0600, не выводит значения
и отказывается перезаписывать существующий файл. Не копировать старые credentials.
Сохранить credentials вне сервера в согласованном хранилище отдельно от резервной
копии БД; не передавать их в чат и не добавлять `.env` в Git.

Архив поставляется без `.git`; для рабочего репозитория на VPS:

```bash
git init -b main
git add README.md app.py sync.py activity src index.html package.json package-lock.json vite.config.js requirements.in requirements-dev.in requirements.lock requirements-dev.lock Dockerfile compose.yaml init scripts deploy docs examples tests .gitignore .dockerignore .env.example
git status --short
```

Проверить список staged-файлов перед `git commit`. Настроить собственные Git name/email
локально и сделать первый commit. Remote в этой версии не настроен.

## 5. Проверить Compose и запустить только новый проект

```bash
(
  set -eu
  sudo docker compose --project-directory /opt/team-activity-explorer config --quiet
  sudo docker compose --project-directory /opt/team-activity-explorer up -d --build postgres web
  sudo docker compose --project-directory /opt/team-activity-explorer ps
)
```

`init/` запускается только для **нового пустого volume**. Если volume уже существует,
изменение init SQL само по себе не мигрирует схему. Не выполнять `down -v` ради
«починки»: это удалит данные. Для следующей версии нужна явная миграция и backup.

Для production перед установкой зафиксировать digest фактически выбранных образов
postgres:16-bookworm, python:3.12-slim-bookworm и node:24-bookworm-slim. Docker image
build и потребление ресурсов на этом VPS в локальной среде аудита не проверялись.

## 6. Read-only проверки после запуска

```bash
curl --fail --silent --show-error http://127.0.0.1:8480/api/health
curl --fail --silent --show-error http://127.0.0.1:8480/api/meta
```

Health должен показать `{"ok":1,"mode":"postgres"}`, meta — `demo:false`,
частичный охват и отсутствие подтверждённых задач. Пустой экран в этом состоянии
корректен: фиктивных задач в production image нет.

Проверка SQL-полномочий без изменения данных:

```bash
sudo docker compose --project-directory /opt/team-activity-explorer exec -T postgres psql -U activity_admin -d activity -v ON_ERROR_STOP=1 -c "SELECT has_table_privilege('activity_reader','work.tasks','SELECT') AS can_read, has_table_privilege('activity_reader','work.tasks','INSERT') AS can_insert, has_table_privilege('activity_reader','work.tasks','UPDATE') AS can_update, has_schema_privilege('activity_reader','work','CREATE') AS can_create;"
```

Ожидается `true, false, false, false`. Проверить также, что старые контейнеры
сохранили своё состояние через `sudo docker ps --format ...` из раздела 1.

## 7. Открыть UI через отдельный туннель

В новом окне PowerShell:

```powershell
ssh -N -o ExitOnForwardFailure=yes -L 8480:127.0.0.1:8480 -i "$env:USERPROFILE\.ssh\vaultwarden_codex" vaultadmin@167.99.36.155
```

Открыть `http://127.0.0.1:8480/`. Туннель не использует порт 8443 Vaultwarden.
На сервере порт 8480 привязан только к loopback, Postgres-порт наружу не опубликован.
Пароля UI в этой фазе нет: доступ обеспечивается авторизованным SSH-туннелем.

## 8. Загрузить небольшой проверяемый набор

Поместить очищенный JSON экспорта в `/opt/team-activity-explorer/data/inbox/chat.json`
и задать права 0600. Не импортировать демонстрационный пример в рабочую БД.
CLI запускается с UID/GID текущего оператора, поэтому может читать его приватные
файлы из read-only bind mount без расширения прав на данные.

Пробный разбор без записи:

```bash
cd /opt/team-activity-explorer
sudo docker compose run --rm --user "$(id -u):$(id -g)" cli ingest-messages /inbox/chat.json --dry-run
```

Явная загрузка сообщений, затем предложений из `tasks.json`:

```bash
sudo docker compose run --rm --user "$(id -u):$(id -g)" cli ingest-messages /inbox/chat.json
sudo docker compose run --rm --user "$(id -u):$(id -g)" cli ingest-proposals /inbox/tasks.json
```

Предложения не меняют статистику. После просмотра конкретного предложения и
его доказательств оператор выполняет отдельную команду, подставив настоящий ID:

```bash
sudo docker compose run --rm --user "$(id -u):$(id -g)" cli review PROPOSAL_ID --decision accept --actor vaultadmin --expected-revision 0
```

`0` означает новую задачу. Для исправления существующей карточки использовать
её текущую revision. Не принимать весь набор автоматически только потому, что
JSON прошёл схему: корректность ссылок не доказывает правильность интерпретации.

## 9. Резервная копия и пробное восстановление

Скрипт ниже содержит только `pg_dump` новой БД. Он не печатает пароли и делает
окончательный файл только после успешного завершения dump. Запускать от vaultadmin:

```bash
bash /opt/team-activity-explorer/scripts/backup.sh /home/vaultadmin/activity-backups
```

Внутри выполняется следующая sudo-операция чтения:

```bash
sudo docker compose --project-directory /opt/team-activity-explorer exec -T postgres pg_dump -U activity_admin -d activity --format=custom
```

Дамп содержит тексты сообщений и историю, хранить с ограниченным доступом.
Копию нужно переносить вне этого VPS. Встроенного расписания backup пока нет.

Восстановление сначала проверяется в **новой отдельной** БД, не в `activity`:

```bash
(
  set -eu
  sudo docker compose --project-directory /opt/team-activity-explorer exec -T postgres createdb -U activity_admin activity_restore_check
  sudo docker compose --project-directory /opt/team-activity-explorer exec -T postgres pg_restore -U activity_admin -d activity_restore_check --exit-on-error < /home/vaultadmin/activity-backups/REPLACE_WITH_BACKUP.dump
  sudo docker compose --project-directory /opt/team-activity-explorer exec -T postgres psql -U activity_admin -d activity_restore_check -c 'SELECT count(*) FROM work.tasks;'
)
```

Если `activity_restore_check` уже существует, команда createdb должна остановить
процедуру. Не переиспользовать её без проверки. В основной БД ничего не заменяется.
Удаление тестовой БД — отдельное осознанное действие после сверки.

## 10. Остановить пилот без удаления данных

```bash
sudo docker compose --project-directory /opt/team-activity-explorer stop web
```

При необходимости остановить и новую БД:

```bash
sudo docker compose --project-directory /opt/team-activity-explorer stop postgres
```

Volume остаётся. Старые Compose-проекты эти команды не затрагивают.

## 11. Публичный домен — после пилота

Предлагаемое имя: `activity.167-99-36-155.sslip.io` либо собственный домен.
Доступность DNS и сертификата нужно проверить заново в момент настройки.
`deploy/Caddyfile.example` и `deploy/compose.proxy.example.yaml` — заготовки,
а не команды для автоматического изменения действующего Caddy.

Перед этим этапом нужны read-only проверка реального Caddy Compose/Caddyfile,
резервная копия его конфигурации, выбранное имя Docker proxy-сети и новые
Basic Auth credentials. Точные команды изменения и `caddy validate`/reload
можно сформировать только после этой проверки. Не копировать старый installer
TheRaw и не подменять его домен строковой заменой.

