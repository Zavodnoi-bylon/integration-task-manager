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
