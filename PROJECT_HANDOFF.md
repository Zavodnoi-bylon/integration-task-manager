# Точка входа в проект

Текущая точка отсчёта — **29 октября 2026**.

Основной документ: [SERVER_GUIDE](docs/SERVER_GUIDE.md). Он содержит карту файлов,
фактические адреса и команды для действующего сервера. Актуальные результаты —
[STATUS](docs/STATUS.md), правила цепочки — [WORKFLOW](docs/WORKFLOW.md).

Целевой процесс изменён: два Telegram task-manager бота заменяют парсинг рабочих
переписок как источник новых задач. Kalisto и Mephisto — bot profiles, не AIO-тенанты.
Два администратора общие, технарь у каждого бота свой, клиент видит только свои тикеты.

Текущий этап:

1. PostgreSQL migration, перенос SQLite и переключение обоих systemd units выполнены.
2. Реальный тикет №4 подтвердил публичный диалог, внутреннюю заметку, завершение,
   переоткрытие и отсутствие дублей.
3. Подключить API/UI к `reporting.*`; отдельно решить редактор раскладки.
4. После пользовательской приёмки решить судьбу прежнего Telegram collector/timer.

Текущий production release: `/home/teamactivity/integration-task-manager/releases/20261029Tticketing-v7`.
Каноническая ticket DB: `activity_trial.ticketing`; UI-витрины: `activity_trial.reporting`.
Точные backups, проверки и ещё не наблюдавшиеся сценарии перечислены в STATUS.
