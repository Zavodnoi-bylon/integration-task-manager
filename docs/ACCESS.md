# Доступы дашборда

Реализованы на новом trial VPS 23 октября 2026. Старый production не менялся.

| Роль | Панели | Общая раскладка |
|---|---|---|
| admin — текущий activity_viewer владельца | Все | Просмотр и изменение |
| viewer — логин viver | Все | Только просмотр |
| tech_lead — логин tech_lead | Только назначенные; сейчас список пуст | Только просмотр |

У владельца сохранены прежние реквизиты. Новые случайные пароли находятся только
в root-only файлах /etc/team-activity-access/{viver,tech_lead}-credentials.txt
и локальных gitignored state/access/viver-access.txt, state/access/tech-lead-access.txt.
Не добавлять эти файлы в Git и не печатать пароли в журналах.
Все используют прежний HTTPS URL https://team-bi.172-105-249-196.sslip.io/.

Пустой tech_lead видит сообщение «Панели ещё не назначены», без отчётов и фильтров.
Пользователь назовёт доступные ему панели позже. До этого ничего не разрешать.
Известные ID: metrics, flow, backlog, categories, registry, calendar.
registry включает реестр, карточки/evidence, pending proposals и CSV.
metrics — весь существующий блок основных показателей. Более мелкие ограничения
внутри блока потребуют отдельного согласования и реализации.

## Проверка прав

Caddy проверяет Basic Auth на всём сайте. Прокси перезаписывает X-Activity-User
и X-Activity-Proxy-Token своими значениями, удаляет Authorization перед backend.
Backend принимает личность только с секретным токеном прокси (constant-time сравнение).
Прямой запрос к loopback API без токена отклоняется; health остаётся для Docker.
Секреты не попадают в JS, UI или ответы. Роль из браузера не принимается.

ACTIVITY_AUTH_ROLES — JSON mapping username → admin/viewer/tech_lead.
ACTIVITY_TECH_LEAD_PANELS — JSON allowlist; [] по умолчанию, неизвестные ID запрещены.
Они находятся в root-only .env проекта и передаются web в compose.trial.yaml.
ACTIVITY_PROXY_TOKEN должен совпадать в backend и root-only access.env прокси.
При отсутствующей auth-конфигурации обычный backend закрывает API, без demo fallback.
Демонстрационный режим доступен только при явном ACTIVITY_DEMO=1; запускатель
scripts/dev.mjs использует его на 127.0.0.1 с отдельными локальными UI-настройками.

Проверка доступа идёт до чтения snapshot. Закрыты также прямые запросы к API и CSV.
При частичном доступе report содержит только данные разрешённых панелей,
meta исключает клиентов/имена/pending, подробные фильтры требуют registry.
Имена/опорные даты графика загружаются через защищённый /api/schedule;
в статическом JS команды больше нет. GET /api/access сообщает текущие права.
Отображение скрытых DOM-блоков вручную не даёт скрытых данных.

## Настройки и эксплуатация

Общая раскладка хранится отдельно от PostgreSQL: см. [LAYOUT_EDITOR](LAYOUT_EDITOR.md).
После изменения настроек ролей нужно обновить web с обоими явными Compose-файлами.
Добавление отдельных пользователей требует также login/hash в Basic Auth Caddy;
публичной регистрации/редактора пользователей сейчас нет.

Перед обновлением сохранены code-before-shared-roles-20261023.tar.gz,
образ team-activity-explorer:before-shared-roles-20261023 и root-only конфигурации
/etc/team-activity-access/before-roles-20261023/. Для отката согласованно вернуть
код/образ и старые конфигурации API/прокси; не включать новые viewer-логины на старом
backend без серверной проверки прав. Каталог ui-state сохранять для восстановления.
