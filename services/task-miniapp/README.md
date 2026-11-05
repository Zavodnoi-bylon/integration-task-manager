# dest_bot Mini App

Экспериментальный Telegram Mini App transport для Integration Task Manager.

Репозиторий содержит только интерфейс Mini App, HTTP/auth boundary, контракты,
тестовые адаптеры и проверки. Он не является копией production-ботов и не
содержит их Telegram handlers, PostgreSQL credentials или deployment runtime.

Целевой продукт:

- один общий код Mini App;
- два изолированных запуска: Kalisto и Mephisto;
- полный текущий клиентский и технический flow;
- отдельный read-only бот аналитики, который не входит в этот репозиторий;
- перенос в canonical Integration Task Manager только после review.

Начинать с ADR архитектуры, матрицы ролей и HTTP API-контракта в каталоге docs.
Production deployment из этого репозитория запрещён.

## Реализованный первый слой

- проверка Telegram Web App `initData`;
- opaque server-side sessions, rotation, CSRF и повторная проверка membership;
- actor-aware read-only gateway для профиля текущего server instance;
- `/api/me`, списки/карточка тикетов, advertisers и postbacks;
- domain state machine и mutation API для create/message/review/accept/return/
  Destination complete/reopen;
- canonical PostgreSQL gateway/UOW, persistent sessions, durable idempotency
  reservations и transactional outbox worker;
- versioned migration `0002_miniapp_workflow.sql` и reporting views для review,
  returns и reopens.
- адаптивный Mini App интерфейс для клиента и технаря:
  списки, создание General/Destination, переписка, статусные
  действия, внутренние записи и postback-шаблоны.
- необязательные приватные черновики Destination из пересланных Telegram-
  сообщений: клиент проверяет текст, выбирает advertiser и cap и только затем
  передаёт тикет в работу; до отправки черновик не виден staff и аналитике.

Транзакционная модель, границы PostgreSQL и ограничения outbox
зафиксированы в `docs/ADR-0003-postgresql-runtime.md`.

`MemorySessionStore` предназначен только для тестов и локальной разработки.
Production composition root обязан передать общее persistent session storage и
адаптер к canonical application layer.

Проверка:

    python -m venv .venv
    .venv\Scripts\python -m pip install -e ".[test]"
    .venv\Scripts\python -m unittest discover -s tests -v

PostgreSQL integration tests выполняются только на отдельной test database:

    set TEST_DATABASE_URL=postgresql://...
    .venv\Scripts\python -m unittest discover -s tests\integration -v

`BOT_KEY=kalisto` соответствует `@experementa1_bot`, `BOT_KEY=mephisto` —
`@testmaniken_bot`. `@tiketanalitic_bot` относится к отдельному аналитическому
контуру и этим приложением не используется. Реальные `.env` не коммитятся.

## Локальный test deployment

Mini App собирается одним образом. Compose запускает два изолированных
web-процесса и два outbox worker с разными bot token/env. Секреты
остаются вне Git.

1. Скопировать `deploy/database.env.example`, `deploy/kalisto.env.example`
   и `deploy/mephisto.env.example` в файлы без `.example`.
2. Указать путь к чистому checkout canonical Integration Task Manager:

       set CANONICAL_SOURCE_DIR=C:\path\to\integration-task-manager

3. Указать имя уже существующей PostgreSQL Docker network и запустить:

       set DATABASE_NETWORK=team-activity-trial_backend
       docker compose up --build -d

`migrate` применяет Mini App migrations 0002–0004 после canonical 0001.
Web healthcheck проверяет HTTP и PostgreSQL, worker healthcheck — свежий heartbeat.
Telegram-доставка имеет семантику at-least-once: после неопределённого
сетевого исхода Telegram одно безопасное повторное уведомление возможно.
