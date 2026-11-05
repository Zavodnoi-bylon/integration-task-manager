# Integration Task Manager

Два Telegram-бота для единого тикетного процесса технической команды:
`Kalisto` и `Mephisto`. Клиент создаёт тикет и ведёт публичный диалог в его
карточке; технарь видит тикеты своего бота, добавляет внутренние заметки и меняет
статус. Администраторы имеют полный доступ в обоих профилях.

Production-источник данных — PostgreSQL `activity_trial`, схемы `ticketing` и
`reporting`. Оба бота используют одну БД, разделяют доступ по `profile_key`, а
номера тикетов остаются глобально уникальными. SQLite предназначен только для
локальной разработки и unit-тестов.

Проект основан на MIT-проекте `prasx/HelpDesk`; исходная лицензия сохранена.
Прикладная архитектура, хранилище и production-развёртывание существенно
переработаны.

## Возможности

- атомарное создание тикета с уникальным ID;
- роли `client`, `technician`, `admin` отдельно для каждого профиля;
- открытые и завершённые тикеты с пагинацией;
- публичный диалог внутри тикета;
- внутренние заметки, недоступные клиенту;
- завершение и возврат тикета в работу всеми участниками;
- append-only журнал сообщений и переходов статуса;
- идемпотентность Telegram update/callback на уровне PostgreSQL;
- статичные postback-шаблоны с отдельным набором для каждого бота.
- типы задач `Destination` и `Другая задача`;
- оформление Destination: клиент выбирает Advertiser, указывает капу и отправляет
  исходный оффер, после чего тикет создаётся без предпросмотра и подтверждения;
- поиск, популярные варианты и постраничный просмотр advertiser’ов отдельно для
  Kalisto и Mephisto; URL-параметры хранятся только в backend-каталоге;
- исходный оффер сохраняется первым публичным сообщением внутри тикета.

## Монорепозиторий

Корпоративный GitLab-проект содержит весь рабочий контур таск-менеджера:

```text
app/                     Telegram-боты Kalisto и Mephisto
services/task-miniapp/   рабочий интерфейс клиентов и технарей
services/reporting/      отчётность, аналитическая Mini App и backup-инфраструктура
migrations/              общая PostgreSQL-схема ticketing/reporting
deploy/                  production Compose и операции основного bot runtime
```

`aio-domain-monitor` является отдельным сервисом и намеренно не входит в этот
репозиторий. Секретные env-файлы, дампы и пользовательские данные не копируются
между сервисами и не хранятся в Git.

## Архитектура Telegram-ботов

```text
main.py                  точка входа
config.py                чтение переменных окружения
app/bootstrap.py         сборка Bot и Dispatcher
app/domain/              модели и доменные константы
app/handlers/            Aiogram Router по сценариям
app/services/            бизнес-операции и уведомления
app/views/               тексты и клавиатуры
app/repositories/        типизированная граница хранилища и выбор адаптера
app/sql.py               SQLite-адаптер для разработки и тестов
app/postgres.py          production-адаптер PostgreSQL
migrations/              неизменяемые миграции PostgreSQL с контрольными суммами
content/postbacks/       редактируемые шаблоны postback
content/destination/     системный промпт и backend-каталог advertiser’ов
tests/                   регрессионные unit-тесты
compose.yaml             Kalisto и Mephisto в контейнерах
deploy/                  примеры конфигурации и операции развёртывания
scripts/                 миграция и проверка PostgreSQL
```

Зависимости направлены сверху вниз: `handlers → services → repositories`.
Представления и бизнес-сценарии не обращаются к конкретному SQL-адаптеру напрямую.
Канонический факт — завершившаяся транзакция PostgreSQL, а не отправленное
Telegram-уведомление.

## Миграции PostgreSQL

Структура `ticketing` и её витрины `reporting` версионируются в `migrations/`.
Runner применяет файлы по порядку внутри транзакции, сериализует миграции advisory
lock и сохраняет SHA-256 в `ticketing.schema_migrations`. Уже применённый файл
нельзя незаметно изменить. Для миграции требуется отдельный административный DSN;
рабочая роль бота не имеет DDL-прав.

```bash
export DATABASE_MIGRATION_URL='postgresql://admin:password@host/database'
python scripts/migrate_postgres.py
```

В Git хранится только DDL. DSN, дампы, Telegram ID и реальные тикеты не входят в
репозиторий.

## Локальный запуск

Требуется Python 3.12.

```powershell
python -m venv .venv
.venv\Scripts\python -m pip install -r requirements.txt
Copy-Item .env.example .env
# Заполнить .env локальными тестовыми значениями
.venv\Scripts\python main.py
```

Для Destination-флоу необходим `OPENROUTER_API_KEY`. Модель задаётся через
`OPENROUTER_MODEL`, а путь к инструкции — через `DESTINATION_PROMPT_PATH`.
Если форматирование недоступно, исходный оффер всё равно сохраняется и создаётся
тикет для ручной обработки.

Каталог собирается из очищенной AIO-выгрузки без UUID:

```powershell
.venv\Scripts\python scripts\build_advertiser_catalog.py `
  C:\path\to\aio-advertisers-active.json `
  content\destination\advertisers.json
```

В клиентский интерфейс попадают только шаги формы и номер созданной задачи. Во
внутреннюю обработку передаются выбранное название, его URL-хвост, капа и исходный
текст заявки. Python не перестраивает получившуюся ссылку.
Для `V1VA` URL-хвост пустой, а технарю автоматически создаётся внутренняя пометка
`Выбрать advertiser для нужного GEO`.
При выборе `Новый advertiser` введённое клиентом название передаётся модели с
пустым URL-хвостом, а технарю создаётся внутренняя пометка `Новый рекл`.
Advertiser, капа и исходная заявка сохраняются первым сообщением внутри тикета;
капа не добавляется в отдельную отчётную сущность.

Проверка:

```powershell
.venv\Scripts\python -m unittest discover -s tests -v
```

### Алерты FTD без revenue

Отдельный worker этого же проекта читает AIO Conversions каждые десять минут,
фильтрует точный тип FTD и ставит в очередь конверсии с `revenue = 0`. Состояние,
дедупликация и доставка хранятся в PostgreSQL; ключ события —
`tenant_uuid + conversion_uuid`. Checkpoint обновляется только после успешного
получения всех страниц, а десятиминутное перекрытие защищает от задержек API.
Опрос привязан к минутам `:02/:12/:22/:32/:42/:52`, чтобы не пересекаться с
часовым запросом `aio-domain-monitor` в начале часа.

На первом запуске worker только фиксирует текущий момент как начало мониторинга:
исторические события в Telegram не отправляются. Существующие боты и их `.env`
не используются и не изменяются; worker получает отдельный `aio-ftd.env`, но
может отправлять сообщения через уже созданного бота.

Перед включением выполните read-only аудит. Добавьте только `AIO_TOKEN` и
`AIO_TENANT_ID` в локальный `.env`, затем выполните:

```powershell
.venv\Scripts\python aio_ftd_main.py --hours 24
```

Команда выводит только агрегированные счётчики, не пишет в БД и не отправляет
сообщения. Токен передаётся только в query string запроса AIO и не попадает в логи. Клиент
использует таймауты, ограниченные повторы для `429/5xx`, точный серверный фильтр
по UUID типа FTD и прекращает пагинацию на пустой странице даже при некорректном
`next=true` в ответе AIO.

На проверенном tenant API возвращал лимит `60` запросов в минуту. Это наблюдаемое
значение, а не документированный контракт AIO. Worker читает rate-limit headers,
уважает `Retry-After` и делает защитную паузу, если осталось не больше двух
запросов.

Worker проверяет только текущее значение `revenue`; поле `payout` намеренно не
используется. Если API помечает строку `is_correction`, она исключается. AIO не
возвращает исходный postback, поэтому уже исправленный до опроса нулевой revenue
восстановить невозможно.

Подготовка без запуска production-алертов:

```bash
sudo bash deploy/apply-aio-ftd-migration.sh
install -m 600 deploy/aio-ftd.env.example \
  /home/teamactivity/.config/integration-task-manager/aio-ftd.env
# Вписать AIO_TOKEN, AIO_TENANT_ID, AIO_ALERT_BOT_TOKEN и AIO_ALERT_CHAT_ID.
```

Разовая инициализация checkpoint (не отправляет историю):

```bash
sudo bash deploy/compose.sh --profile aio-ftd run --rm aio-ftd \
  python aio_ftd_main.py --once
```

Запуск постоянного мониторинга выполняется только после проверки dry-run и
явного разрешения владельца:

```bash
sudo bash deploy/compose.sh --profile aio-ftd up -d aio-ftd
sudo bash deploy/compose.sh --profile aio-ftd logs --tail 100 aio-ftd
```

## Локальная проверка черновиков

Изолированный контур запускается через `compose.local-draft.yaml`. Он использует
отдельный PostgreSQL volume и не подключается к production database. Секреты
хранятся только в игнорируемом Git файле `deploy/kalisto.draft-local.env`.

Перед локальным polling нужно остановить другой экземпляр того же тестового
бота. Затем запустить инфраструктуру без bot process:

```powershell
docker compose -f compose.local-draft.yaml up --build -d postgres canonical-migrate mini-migrate mini-web mini-worker tunnel
docker compose -f compose.local-draft.yaml logs --no-log-prefix tunnel
```

Из логов взять `https://...trycloudflare.com`, записать его без завершающего `/`
в `ALLOWED_ORIGIN`, настроить этот же URL как menu button тестового бота и
запустить polling:

```powershell
docker compose -f compose.local-draft.yaml up -d --force-recreate mini-web mini-worker bot
docker compose -f compose.local-draft.yaml ps
```

Quick Tunnel получает новый URL после пересоздания контейнера `tunnel`; в таком
случае `ALLOWED_ORIGIN` и menu button требуется обновить. Остановка контура без
удаления тестовой БД:

```powershell
docker compose -f compose.local-draft.yaml stop
```

## Production в Docker

Один неизменяемый образ запускается двумя сервисами. У каждого свой токен,
технарь и `BOT_KEY`; администраторы могут совпадать. Контейнеры работают от UID
`10001`, с read-only root filesystem, без Linux capabilities и без опубликованных
портов. Лимит каждого экземпляра: 0.5 CPU, 256 MiB RAM, 128 процессов.

Секреты не входят в Git и образ. На сервере они находятся в:

```text
/home/teamactivity/.config/integration-task-manager/database.env
/home/teamactivity/.config/integration-task-manager/kalisto.env
/home/teamactivity/.config/integration-task-manager/mephisto.env
```

Формат показан в `deploy/*.env.example`. Файлы должны быть доступны только
владельцу. `database.env` на хосте может сохранять loopback-настройки: Compose
явно заменяет адрес на DNS-имя `postgres:5432` внутри закрытой сети.

Боты подключаются к двум сетям:

- `team-activity-trial_backend` — существующая внутренняя сеть PostgreSQL;
- собственная `egress` — только для исходящих запросов к Telegram.

Проверенный переход с legacy systemd выполняет `deploy/deploy-compose.sh`. Он:
собирает отдельный test image, запускает тесты без сети, проверяет схему БД,
останавливает старые units, запускает оба контейнера и автоматически возвращает
systemd-сервисы при ошибке. Старые release и units не удаляются.

```bash
sudo bash deploy/deploy-compose.sh
```

Текущее состояние и логи:

```bash
sudo bash deploy/compose.sh ps
sudo bash deploy/compose.sh logs --tail 100 kalisto mephisto
```

Ручной откат:

```bash
sudo bash deploy/compose.sh down
systemctl enable --now integration-task-manager-kalisto.service
systemctl enable --now integration-task-manager-mephisto.service
```

## Конфигурация ролей

- `ADMIN_USERS` — Telegram ID администраторов; список через запятую.
- `TECHNICIAN_USERS` — Telegram ID технаря текущего бота.
- `ADMIN_MESSAGE` — получатель уведомлений о новых тикетах; должен входить в один
  из двух списков выше.
- `BOT_KEY` — строго `kalisto` или `mephisto`.
- `OPENROUTER_API_KEY` — ключ OpenRouter для автоматического оформления Destination.
- `OPENROUTER_MODEL` — модель форматтера; одинаковое значение можно использовать
  в обоих ботах.
- `DESTINATION_CATALOG_PATH` — путь к backend-каталогу advertiser’ов.

Postback-шаблоны:

- Kalisto: `Traffic Angels`, `AI Buying - G`;
- Mephisto: `TheRaw`.

Тексты лежат в `content/postbacks/*.txt` и читаются при каждом запросе.
