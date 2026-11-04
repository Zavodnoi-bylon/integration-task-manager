# HTTP API contract

Префикс: /api. Profile отсутствует во всех request DTO и определяется процессом.
Ответы не содержат Telegram ID, bot token, internal DB ID пользователя или backend
configuration.

## Session

### POST /api/session

Request:

    {"init_data": "<Telegram initData>"}

Проверяет подпись текущего bot token, TTL и onboarding. Устанавливает HttpOnly
session cookie. Response:

    {"role":"client","profile":{"key":"kalisto","name":"Kalisto"},"csrf_token":"..."}

Ошибки: 401 invalid/tampered/stale initData, 403 inactive membership.

### DELETE /api/session

Удаляет текущую session.

### GET /api/me

Возвращает public role/profile capabilities без Telegram ID.

## Tickets

### GET /api/tickets

- scope=mine|team; team только staff;
- status=open|completed;
- page >= 1.

### GET /api/tickets/{ticket_id}

Client получает только собственный ticket и public history. Staff получает ticket
текущего profile с internal history.

### POST /api/tickets

Header Idempotency-Key: UUID.

General:

    {"type":"general","body":"Описание"}

Destination:

    {
      "type":"destination",
      "advertiser_key":"catalog-key-or-new",
      "advertiser_name":"только для new",
      "cap":25,
      "body":"Исходная заявка"
    }

Backend сам получает catalog suffix/notes и не принимает их из request.

### POST /api/tickets/{ticket_id}/messages

Headers Idempotency-Key и X-CSRF-Token.

    {"visibility":"public","body":"Текст"}

internal разрешён только staff.

### POST /api/tickets/{ticket_id}/complete

Только staff. Body пустой объект. Status conflict: 409.

### POST /api/tickets/{ticket_id}/reopen

Client — только собственный ticket; staff — любой ticket текущего profile.

## Profile data

### GET /api/advertisers

Возвращает только key/name каталога текущего profile. Backend suffix не выдаётся.

### GET /api/postbacks

Сохраняет фактический текущий доступ для всех ролей. Возвращает только шаблоны
текущего profile.

## Общие ошибки

- 400 malformed payload;
- 401 отсутствующая/истёкшая session;
- 403 операция запрещена роли;
- 404 ticket не существует либо не виден actor;
- 409 stale state или idempotency conflict;
- 413 body/response limit;
- 422 field validation;
- 503 временно недоступен repository/notification pipeline.

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
