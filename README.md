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

Проверка:

```powershell
.venv\Scripts\python -m unittest discover -s tests -v
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

Postback-шаблоны:

- Kalisto: `Traffic Angels`, `AI Buying - G`;
- Mephisto: `TheRaw`.

Тексты лежат в `content/postbacks/*.txt` и читаются при каждом запросе.
