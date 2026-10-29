# Доступ

Сервер: 172.105.249.196. Владелец входит по SSH/SFTP как root с личным ключом.
teamactivity — рабочая учётная запись проекта без sudo и Docker group.
SSH по ключу проверен; парольный вход выключен. Полный файловый доступ требует root.
Пароли и ключи не помещать в документацию. Администрирование сервера и роль в UI — разные права.

## Дашборд

URL: https://team-bi.172-105-249-196.sslip.io/

| Пользователь | Роль | Доступ |
|---|---|---|
| activity_viewer | admin | Все панели, общая раскладка, смена статуса задачи |
| viver | viewer | Все панели, чтение |
| tech_lead | tech_lead | Пока нет панелей; список определяет владелец |

Серверные credentials и настройки Caddy находятся в /etc/team-activity-access/ (root).
Прокси проверяет Basic Auth, удаляет Authorization перед backend и перезаписывает
X-Activity-User/X-Activity-Proxy-Token. Backend проверяет секретный токен и роли.
Открытие скрытого элемента браузером не расширяет права API.

Конфигурация ролей ACTIVITY_AUTH_ROLES, ACTIVITY_TECH_LEAD_PANELS и proxy token —
в защищённой .env проекта. Возможные панели: metrics, flow, backlog, categories, registry, calendar.
Writer-пароль доступен backend для разрешённой смены статуса, но не передаётся браузеру.
Ручные изменения задач: [TASK_STATUS](TASK_STATUS.md). Раскладка: [LAYOUT_EDITOR](LAYOUT_EDITOR.md).
