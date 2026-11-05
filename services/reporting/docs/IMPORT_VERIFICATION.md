# Проверка импорта

1. Проверить источник, tenant/account, manifest и неизменённые evidence revisions.
2. Выполнить evidence-aware dry-run proposals с normalized messages; syntax_only недостаточен.
3. Импортировать исходники и proposals. Новые proposals должны оставаться pending.
4. Принять только в рамках разрешения пользователя, с expected revision для существующей задачи.
5. Сверить историю, показатели и API. Повтор не должен создавать новые задачи/события.

Синтетические SQL-проверки описаны в tests/integration/README.md; реальные данные
проверяются без записи тестовых задач в рабочую БД. Команды CLI — sync.py --help.
