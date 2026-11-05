# Два Telegram-аккаунта

Корень сборщика: /home/teamactivity/team-activity-ingest.
Исполняемые файлы: current/; Python: .venv/bin/python.

| Слот | Тенант / общий аккаунт | Разрешённые источники |
|---|---|---|
| account-1 | Traffic Angels / Kalisto | Tech support; Alert from asana |
| account-2 | TheRaw / mephisto | КОНТОРА, только тема Таски (9); Nelli TheRaw; TheRaw \| запросы офферов |

Числовые chat IDs и topic scope хранятся в accounts/account-N/config.json.
Чаты вне этого списка не собираются. Arina TL BDM и CBDO — ранее добавленные разовые
ручные экспорты Traffic Angels, не регулярные источники.

Проверить действительность обеих сессий от teamactivity:

```sh
~/team-activity-ingest/current/accounts.sh check
```

При необходимости нового входа: accounts.sh login account-1 (либо account-2).
Вводить номер, код и 2FA только в интерактивном терминале. Под root запускать через
`runuser -l teamactivity`, а не создавать root-сессию Telegram.
Пароли и файлы сессий не отправлять в чат. Сессии Desktop не копировать.

Для каждого слота отдельны config.json, identity.json, data/session/user.session и data/run.lock.
telegram-app.json содержит реквизиты приложения; значения не входят в карту/логи.
Изменение состава источников требует явного решения и проверки охвата; дневной runner
останавливается при изменении scope относительно checkpoint, чтобы не пропустить историю.
