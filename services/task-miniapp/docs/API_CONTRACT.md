# HTTP API contract

Префикс: /api. Profile отсутствует во всех request DTO и определяется процессом.
Ответы не содержат Telegram ID, bot token, internal DB ID пользователя или backend
configuration.

Все authenticated POST и DELETE требуют X-CSRF-Token. Workflow ticket mutations
дополнительно требуют UUID Idempotency-Key. Абсолютная установка business-флага
`issued_to_buying` идемпотентна по значению и отдельный key не требует.
POST /api/session не требует этих headers, но обязательно проходит exact
ALLOWED_ORIGIN check.

## Session

### POST /api/session

Request:

    {"init_data": "<Telegram initData>"}

Проверяет подпись текущего bot token, TTL и onboarding. Проверка использует
Telegram WebAppData: sorted decoded fields без hash формируют data_check_string;
secret_key = HMAC-SHA256("WebAppData", BOT_TOKEN), затем calculated_hash =
HMAC-SHA256(secret_key, data_check_string).hexdigest(), constant-time compare.
Duplicate keys отклоняются. Direct HMAC ключом BOT_TOKEN запрещён.

Устанавливает HttpOnly session cookie. Session id создаётся CSPRNG с минимум
256 бит энтропии, server хранит его hash, а повторный login ротирует session.
Response:

    {"role":"client","profile":{"key":"kalisto","name":"Kalisto"},"csrf_token":"..."}

Успех: 201. Ошибки: 401 invalid/tampered/stale initData, 403 inactive membership.

### DELETE /api/session

Удаляет текущую session. Требует связанный с session непредсказуемый
X-CSRF-Token с constant-time проверкой. Server-side record инвалидируется до
удаления cookie. Успех: 204.

### GET /api/me

Возвращает public role/profile capabilities без Telegram ID. Опциональный объект
`links` содержит только явно настроенные server-side HTTPS-переходы; отсутствие
настройки возвращает пустой объект. Bot token и другие backend configuration не
сериализуются. Успех: 200.

### POST /api/analytics/handoff

Доступен только активному пользователю, чей Telegram ID входит в server-side allowlist
текущего профиля. Для остальных `/api/me` не возвращает ссылку, а прямой вызов endpoint
возвращает 403. Пустой allowlist запрещает переход всем.
Требует session-bound `X-CSRF-Token` и пустой JSON body. Backend повторно получает
активного actor из БД и выпускает подписанный HMAC-SHA256 token на 300 секунд. Исходная
роль Task Manager не передаётся: role всегда `analytics_viewer`.

Response:

    {
      "url":"https://explorer.example/#handoff=<compact-token>",
      "expires_at":1794840300
    }

Token содержит `iss`, `sub`, `telegram_user_id`, `bot_uuid`, `profile_id`, `role`,
`iat`, `exp`, `jti`; не содержит `tenant_uuid`, имени, username или bot token. Fragment
не отправляется в HTTP access logs. Полный контракт проверки Explorer описан в
`ANALYTICS_HANDOFF.md`. При отключённой интеграции endpoint возвращает 404.

## Response DTO

Page содержит:

    {
      "items": [],
      "total": 0,
      "page": 1,
      "page_size": 20,
      "has_next": false
    }

TicketSummary содержит ticket_id, ticket_number, profile display name, status, ticket type,
title/description preview, created_at, updated_at, closed_at и текущее булево
`issued_to_buying`. Он не содержит
requester Telegram ID, internal user ID или backend metadata.
ticket_id — положительный bigint canonical ticket, не строковый transport ID. Он остаётся
внутренним идентификатором в path, callback и связях БД. ticket_number — положительный
публичный номер внутри текущего profile; номера разных профилей могут совпадать.

Actor содержит display_name и role без Telegram/internal ID.

ConversationMessage содержит message_id, visibility, body, sent_at и Actor.
Client получает только visibility=public.

ClientTicketDetail содержит разрешённые ticket/status fields и public
conversation. Public history означает только public messages и разрешённые поля
тикета/статуса, но не raw event stream.

StaffTicketDetail содержит те же ticket fields, public conversation и отдельную
internal history. Internal history никогда не сериализуется в client response.

## Tickets

### GET /api/tickets

- scope=mine|team; team доступен staff и supervisor в пределах его связей;
- status=open|review|completed;
- issued_to_buying=true — только supervisor Kalisto, оставляет только отмеченные тикеты;
- page >= 1, page_size от 1 до server maximum.
- search: optional literal substring of the visible title, trimmed, maximum 200
  characters, case-insensitive. Includes the first Destination naming, with the same
  title/description fallback as the list. Profile, actor and status restrictions
  apply before both count and pagination; SQL wildcard characters are literal.

Response: Page<TicketSummary>, status 200.

### GET /api/tickets/{ticket_id}

Client получает только собственный ticket и public history. Staff получает ticket
текущего profile с internal history.

Для client существующий чужой ticket, ticket другого profile и отсутствующий
ticket неразличимы: одинаковые status 404 и response body. Adapter обязан
преобразовать object-level denial в ResourceNotFound; transport дополнительно
нормализует AccessDenied на этом endpoint в 404.

Response: ClientTicketDetail либо StaffTicketDetail, status 200.

### POST /api/tickets/{ticket_id}/buying-flag

Только `supervisor` профиля Kalisto и только для собственного либо связанного
тикета. Body задаёт абсолютное текущее состояние:

    {"issued":true}

Поставить флаг можно только завершённому Destination. Снять уже установленный
флаг можно и после повторного открытия тикета. Операция требует X-CSRF-Token и
безопасна для повтора, потому что не переключает, а задаёт значение.

Флаг не является workflow-статусом: его изменение не создаёт `ticket_event`,
message, internal note или notification. В БД хранится только факт наличия флага
без автора, времени изменения и истории. Response:

    {"ticket_id":7,"issued_to_buying":true}

### POST /api/tickets

Headers: Idempotency-Key UUID и X-CSRF-Token.

General:

    {"type":"general","body":"Описание"}

Destination:

    {
      "type":"destination",
      "advertiser":{"kind":"catalog","key":"catalog-key"},
      "cap":25,
      "cap_mode":"daily",
      "body":"Исходная заявка"
    }

Новый advertiser:

    {
      "type":"destination",
      "advertiser":{"kind":"new","name":"Новое название"},
      "cap":25,
      "cap_mode":"lifetime",
      "body":"Исходная заявка"
    }

Advertiser — discriminated union: catalog key либо new name, ровно один вариант.
`cap_mode` обязателен: `daily`, `monthly`, `lifetime` используют положительный
целый `cap`; `unlim` использует `cap: "uncap"` и не содержит числового лимита.
Backend сам получает catalog suffix/notes и не принимает их из request.
Успех: 201. Повтор того же key/payload возвращает прежний response со status 200.
Mutation response возвращает внутренний `ticket_id` для дальнейших API routes и
публичный `ticket_number` для отображения пользователю.

### POST /api/tickets/{ticket_id}/messages

Headers Idempotency-Key и X-CSRF-Token.

    {"visibility":"public","body":"Текст"}

internal разрешён только staff.
Успех: 201; idempotent replay: 200.

### POST /api/tickets/{ticket_id}/submit-review

Только staff, только general в status=open. Переводит в review и атомарно
создаёт immediate notification автору и один reminder для текущего review-cycle.
Body пустой объект. Успех: 200; conflict: 409.

### POST /api/tickets/{ticket_id}/accept

Только автор general ticket в status=review. Переводит в completed. Staff-автор
может принять собственный ticket, не теряя staff visibility. Body пустой объект.

### POST /api/tickets/{ticket_id}/return

Только автор general ticket в status=review. Переводит в open. Body:

    {"reason":"Что нужно исправить"}

Reason обязателен, сохраняется как public message/event и входит в notification
staff. Reminder текущего review-cycle после выхода из review не доставляется.

### POST /api/tickets/{ticket_id}/complete

Только staff, только Destination в status=open. Переводит напрямую в completed.
Клиенту создаётся нейтральное notification intent без internal note. Body пустой
объект. Успех: 200. Status/idempotency conflict: 409.

### POST /api/tickets/{ticket_id}/reopen

Только автор completed ticket. Headers Idempotency-Key и X-CSRF-Token. Body:

    {"reason":"Проблема повторилась"}

Reason обязателен и сохраняется как public message/event. Успех: 200.
Conflict: 409.

Все mutation endpoints требуют UUID Idempotency-Key и session-bound CSRF.
Ticket/status event, public reason, idempotency record и notification intents
фиксируются одной транзакцией canonical adapter. Telegram HTTP delivery не
выполняется внутри request.

Idempotency namespace:

    miniapp:{profile}:{actor_telegram_user_id}:{operation}:{uuid}

Одинаковый UUID в другом profile или operation независим.

## Черновики из пересланных сообщений

Черновик не является тикетом. Он принадлежит одному автору в одном profile,
не попадает в `/api/tickets`, team queue, reporting views и аналитику. Staff не
получает уведомление до передачи черновика в работу.

Telegram-бот создаёт черновик только из пересланного текста или подписи к
медиа. Сообщение клиента непосредственно перед пересылкой может быть сохранено
как необязательный комментарий. Медиафайл и OCR в первой версии не сохраняются.

### GET /api/drafts

Возвращает активные черновики текущего автора в текущем profile. Успех: 200.

### GET /api/drafts/{draft_id}

Возвращает один активный черновик текущего автора. Чужой, удалённый,
переданный или принадлежащий другому profile черновик неразличимы: 404.

### POST /api/drafts/{draft_id}/save

Требует X-CSRF-Token. Сохраняет редактируемый текст, комментарий, cap, cap_mode и
advertiser. Неполный черновик допустим; это не создаёт тикет.

### DELETE /api/drafts/{draft_id}

Требует X-CSRF-Token. Помечает активный черновик текущего автора удалённым.
Успех: 204.

### POST /api/drafts/{draft_id}/submit

Требует X-CSRF-Token. Требует заполненные body, advertiser, cap и cap_mode. Создаёт
обычный Destination ticket через тот же application service, что и
`POST /api/tickets`, после чего закрывает черновик для повторной передачи.
Создание использует стабильный idempotency key: если запись статуса черновика
оборвалась после commit тикета, повтор запроса восстанавливает связь и не создаёт
дубликат. Успех: 201.

## Review reminder

При каждом переходе general open -> review создаётся новый review-cycle. Для
него планируется ровно один push автору на следующий календарный день в 12:00
Europe/Moscow. Dedup key содержит ticket_id и review-cycle. Перед delivery worker
обязан атомарно подтвердить, что ticket всё ещё review и cycle совпадает.

## Profile data

### GET /api/advertisers

Параметры:

- mode=popular|search|page;
- q только для search;
- page и page_size для пагинации.

Возвращает Page<AdvertiserSummary> с полями key/name только для каталога текущего
profile. Backend suffix и technician note не выдаются.

### GET /api/postbacks

Сохраняет фактический текущий доступ для всех ролей. Возвращает список key/name
шаблонов только текущего profile.

### GET /api/postbacks/{key}

Возвращает один template через canonical PostbackService. Произвольный file/path
или неизвестный template key запрещён.

## Общие ошибки

- 400 malformed payload;
- 401 отсутствующая/истёкшая session;
- 403 операция запрещена роли;
- 404 ticket не существует либо не виден actor;
- 409 stale state или idempotency conflict;
- 413 body/response limit;
- 422 field validation;
- 503 mutation transaction не смогла записать ticket change вместе с notification
  intent либо repository временно недоступен. Ошибка асинхронной Telegram delivery
  после успешного commit не меняет HTTP response.

## Обязательные тесты контракта

- valid/tampered/stale initData;
- initData другого bot token;
- auto-onboarding client и staff role из server config;
- client не видит team, чужой ticket и internal history;
- denial каждого cross-profile query/mutation;
- duplicate/conflicting Idempotency-Key;
- concurrent/stale complete/reopen;
- public/internal visibility;
- general/Destination и model fallback;
- notification intent dedup/retry;
- advertiser/postback profile separation.
