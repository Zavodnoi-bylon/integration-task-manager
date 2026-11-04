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
- никаких mutation endpoint и прямого доступа к production database.

`MemorySessionStore` предназначен только для тестов и локальной разработки.
Production composition root обязан передать общее persistent session storage и
адаптер к canonical application layer.

Проверка:

    python -m venv .venv
    .venv\Scripts\python -m pip install -e ".[test]"
    .venv\Scripts\python -m unittest discover -s tests -v
