# Проверки

Из корня checkout с установленными зависимостями:

```sh
python -m unittest discover -s tests -v
python -m unittest discover -s collector -p 'test_*.py' -v
node --test tests/frontend.test.mjs
npm run build
```

Полные collector-тесты выполняются на Linux. Ошибка доступа Windows к temp/symlink
не доказывает ошибку приложения; результат другого окружения указывать отдельно.
Тесты не должны использовать настоящие Telegram-сессии или рабочую БД.

Для изменений SQL: отдельная восстановленная PostgreSQL 16, проверки tenant/evidence,
идемпотентности и прав. PGlite — дополнительный тест, не замена реального restore.
Миграция task-manager выполняется только через `scripts/apply-ticketing-migration.sh`:
скрипт делает `pg_dump`, восстанавливает отдельную БД, применяет DDL, проверяет роли,
аудит, два bot profile и витрины, и лишь затем меняет `activity_trial`. После этого
одноразовый перенос SQLite сверяется по тикетам, пользователям и событиям до переключения
systemd units. Исходная SQLite и dump не удаляются.
Для таймера: systemd-analyze verify, календарь с Europe/Moscow, живой запуск unit,
проверка двух account inputs и повтор без новых Telegram-запросов.
Для UI: tests/build плюс браузерная проверка изменённого поведения.
Фактически выполненные проверки и ограничения записывать в STATUS, не приписывать
этому документу вечный успешный результат.
