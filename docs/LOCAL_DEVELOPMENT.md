# PyCharm и локальный Codex

На текущем компьютере диска D: нет. `D:\Codex` — путь старого ПК, который ещё сохранён у проекта `Codex` в приложении. Не использовать его в командах и не создавать диск-подмену. Постоянные инструкции исправлены в `C:\Users\Судак\.codex\AGENTS.md`, но это само по себе не меняет сохранённую привязку папки в приложении.

Открыть папку `C:\Users\Судак\Documents\Projects\team-activity-explorer` как проект в PyCharm. Interpreter — `.venv\Scripts\python.exe` (Python 3.12). Backend, SQL, frontend и collector находятся в одном Git-репозитории.

Для этого checkout окружение Python 3.12 и frontend dependencies уже установлены.
Запуск демо: `scripts/dev.ps1`; повторная сборка: `scripts/dev.ps1 -BuildOnly`.
Скрипт использует установленный Node или доступный bundled Node приложения; глобальный npm не требуется для уже подготовленного checkout.
Окружение ссылается на bundled Python Codex. После удаления/обновления этого runtime
при необходимости пересоздать `.venv` из установленного Python 3.12.

## Контекст Codex

Открывать ту же папку как проект Codex. Выбирать `Work in → This computer` (Local); для проверки запустить `scripts/preflight.ps1` из локального терминала Codex. Проверка должна показать реальный Windows-путь, Git, SSH и успешный `id -un` на выбранном хосте.

Открытое desktop-приложение само по себе не доказывает локальное выполнение. Документация различает Local/Worktree/Cloud; local файлы и процессы автоматически в Cloud не передаются:
https://learn.chatgpt.com/docs/environments/modes

Кроме того, Work Cloud в некоторых конфигурациях поддерживает подключённый локальный компьютер. Поэтому один признак `kind=chatgpt` не доказывает отсутствия локального исполнителя:
https://learn.chatgpt.com/docs/enterprise/cloud-local-access

Для старого чата инструмент показал `kind=chatgpt, cwd=null`, а его ответ сообщил об отсутствии Windows/SSH. Каким именно нажатием был выбран этот режим, данных нет. Нельзя достоверно утверждать, что приложение само перевело чат в облако или что подключение локального компьютера в принципе невозможно.

Перед первым рабочим поручением проверять среду, а не ждать сбоя deploy. Историю чатов не нужно переносить целиком: агент читает AGENTS.md и WORKFLOW/STATUS. Не создавать автоматически новые чаты для исправления доступа.

## Локальные команды

```powershell
.\.venv\Scripts\python.exe -m unittest discover -s tests -v
node --test tests/frontend.test.mjs
npm run build
npm run dev:demo
```

Демо содержит вымышленные данные. Production UI на VPS работает с PostgreSQL. Collector использует Linux `fcntl` и `flock`; редактировать его можно в PyCharm, выполнять — Linux/WSL/проверочная серверная среда.

## SSH-проверка

```powershell
.\scripts\preflight.ps1 -SshAlias vaultwarden
```

Это проверяет **старый** сервер. Новый alias `team-activity` появится только после настройки новой машины.
Туннель UI для выбранного хоста: `scripts/dashboard-tunnel.ps1 -SshAlias team-activity`.
Для обычной работы Codex открывает свои SSH-соединения; оставлять отдельный пользовательский терминал SSH открытым для него не требуется.
