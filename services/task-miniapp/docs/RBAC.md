# RBAC Mini App

Profile всегда определяется server instance. Все права ограничены текущим profile.

| Операция | client | technician | admin |
|---|---:|---:|---:|
| Зарегистрироваться в текущем profile | да | да | да |
| Создать general ticket | да | да | да |
| Создать Destination ticket | да | да | да |
| Список собственных тикетов | да | да | да |
| Просмотр собственного тикета/public history | да | да | да |
| Общая очередь текущего profile | нет | да | да |
| Просмотр любого тикета текущего profile | нет | да | да |
| Public message в собственный открытый тикет | да | да | да |
| Public message в любой открытый тикет profile | нет | да | да |
| Просмотр internal notes/events | нет | да | да |
| Добавить internal note | нет | да | да |
| Отправить general на review | нет | да | да |
| Принять свой general в review | да | да | да |
| Вернуть свой general из review с public reason | да | да | да |
| Завершить Destination напрямую | нет | да | да |
| Reopen собственного completed ticket с public reason | да | да | да |
| Reopen любого completed ticket profile | нет | нет | нет |
| Postback templates | да | да | да |
| Cross-profile доступ | нет | нет | нет |

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
- postback остаётся доступен client для паритета текущему handler, пока владелец
  продукта явно не изменит это правило.
