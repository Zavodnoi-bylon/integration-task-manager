# Разработка

Открыть корень существующего checkout. Абсолютный путь личного компьютера не является
частью конфигурации проекта. На сервере использовать пути из SERVER_GUIDE.md.

Backend: Python 3.12, зависимости requirements*.lock. Frontend: Node.js/Vite, package-lock.json.
Из активированной среды в корне:

```sh
python -m unittest discover -s tests -v
node --test tests/frontend.test.mjs
npm run build
```

Локальное демо: npm run dev:demo. Windows-обёртка: scripts/dev.ps1.
Демо содержит синтетические данные и не должно заменять рабочую БД при ошибке подключения.
Коллектор и его полный набор тестов запускать на Linux; блокировки используют fcntl.
Рабочие credentials, Telegram-сессии, exports и дампы не добавлять в Git.
