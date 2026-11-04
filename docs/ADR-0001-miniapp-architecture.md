# ADR-0001: Mini App как transport Integration Task Manager

Статус: согласовано для экспериментальной разработки, production review обязателен.

## Контекст

Kalisto и Mephisto — два самостоятельных Telegram-бота с разными профилями,
настройками и внутренними flow. Mini App должен перенести существующий клиентский
и технический функционал в веб-интерфейс, не создавая второй task manager.

Canonical проект уже содержит Settings.bot_key, memberships с ролями client,
technician и admin, TicketService, repository adapters, PostgreSQL ticketing,
Destination, advertiser catalog, postback и Telegram notification services.

Экспериментальный MVP не является production-кодом: в нём есть отдельный HTTP
server, SQLite-расширения и transport-level проверки, которые нельзя переносить
в canonical проект без review.

## Решение

### Топология

Один frontend и один набор HTTP routes запускаются двумя изолированными
инстансами:

- miniapp-kalisto: собственные BOT_KEY=kalisto, BOT_TOKEN и URL;
- miniapp-mephisto: собственные BOT_KEY=mephisto, BOT_TOKEN и URL.

Profile берётся только из server settings. Query/body/cookie не могут выбирать
profile. initData одного бота не проходит HMAC другого.

### Слои

    Telegram WebView
      -> profile-specific auth/HTTP transport
      -> actor-aware application gateway
      -> canonical TicketService / repositories
      -> PostgreSQL ticketing
      -> transactional notification intent/outbox
      -> profile-specific Telegram worker

В этом репозитории application gateway задаётся интерфейсом. Production adapter
реализуется в canonical проекте; тестовый adapter использует только синтетические
данные.

### Авторизация

1. Frontend передаёт Telegram initData в POST /api/session.
2. Backend проверяет Telegram WebAppData по точному двухшаговому алгоритму:
   duplicate keys отклоняются; data_check_string строится из всех decoded fields
   кроме hash, отсортированных по key и соединённых LF; secret_key равен бинарному
   HMAC-SHA256 с key="WebAppData" и message=BOT_TOKEN; calculated_hash равен
   HMAC-SHA256 с key=secret_key и message=data_check_string, представленному
   hexdigest. calculated_hash и hash сравниваются constant-time. Нельзя применять
   SHA256(BOT_TOKEN) или direct HMAC ключом BOT_TOKEN. BOT_TOKEN не логируется.
   Обязательны hash, auth_date и user; configurable max age по умолчанию 300
   секунд; malformed query и malformed user JSON отклоняются.
3. Backend регистрирует пользователя в текущем profile, если membership ещё нет.
   Новый обычный пользователь получает client.
4. Staff-role определяется только server configuration и membership.
5. Backend создаёт opaque server-side session с absolute TTL (default 12 часов).
   Session id генерируется CSPRNG и содержит минимум 256 бит энтропии; server store
   хранит только hash session id. Cookie содержит raw random id: HttpOnly, Secure,
   SameSite=Lax, Path=/, без Domain. Profile-инстансы используют разные origins
   либо разные cookie names. Повторный POST /api/session инвалидирует и ротирует
   предыдущую session.
6. Session связывает identity и profile, но не является источником роли. Active
   membership и staff-role проверяются сервером на каждом request.
7. При создании session absent membership вызывает register_user; inactive
   membership получает 403 без реактивации; active membership обновляет last_seen
   и сверяется с server staff configuration.
8. Для каждой authenticated mutation требуется X-CSRF-Token. Token непредсказуем,
   связан с session и сравнивается constant-time. Logout сначала инвалидирует
   server-side record, затем удаляет cookie. Wildcard CORS запрещён; Origin
   проверяется по exact allowlist, включая POST /api/session. Host, query и body
   не являются источником profile.

Клиентский JavaScript не получает bot token, DB credentials, raw membership или
возможность выбрать роль/profile.

### Application boundary

Telegram handlers и HTTP transport должны вызывать одни use cases: create general
и Destination, list mine/team, safe ticket view, public message/internal note,
complete/reopen, advertiser catalog и postback templates.

Нельзя отдавать raw get_events клиенту: DTO клиента исключает internal events.
Нельзя вызывать list_technician_tickets без предварительной проверки actor.

### Идемпотентность и транзакции

Create ticket, message, complete и reopen принимают UUID Idempotency-Key и
X-CSRF-Token. DELETE session требует X-CSRF-Token, но не Idempotency-Key.
POST session не требует ни CSRF, ни Idempotency-Key. Server namespace:

    miniapp:{profile}:{actor}:{operation}:{uuid}

Повтор с тем же key и payload возвращает прежний response. Тот же key с другим
payload даёт 409. Concurrent/stale complete/reopen не создаёт второе событие.

Перед production Destination aggregate должен создаваться одной DB-командой:
ticket + source public message + system/internal note + notification intents.
OpenRouter вызывается до DB transaction; ошибка модели использует существующий
fallback.

### Уведомления

Production parity требует transactional outbox. Изменение тикета и notification
intent фиксируются одной транзакцией. HTTP mutation успешна после commit обоих
фактов. Worker асинхронно отправляет Telegram notification токеном текущего
profile, повторяет временные ошибки и дедуплицирует delivery; доставка Telegram
не блокирует и не меняет успешный HTTP response.

HTTP handler не отправляет Bot API напрямую и не формирует отдельные тексты.

## Запрещённые переносы из MVP

- SQLite как production storage;
- прямой SQL из HTTP routes;
- второй TicketService;
- profile/role из request;
- raw internal events в client DTO;
- monolithic demo server как production architecture;
- fixtures/runtime/cloudflared/logs/DB;
- отдельная notification logic;
- wholesale merge изменений app/sql.py или TicketService.

## Граница репозитория

Разрешены: auth transport, routes/DTO, gateway protocol, frontend, synthetic test
adapter, tests, ADR/API docs и test-bot tooling без секретов.

Canonical bot handlers, production repositories, migrations, deployment secrets
и реальные данные сюда не копируются.

## Передача в canonical проект

Review получает ADR, API contract, RBAC, чистый diff, PostgreSQL integration-test
contract, Telegram WebView QA checklist и secret/runtime audit. Deployment делает
только canonical-чат после approve и отдельного разрешения пользователя.

## Открытый продуктовый вопрос

Текущий production flow при завершении может отправить клиенту последнюю internal
note как ответ исполнителя. Это противоречит приватности internal note.
Рекомендуемое решение: internal note никогда не показывается клиенту; результат
завершения передаётся отдельным public resolution message либо нейтральным status
notification. Mutation-код не реализуется до фиксации решения.
