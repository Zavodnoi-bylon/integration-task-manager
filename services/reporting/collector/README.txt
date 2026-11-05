Team Activity Explorer — Telegram collector

Серверный корень: /home/teamactivity/team-activity-ingest
Рабочий запуск: .venv/bin/python current/daily.py от teamactivity.
Один ежедневный timer обслуживает account-1 и account-2, входы разделены по тенантам.

Основная инструкция и карта: /home/teamactivity/team-activity-explorer/docs/SERVER_GUIDE.md
Детали: docs/TELEGRAM_ACCOUNTS.md, docs/TELEGRAM_BUNDLES.md, docs/TENANTS.md в проекте.

accounts.py/accounts.sh — авторизация и проверка; bundle.py — исходный capture/resume;
partition.py — два account inputs; daily.py — суточный checkpoint и продолжение.
ingest.py предоставляет общие функции и отдельный legacy SQLite collector.
install.sh/run.sh/setup-telegram.sh/backup_daily.py — прежний односессионный способ,
НЕ устанавливать им расписание для текущих двух аккаунтов. Действующий timer лежит в deploy/.

Тесты: python -m unittest discover -s collector -p 'test_*.py' -v из корня checkout на Linux.
Collector не вызывает модель, не принимает proposals и не записывает задачи в PostgreSQL.
