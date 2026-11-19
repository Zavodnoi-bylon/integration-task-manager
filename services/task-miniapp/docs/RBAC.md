# RBAC Mini App

Profile всегда определяется server instance. Все права ограничены текущим profile.

| Операция | client | supervisor | technician | admin |
|---|---:|---:|---:|---:|
| Зарегистрироваться в текущем profile | да | да | да | да |
| Создать general/Destination ticket | да | да | да | да |
| Список собственных тикетов | да | да | да | да |
| Просмотр собственного тикета/public history | да | да | да | да |
| Тикеты назначенных подчинённых/public history | нет | да | да | да |
| Общая очередь текущего profile | нет | нет | да | да |
| Public message в доступный открытый тикет | свой | свой/подчинённого | любой | любой |
| Просмотр или добавление internal notes | нет | нет | да | да |
| Отправить general на review | нет | нет | да | да |
| Принять/вернуть свой general в review | да | да | да | да |
| Завершить Destination напрямую | нет | нет | да | да |
| Reopen собственного completed ticket | да | да | да | да |
| Поставить/снять «Выдано баингу» на доступном Destination | нет | да, Kalisto | нет | нет |
| Фильтр «Выданные» | нет | да, Kalisto | нет | нет |
| Техническое действие над тикетом подчинённого | нет | нет | да | да |
| Postback templates | да | да | да | да |
| Cross-profile доступ | нет | нет | нет | нет |

Admin-specific ticket operation сейчас отсутствует; искусственно добавлять её в
Mini App не нужно. Assign, priority, tags, SLA, delete, edit и attachments UI не
входят в фактический текущий flow.

## Инварианты

- client DTO не содержит internal note даже при завершении;
- internal visibility определяется текущей ролью, а не авторством: staff-автор
  видит internal history, client-автор не видит её никогда;
- client не задаёт requester, actor или profile;
- technician/admin не могут пересечь profile;
- inactive membership не авторизует session;
- staff-role не создаётся из client onboarding;
- supervisor не является staff, не видит internal history и получает команду
  только через profile-scoped связи в PostgreSQL;
- supervisor сохраняет author-only действия только на собственных тикетах;
- «Выдано баингу» — отдельный business-флаг Kalisto, а не статус тикета:
  хранится только текущее значение без автора, времени, истории, internal note,
  event и notification; поставить его можно на завершённый Destination, снять —
  в любой момент с доступного Destination;
- postback остаётся доступен client для паритета текущему handler, пока владелец
  продукта явно не изменит это правило.
