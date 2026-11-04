# HTTP API contract

Префикс: /api. Profile отсутствует во всех request DTO и определяется процессом.
Ответы не содержат Telegram ID, bot token, internal DB ID пользователя или backend
configuration.

Все authenticated POST и DELETE требуют X-CSRF-Token. Create ticket, message,
complete и reopen дополнительно требуют UUID Idempotency-Key. POST /api/session
не требует этих headers, но обязательно проходит exact ALLOWED_ORIGIN check.

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

Возвращает public role/profile capabilities без Telegram ID. Успех: 200.

## Response DTO

Page содержит:

    {
      "items": [],
      "total": 0,
      "page": 1,
      "page_size": 20,
      "has_next": false
    }

TicketSummary содержит ticket_id, profile display name, status, ticket type,
title/description preview, created_at, updated_at и closed_at. Он не содержит
requester Telegram ID, internal user ID или backend metadata.
ticket_id — положительный bigint canonical ticket, не строковый transport ID.

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

- scope=mine|team; team только staff;
- status=open|completed;
- page >= 1, page_size от 1 до server maximum.

Response: Page<TicketSummary>, status 200.

### GET /api/tickets/{ticket_id}

Client получает только собственный ticket и public history. Staff получает ticket
текущего profile с internal history.

Для client существующий чужой ticket, ticket другого profile и отсутствующий
ticket неразличимы: одинаковые status 404 и response body. Adapter обязан
преобразовать object-level denial в ResourceNotFound; transport дополнительно
нормализует AccessDenied на этом endpoint в 404.

Response: ClientTicketDetail либо StaffTicketDetail, status 200.

### POST /api/tickets

Headers: Idempotency-Key UUID и X-CSRF-Token.

General:

    {"type":"general","body":"Описание"}

Destination:

    {
      "type":"destination",
      "advertiser":{"kind":"catalog","key":"catalog-key"},
      "cap":25,
      "body":"Исходная заявка"
    }

Новый advertiser:

    {
      "type":"destination",
      "advertiser":{"kind":"new","name":"Новое название"},
      "cap":25,
      "body":"Исходная заявка"
    }

Advertiser — discriminated union: catalog key либо new name, ровно один вариант.
Backend сам получает catalog suffix/notes и не принимает их из request.
Успех: 201. Повтор того же key/payload возвращает прежний response со status 200.

### POST /api/tickets/{ticket_id}/messages

Headers Idempotency-Key и X-CSRF-Token.

    {"visibility":"public","body":"Текст"}

internal разрешён только staff.
Успех: 201; idempotent replay: 200.

### POST /api/tickets/{ticket_id}/complete

Только staff. Headers Idempotency-Key и X-CSRF-Token. Body пустой объект.
Успех: 200. Status/idempotency conflict: 409.

### POST /api/tickets/{ticket_id}/reopen

Client — только собственный ticket; staff — любой ticket текущего profile.
Headers Idempotency-Key и X-CSRF-Token. Успех: 200. Conflict: 409.

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
