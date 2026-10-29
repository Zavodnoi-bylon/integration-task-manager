# Тенанты

| Аккаунт | tenant_id | Общий аккаунт |
|---|---|---|
| account-1 | traffic-angels | Traffic Angels / Kalisto |
| account-2 | theraw | TheRaw / mephisto |

Источники перечислены в TELEGRAM_ACCOUNTS.md. Разовые Arina TL BDM и CBDO относятся
к Traffic Angels и не добавляются автоматически в будущие сборы.

bundle.py сохраняет исходные сообщения отдельно по источникам; partition.py публикует
два account-input файла через staging/rename с проверкой SHA256. Общий manifest технический;
анализ получает отдельные файлы и контексты. Входы не перезаписываются.
Ручные экспорты добавляются только явно в отдельный каталог обработки через partition.py.

sync.py normalize / ingest-messages принимает telegram-account-input.
activity/account_input.py проверяет raw hash, peer, topic, account, период и timezone.
API identity включает account: telegram-api:ACCOUNT:CHAT:MESSAGE. Исторические Desktop
message_key сохраняются; другое представление сообщения не создаёт новую задачу.

init/20-tenants.sql добавляет ops.tenants и ограничения источников/задач/proposals.
Новое предложение обязано иметь external_refs.tenant_id. Нельзя смешивать evidence тенантов,
менять назначенный tenant молча или принимать источник под чужим аккаунтом.
Все текущие задачи размечены после пересчёта; исторические snapshots не переписываются.
UI объединяет принятые задачи обоих тенантов. Pending в метрики не входят.

Совпадение бренда/клиента/GEO не связывает тенанты. Destination уникален и хранится
строкой с ведущими нулями, но одна сущность destination может участвовать в разных работах.
Visit UID, Conversion UUID, click ID и transaction ID — разные идентификаторы.
В чате запросов офферов учитывается только доказанная работа нашего отдела.
Общий аккаунт не определяет сотрудника. Точные правила — EXTRACTION_PROMPT.md.
