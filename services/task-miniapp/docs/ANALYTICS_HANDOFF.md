# Analytics handoff: Task Manager -> Explorer

Кнопка `Аналитика` не передаёт Telegram `initData` и не доверяет данным браузера.
Mini App выполняет authenticated `POST /api/analytics/handoff`; backend повторно
проверяет активное членство пользователя, создаёт одноразовый короткоживущий token и
возвращает URL Explorer. Срок token по умолчанию — 300 секунд.

До показа кнопки и выпуска token backend проверяет server-side allowlist текущего
профиля. Если Telegram ID отсутствует в `ANALYTICS_ALLOWED_USER_IDS`, кнопка скрыта, а
прямой вызов endpoint возвращает 403. Пустой список запрещает переход всем.

## Transport

URL имеет вид:

    https://explorer.178-62-218-121.sslip.io/#handoff=<compact-token>

Token находится во fragment, поэтому браузер не отправляет его в HTTP request line и
обычные access logs. Страница Explorer должна немедленно извлечь fragment, удалить его
из адресной строки через `history.replaceState`, отправить token своему backend по
HTTPS POST и после успешного обмена создать собственную session cookie (`Secure`,
`HttpOnly`, подходящий `SameSite`). Token нельзя сохранять в localStorage, логи или URL.

## Подпись

Формат token — compact JWS/JWT из трёх base64url-частей. Подпись:

    HMAC-SHA256(base64url(header) + "." + base64url(payload), secret)

Header:

    {"alg":"HS256","kid":"<bot_uuid>","typ":"JWT"}

`kid` используется только для выбора заранее настроенного ключа. Explorer обязан
принудительно ожидать `HS256`, а не выбирать алгоритм из недоверенного header. У Kalisto
и Mephisto разные 256-битные secrets, `bot_uuid`, `profile_id` и issuer.

Payload:

    {
      "iss": "theraw-task-manager",
      "sub": "telegram:123456789",
      "telegram_user_id": 123456789,
      "bot_uuid": "<uuid>",
      "profile_id": "<uuid>",
      "role": "analytics_viewer",
      "iat": 1794840000,
      "exp": 1794840300,
      "jti": "<random-uuid>"
    }

`tenant_uuid`, имя и username не передаются. Исходная роль пользователя в Task Manager
тоже не передаётся: разрешённый активный пользователь получает только
`analytics_viewer`.
Уникальная внешняя личность Explorer — `(bot_uuid, telegram_user_id)`.

## Обязательная проверка Explorer

1. Разделить compact token на три части и ограничить его допустимый размер.
2. По `kid` найти ровно одну локальную конфигурацию интеграции; неизвестный `kid`
   отклонить.
3. Проверить HMAC-SHA256 constant-time сравнением и только после этого доверять payload.
4. Проверить совпадение `iss`, `bot_uuid`, `profile_id` с конфигурацией выбранного ключа.
5. Проверить `sub == "telegram:" + telegram_user_id`, целый положительный Telegram ID,
   `iat`, `exp`, срок `exp - iat <= 300` и текущее время с небольшим clock skew.
6. Принять только роль из allowlist: сейчас ровно `analytics_viewer`.
7. Атомарно зарегистрировать `(bot_uuid, jti)` до `exp`; повторный обмен отклонить.
8. Создать собственную Explorer session и больше не использовать handoff token.

## Конфигурация бота

Все поля обязательны вместе; при неполной конфигурации Mini App не стартует:

    ANALYTICS_URL=https://explorer.178-62-218-121.sslip.io
    ANALYTICS_ISSUER=theraw-task-manager
    ANALYTICS_BOT_UUID=<uuid>
    ANALYTICS_PROFILE_ID=<uuid>
    ANALYTICS_SIGNING_SECRET=<base64url без padding, минимум 32 random bytes>
    ANALYTICS_ALLOWED_USER_IDS=123456789,987654321
    ANALYTICS_HANDOFF_TTL_SECONDS=300

Secret хранится только в закрытых env обоих backend-компонентов. Во frontend, Git,
Telegram и API response secret не попадает.
