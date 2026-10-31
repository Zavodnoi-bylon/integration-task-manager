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
- автоматическое оформление Destination через OpenRouter: клиент вводит Advertiser
  и исходный оффер, после чего тикет создаётся без предпросмотра и подтверждения;
- исходный оффер сохраняется первым публичным сообщением внутри тикета.

## Архитектура

```text
main.py                  точка входа
config.py                чтение переменных окружения
app/bootstrap.py         сборка Bot и Dispatcher
app/domain/              модели и доменные константы
app/handlers/            Aiogram Router по сценариям
app/services/            бизнес-операции и уведомления
app/views/               тексты и клавиатуры
app/storage.py           выбор SQLite/PostgreSQL
app/sql.py               SQLite-адаптер для разработки и тестов
app/postgres.py          production-адаптер PostgreSQL
content/postbacks/       редактируемые шаблоны postback
content/destination/     системный промпт форматтера Destination
tests/                   регрессионные unit-тесты
compose.yaml             Kalisto и Mephisto в контейнерах
deploy/                  примеры конфигурации и операции развёртывания
scripts/                 миграция и проверка PostgreSQL
```

Зависимости направлены сверху вниз: `handlers → services → storage`. Представления
не обращаются к БД напрямую. Канонический факт — завершившаяся транзакция
PostgreSQL, а не отправленное Telegram-уведомление.

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
`DESTINATION_MODEL`, а путь к инструкции — через `DESTINATION_PROMPT_PATH`.
Если форматирование недоступно, исходный оффер всё равно сохраняется и создаётся
тикет для ручной обработки.

Проверка:

```powershell
.venv\Scripts\python -m unittest discover -s tests -v
```

### Read-only аудит FTD без revenue

Экспериментальный worker читает только таблицу AIO Conversions, фильтрует точный
тип FTD и выводит агрегированную статистику. Он не пишет в БД и не отправляет
Telegram-сообщения. Это намеренно первый этап: расписание, создание тикетов и
уведомления включаются только после проверки dry-run на тестовом окружении.

Добавьте `AIO_TOKEN` и `AIO_TENANT_ID` в локальный `.env`, затем выполните:

```powershell
.venv\Scripts\python aio_ftd_main.py --hours 24
```

Токен передаётся только в query string запроса AIO и не попадает в логи. Клиент
использует таймауты, ограниченные повторы для `429/5xx`, точный серверный фильтр
по UUID типа FTD и прекращает пагинацию на пустой странице даже при некорректном
`next=true` в ответе AIO.

На проверенном tenant API возвращал лимит `60` запросов в минуту. Это наблюдаемое
значение, а не документированный контракт AIO. Worker читает rate-limit headers,
уважает `Retry-After` и делает защитную паузу, если осталось не больше двух
запросов.

Аудит проверяет только текущее значение `revenue`; поле `payout` намеренно не
используется. AIO не возвращает исходный postback и отдельный признак коррекции,
поэтому уже исправленный до опроса нулевой revenue восстановить невозможно. Для
боевого worker нужен checkpoint с перекрывающим окном и дедупликация по паре
`tenant_id + conversion_uuid` в БД — они не подменяются JSON-файлом.

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
- `DESTINATION_MODEL` — модель форматтера; одинаковое значение можно использовать
  в обоих ботах.

Postback-шаблоны:

- Kalisto: `Traffic Angels`, `AI Buying - G`;
- Mephisto: `TheRaw`.

Тексты лежат в `content/postbacks/*.txt` и читаются при каждом запросе.
