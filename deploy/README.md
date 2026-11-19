# Deployment

`compose.yaml` — основной production-способ запуска. `deploy-compose.sh` выполняет
проверку и безопасный переход с двух прежних systemd-сервисов.

Файлы `integration-task-manager-*.service` и `install-systemd.sh` сохранены только
как проверенный rollback. Не запускайте одновременно systemd unit и контейнер с
одним токеном: Telegram отклонит параллельный long polling.

Рабочие env-файлы создаются по примерам в каталоге конфигурации сервера, а не рядом
с исходниками. Скрипт не печатает их содержимое и не удаляет старые release.

Для синхронизации advertiser'ов AIO используется отдельный закрытый файл
`aio-advertisers.env` из одноимённого example. На staging он находится в
`/home/vpnadmin/.config/integration-task-manager-staging/aio-advertisers.env`,
на production — в `/home/teamactivity/.config/integration-task-manager/`.
После успешного cutover он сохраняет несекретный runtime state с точным image tag.
Для последующих операций используйте `sudo bash deploy/compose.sh <command>`.
