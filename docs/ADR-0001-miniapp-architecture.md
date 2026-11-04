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
2. Backend проверяет HMAC своим BOT_TOKEN, auth_date, TTL, структуру user и
   отсутствие дублирующихся полей.
3. Backend регистрирует пользователя в текущем profile, если membership ещё нет.
   Новый обычный пользователь получает client.
4. Staff-role определяется только server configuration и membership.
5. Backend создаёт короткую HttpOnly; Secure; SameSite=Lax session, связанную с
   profile_key + telegram_user_id.
6. Каждая команда повторно проходит actor-aware permission check. Для mutation
   требуется same-origin/CSRF header.

Клиентский JavaScript не получает bot token, DB credentials, raw membership или
возможность выбрать роль/profile.

### Application boundary

Telegram handlers и HTTP transport должны вызывать одни use cases: create general
и Destination, list mine/team, safe ticket view, public message/internal note,
complete/reopen, advertiser catalog и postback templates.

Нельзя отдавать raw get_events клиенту: DTO клиента исключает internal events.
Нельзя вызывать list_technician_tickets без предварительной проверки actor.

### Идемпотентность и транзакции

Каждая mutation принимает UUID Idempotency-Key. Server namespace:

    miniapp:{profile}:{actor}:{operation}:{uuid}

Повтор идентичной операции возвращает прежний результат. Конфликт payload даёт
409. Concurrent/stale complete/reopen не создаёт второе событие.

Перед production Destination aggregate должен создаваться одной DB-командой:
ticket + source public message + system/internal note + notification intents.
OpenRouter вызывается до DB transaction; ошибка модели использует существующий
fallback.

### Уведомления

Production parity требует transactional outbox. Изменение тикета и notification
intent фиксируются одной транзакцией. Worker отправляет Telegram notification
токеном текущего profile, повторяет временные ошибки и дедуплицирует delivery.

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
