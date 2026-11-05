"""Refresh the server guide from actual project files, never reading private data."""
from pathlib import Path
import os

ROOT = Path(__file__).resolve().parents[1]
SKIP = {'.git', '.venv', 'node_modules', '__pycache__', '.pytest_cache', '.idea', '.tmp',
        'state', 'data', 'dist', 'backups', '.agents', '.codex'}
DESCRIPTIONS = {
    'docs/TICKET_BOT_RESEARCH.md': 'Статическое сравнение Telegram ticket-ботов и новая концепция, без развёртывания',
    '.dockerignore': 'Исключения контекста Docker: секреты, данные и лишние файлы',
    '.env.example': 'Шаблон переменных без рабочих секретов',
    '.gitattributes': 'Правила текстовых файлов и окончаний строк Git',
    '.gitignore': 'Запрет попадания частных данных и зависимостей в Git',
    'AGENTS.md': 'Правила работы агента с проектом',
    'PROJECT_HANDOFF.md': 'Короткая точка продолжения и ссылки на текущий контракт',
    'README.md': 'Описание проекта и вход в документацию',
    'Dockerfile': 'Сборка frontend и runtime-образа backend',
    'compose.yaml': 'Базовые сервисы; на этом VPS обязательно дополнение compose.trial.yaml',
    'app.py': 'FastAPI: маршруты, авторизация, отчёт, задачи, статусы и static files',
    'lead_app.py': 'Read-only FastAPI Mini App для сводки и истории тикетов двух профилей',
    'sync.py': 'CLI нормализации, импорта сообщений/proposals и review',
    'index.html': 'HTML-каркас дашборда',
    'package.json': 'Команды и зависимости frontend',
    'package-lock.json': 'Зафиксированные версии frontend-зависимостей',
    'requirements.in': 'Прямые зависимости Python runtime',
    'requirements.lock': 'Зафиксированные зависимости Python runtime',
    'requirements-dev.in': 'Дополнительные зависимости разработки Python',
    'requirements-dev.lock': 'Зафиксированные зависимости разработки Python',
    'vite.config.js': 'Настройка сборки и локального frontend-сервера',
    'activity/__init__.py': 'Пакет backend',
    'activity/access.py': 'Роли, разрешения панелей и хранение общей раскладки',
    'activity/account_input.py': 'Проверка и адаптация разделённых Telegram API inputs',
    'activity/asana_completion.py': 'Проверяемое правило совпадения нейминга и заказанных вариантов',
    'activity/demo.py': 'Загрузка явно включённого синтетического демо',
    'activity/domain.py': 'Жизненный цикл, фильтры и расчёт метрик',
    'activity/ingest.py': 'Нормализация и импорт исходных сообщений/ревизий',
    'activity/proposals.py': 'Валидация предложений, импорт, принятие и журнал решений',
    'activity/repository.py': 'Чтение PostgreSQL в согласованный snapshot',
    'activity/task_status.py': 'Аудируемое ручное завершение/переоткрытие из UI',
    'activity/telegram_webapp_auth.py': 'Проверка подписи, срока и allowlist Telegram Mini App',
    'activity/tenants.py': 'Идентификаторы/ограничения тенантов и аккаунтов',
    'activity/ticket_analytics.py': 'Ограниченные read-only запросы к витринам reporting тикет-менеджеров',
    'lead_static/index.html': 'Каркас read-only Mini App техлида',
    'lead_static/styles.css': 'Адаптивное оформление Mini App аналитики',
    'lead_static/app.js': 'Фильтры, графики, список и карточки тикетов без мутаций',
    'src/base.css': 'Базовое оформление интерфейса',
    'src/style.css': 'Стили Team Activity Explorer и редактора раскладки',
    'src/charts.js': 'Отрисовка графиков и переходы по выбранным периодам',
    'src/layout-model.js': 'Схема раскладки, размеры и размещение блоков',
    'src/layout.js': 'Редактор: drag/resize, черновик и сохранение',
    'src/main.js': 'Загрузка данных, фильтры, реестр, карточки и действия UI',
    'src/schedule.js': 'Календарь смен 2/2 и подсказки',
    'src/utils.js': 'Форматирование и безопасный вывод текста',
    'src/PROVENANCE.md': 'Происхождение переиспользованных frontend-фрагментов',
    'init/00-users.sh': 'Первичная настройка SQL-ролей для пустого volume',
    'init/10-schema.sql': 'Таблицы raw/work/ops, ограничения, индексы и права',
    'init/20-tenants.sql': 'Аддитивная tenant-миграция и ограничения evidence',
    'init/30-ticketing.sql': 'Каноническая PostgreSQL-модель тикетов, событий, диалогов и отчётных витрин',
    'examples/demo_dataset.json': 'Синтетические задачи и события демо',
    'examples/task-proposals.example.json': 'Пример формы предложения с вымышленными evidence',
    'examples/task-proposals.schema.json': 'Действующий JSON-контракт proposals/events/evidence',
    'deploy/Caddyfile.dashboard': 'HTTPS proxy: Telegram-auth путь /lead и Basic Auth основного dashboard',
    'deploy/Caddyfile.example': 'Общий пример proxy, не действующая конфигурация',
    'deploy/compose.trial.yaml': 'Обязательные overrides текущего VPS: БД, порты, статусные записи',
    'deploy/compose.lead.yaml': 'Изолированный read-only контейнер Telegram Mini App на loopback 8482',
    'deploy/compose.dashboard-access.yaml': 'Отдельный Compose HTTPS-прокси',
    'deploy/compose.proxy.example.yaml': 'Пример proxy для другой установки, не рабочий Compose',
    'deploy/team-activity-collect.service': 'Исходник systemd service двух аккаунтов',
    'deploy/team-activity-collect.timer': 'Исходник календаря 00:10 Europe/Moscow',
    'scripts/activate-clean-report.sh': 'Одноразовое переключение при согласованном пересчёте; не запускать при обслуживании',
    'scripts/backup.sh': 'Общий pg_dump helper; defaults не заменяют явные параметры текущего VPS',
    'scripts/check-tenant-migration.sh': 'Проверка tenant-миграции в отдельной восстановленной БД',
    'scripts/apply-ticketing-migration.sh': 'Резервная копия, restore-rehearsal и применение ticketing-схемы',
    'scripts/check-trial-access.py': 'Проверка SQL-доступа reader/writer',
    'scripts/create-env.py': 'Первичная генерация env, не замена рабочих credentials',
    'scripts/dashboard-tunnel.ps1': 'Необязательный туннель для диагностики с Windows',
    'scripts/dev.mjs': 'Локальный запуск демо/backend/frontend',
    'scripts/dev.ps1': 'Windows-обёртка локальной разработки',
    'scripts/preflight.ps1': 'Проверка локального Windows checkout и собственного SSH',
    'scripts/prepare-clean-report.sh': 'Одноразовая подготовка пересчёта в отдельной БД; не ежедневный runner',
    'scripts/restore-migration-trial.sh': 'Восстановление в новую проверочную БД; не перезаписывает рабочую',
    'scripts/start-vps.sh': 'Исторический bootstrap другого развёртывания; НЕ запускать на действующем VPS',
    'scripts/export-migration-trial.sh': 'Исторический согласованный export; не часть текущего расписания',
    'scripts/trial-sync.sh': 'Importer от teamactivity с .env.client и явной рабочей БД',
    'scripts/build-file-map.py': 'Генерация этой карты по фактическим файлам',
    'collector/.gitignore': 'Исключения локальных данных/секретов collector',
    'collector/README.txt': 'Вход в актуальную инструкцию collector',
    'collector/accounts.py': 'Раздельные логины, identity, проверка сессий и targets',
    'collector/accounts.sh': 'Запуск account-команд из интерактивного терминала teamactivity',
    'collector/bundle.py': 'Ограниченный исходный capture, resume и проверка manifest',
    'collector/partition.py': 'Публикация раздельных account inputs и разовых manual exports',
    'collector/daily.py': 'Суточный checkpoint, повтор, catch-up и FloodWait двух аккаунтов',
    'collector/ingest.py': 'Общие функции/блокировки и прежний SQLite collector',
    'collector/requirements.txt': 'Зависимости collector (Telethon)',
    'collector/targets.example.json': 'Пример разрешённых источников, не рабочий config',
    'collector/install.sh': 'Прежний односессионный installer с cron; НЕ использовать для двух аккаунтов',
    'collector/run.sh': 'Прежняя односессионная обёртка; не текущий запуск',
    'collector/setup-telegram.sh': 'Прежняя односессионная настройка; использовать accounts.sh',
    'collector/backup_daily.py': 'Прежний helper резервирования SQLite; не активный timer',
    'tests/frontend.test.mjs': 'Регрессии метрик UI, календаря и модели раскладки',
    'tests/integration/README.md': 'Изолированная PGlite проверка; не запускать на рабочем порту 55432',
    'tests/integration/package.json': 'Зависимости изолированного тестового SQL runtime',
    'tests/integration/package-lock.json': 'Зафиксированные тестовые SQL-зависимости',
    'tests/integration/server.mjs': 'Временный SQL-сервер с синтетическими данными',
    'tests/integration/check_roundtrip.py': 'Проверка импорта/review/прав на синтетической БД',
    'tests/integration/check_tenants.py': 'Проверка tenant/evidence ограничений на отдельной БД',
    'tests/integration/check_ticketing.py': 'Проверка ticketing-схемы, ролей, идемпотентности и отчётных витрин',
    'integrations/asana-review-bdm/README.md': 'Пакет обновления внешнего Asana-бота, не локальный runtime',
    'integrations/asana-review-bdm/clients.py': 'Asana client с чтением секций/полей и обработкой 403/404',
    'integrations/asana-review-bdm/service.py': 'Внешний бот: уведомление о переходе в Review BDM',
    'integrations/asana-review-bdm/apply.sh': 'Проверяемое применение пакета на хосте внешнего бота',
    'integrations/asana-review-bdm/inspect-live.sh': 'Экспорт кода внешнего бота для сверки без секретов',
    'integrations/asana-review-bdm/original-sha256.json': 'Отпечатки проверенной исходной версии внешнего бота',
    'integrations/asana-review-bdm/test_review_bdm.py': 'Регрессии переходов Review BDM и совместимости бот-кода',
}
DOCS = {
 'SERVER_GUIDE':'Основная серверная инструкция и карта файлов',
 'ACCESS':'SSH/SFTP и роли дашборда', 'ARCHITECTURE':'Архитектура, таблицы и инварианты',
 'BACKEND_SEMANTICS':'Точный смысл метрик, дат, фильтров и лимитов',
 'DEPLOYMENT':'Обслуживание текущего Compose', 'EXTRACTION_PROMPT':'Правила ИИ-анализа и доказательства завершения',
 'IMPLEMENTATION_CONTRACT':'Ссылки на единственные действующие контракты',
 'IMPORT_VERIFICATION':'Порядок проверки импорта/review', 'LAYOUT_EDITOR':'Раскладка, хранение и известные дефекты',
 'LOCAL_DEVELOPMENT':'Переносимые команды разработки без личных путей', 'MIGRATION':'Сохранённые архивы и требования будущего переноса',
 'SHIFT_HANDOFF':'Правила передачи смены', 'SHIFT_SCHEDULE':'Подтверждённый календарь 2/2',
 'STATUS':'Проверенное текущее состояние и незавершённые этапы', 'TASK_STATUS':'Смена статуса и аудит БД',
 'TELEGRAM_ACCOUNTS':'Авторизация и разрешённые источники двух аккаунтов',
 'TELEGRAM_BUNDLES':'Пакеты, checkpoints, resume и ограничения охвата',
 'TENANTS':'Разделение аккаунтов/тенантов и объединение отчёта',
 'VERIFICATION':'Команды и границы проверок', 'WORKFLOW':'Сбор → анализ → предложения → БД → UI',
}
for name, purpose in DOCS.items():
    DESCRIPTIONS[f'docs/{name}.md'] = purpose
TESTS = {
 'access':'ролей и раскладки', 'api':'маршрутов и защиты API',
 'api_revisions':'исходных API-редакций и транспортных изменений',
 'asana_completion':'правил завершения Asana', 'domain':'жизненного цикла и метрик',
 'ingest':'импорта, ревизий и источников', 'proposals':'валидации/принятия предложений',
 'repository':'SQL snapshots и лимитов', 'task_status':'завершения/переоткрытия и идемпотентности',
 'tenants':'tenant/account ограничений', 'accounts':'раздельной авторизации и источников',
 'ticket_analytics':'Telegram-подписи, allowlist и фильтров read-only аналитики',
 'bundle':'неизменяемых пакетов, topics и resume', 'partition':'разделения inputs и manual exports',
 'daily':'суточного checkpoint, ошибок, catch-up и повторов без сети',
}
for directory in ('tests','collector'):
    for name,purpose in TESTS.items():
        DESCRIPTIONS[f'{directory}/test_{name}.py'] = 'Тесты '+purpose


def main():
    files=[]
    for directory, dirs, names in os.walk(ROOT):
        dirs[:]=[name for name in dirs if name not in SKIP]
        for name in names:
            p=Path(directory)/name
            rel=p.relative_to(ROOT).as_posix()
            if name.startswith('.env') and name!='.env.example': continue
            if p.suffix in {'.pyc','.log','.dump'}: continue
            if rel not in DESCRIPTIONS:
                raise SystemExit('Add an explicit purpose for: '+rel)
            files.append(rel)
    lines=['## Карта исходников действующего проекта','',
           'Все пути в этой таблице относительно `/home/teamactivity/team-activity-explorer`.','',
           '| Файл | Назначение |','|---|---|']
    for rel in sorted(files): lines.append(f'| `{rel}` | {DESCRIPTIONS[rel]} |')
    runtime=Path('/home/teamactivity/team-activity-ingest/current')
    if runtime.is_dir():
        lines += ['', '## Исполняемые файлы collector', '',
                  'Относительно `/home/teamactivity/team-activity-ingest/current`. Это отдельная рабочая копия.', '',
                  '| Файл | Назначение |','|---|---|']
        for p in sorted(runtime.iterdir()):
            if not p.is_file(): continue
            purpose=DESCRIPTIONS.get('collector/'+p.name)
            if not purpose:raise SystemExit('Unknown runtime file: '+p.name)
            lines.append(f'| `{p.name}` | {purpose} |')
    lines+=['',f'Всего файлов исходников проекта: **{len(files)}**. Секреты и генерируемые данные описаны отдельной таблицей выше.','']
    guide=ROOT/'docs/SERVER_GUIDE.md'
    text=guide.read_text(encoding='utf-8')
    start='<!-- FILE_MAP_START -->'; end='<!-- FILE_MAP_END -->'
    prefix,rest=text.split(start,1); _,suffix=rest.split(end,1)
    guide.write_text(prefix+start+'\n'+'\n'.join(lines)+end+suffix,encoding='utf-8')
    print('Mapped project source files:',len(files))


if __name__=='__main__':main()
