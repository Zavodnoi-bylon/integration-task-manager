"""Separate server-side Telegram logins; never collects messages or schedules work."""
from __future__ import annotations

import argparse
import asyncio
from contextlib import contextmanager, nullcontext
from datetime import datetime, timezone
import getpass
import json
import os
from pathlib import Path
import sys

from ingest import atomic_json

BASE = Path(os.environ.get('ACTIVITY_COLLECTOR_HOME', Path.home() / 'team-activity-ingest'))
SLOTS = ('account-1', 'account-2')


class SetupError(Exception):
    pass


def account_home(slot, base):
    if slot not in SLOTS:
        raise SetupError('Неизвестный аккаунт.')
    return base / 'accounts' / slot


@contextmanager
def locked_file(path):
    path.parent.mkdir(parents=True, exist_ok=True, mode=0o700)
    with path.open('a+') as handle:
        if os.name == 'posix':
            import fcntl
            fcntl.flock(handle, fcntl.LOCK_EX | fcntl.LOCK_NB)
        else:
            import msvcrt
            handle.seek(0)
            handle.write('0')
            handle.flush()
            handle.seek(0)
            msvcrt.locking(handle.fileno(), msvcrt.LK_NBLCK, 1)
        yield


def credentials(base):
    value = json.loads((base / 'telegram-app.json').read_text(encoding='utf-8'))
    api_id, api_hash = int(value['api_id']), value['api_hash']
    if api_id <= 0 or not isinstance(api_hash, str) or len(api_hash) != 32 or any(c not in '0123456789abcdefABCDEF' for c in api_hash):
        raise SetupError('Некорректные реквизиты Telegram-приложения.')
    return api_id, api_hash


def identity(home):
    path = home / 'identity.json'
    return json.loads(path.read_text(encoding='utf-8')) if path.exists() else None


def validate_identity(slot, me, base):
    if me is None or me.bot:
        raise SetupError('Нужен пользовательский Telegram-аккаунт, не бот.')
    uid = str(me.id)
    current = identity(account_home(slot, base))
    if current and current['telegram_user_id'] != uid:
        raise SetupError('Этому слоту уже назначен другой аккаунт. Настройки не изменены.')
    for other in SLOTS:
        if other == slot:
            continue
        registered = identity(account_home(other, base))
        if registered and registered['telegram_user_id'] == uid:
            raise SetupError(f'Этот аккаунт уже назначен {other}. Во втором слоте нужен другой аккаунт.')
    return uid


def client_for(slot, base):
    from telethon import TelegramClient
    api_id, api_hash = credentials(base)
    session = account_home(slot, base) / 'data' / 'session' / 'user'
    session.parent.mkdir(parents=True, exist_ok=True, mode=0o700)
    return TelegramClient(str(session), api_id, api_hash,
                          device_model=f'Team Activity Collector {slot}',
                          app_version='1.0', receive_updates=False,
                          connection_retries=2, request_retries=2, flood_sleep_threshold=0)


async def authorize_client(client):
    """Only login RPCs/get_me; no dialogs or message history requests."""
    from telethon.errors import SessionPasswordNeededError
    phone = getpass.getpass('Номер телефона с кодом страны (скрыт): ').strip()
    if not phone.startswith('+') or not phone[1:].isdigit() or not 5 <= len(phone[1:]) <= 15:
        raise SetupError('Введите номер в международном формате, например +7…')
    sent = await client.send_code_request(phone)
    code = getpass.getpass('Код входа из Telegram (скрыт): ').strip().replace(' ', '')
    try:
        await client.sign_in(phone=phone, code=code, phone_code_hash=sent.phone_code_hash)
    except SessionPasswordNeededError:
        password = getpass.getpass('Пароль двухэтапной проверки Telegram (скрыт): ')
        await client.sign_in(password=password)


async def login_one(slot, base):
    home = account_home(slot, base)
    # Same lock path as ingest.py using ACTIVITY_HOME=<this account directory>.
    with locked_file(home / 'data' / 'run.lock'):
        client = client_for(slot, base)
        try:
            await client.connect()
            if not await client.is_user_authorized():
                await authorize_client(client)
            me = await client.get_me()
            uid = validate_identity(slot, me, base)
            config_path = home / 'config.json'
            config = json.loads(config_path.read_text(encoding='utf-8')) if config_path.exists() else {}
            api_id, api_hash = credentials(base)
            config.update(api_id=api_id, api_hash=api_hash)
            config.setdefault('chats', [])
            config.setdefault('edit_overlap_ids', 1000)
            atomic_json(config_path, config)
            atomic_json(home / 'identity.json', {
                'account_id': slot, 'telegram_user_id': uid,
                'verified_at': datetime.now(timezone.utc).isoformat(),
            })
            print(f'{slot}: вход подтверждён. Сбор сообщений не запускался.')
        finally:
            await client.disconnect()


async def check_one(slot, base):
    home = account_home(slot, base)
    if not identity(home):
        raise SetupError(f'{slot}: сначала выполните вход.')
    with locked_file(home / 'data' / 'run.lock'):
        client = client_for(slot, base)
        try:
            await client.connect()
            if not await client.is_user_authorized():
                raise SetupError(f'{slot}: сессия недействительна, нужен повторный вход.')
            validate_identity(slot, await client.get_me(), base)
            print(f'{slot}: сервер подтвердил действующую сессию.')
        finally:
            await client.disconnect()


def local_status(base):
    result = []
    for slot in SLOTS:
        home = account_home(slot, base)
        config_path = home / 'config.json'
        config = json.loads(config_path.read_text(encoding='utf-8')) if config_path.exists() else {}
        result.append({'account_id': slot, 'login_recorded': identity(home) is not None,
                       'session_present': (home / 'data/session/user.session').exists(),
                       'target_count': len(config.get('chats', []))})
    return {'accounts': result, 'note': 'Локальные записи; действительность сессий проверяет команда check.'}


async def configure_targets(slot, titles, base):
    normalize = lambda value: ' '.join(value.casefold().split())
    wanted = {normalize(title): title for title in titles}
    if not wanted or '' in wanted or len(wanted) != len(titles):
        raise SetupError('Нужны непустые разные названия чатов.')
    home = account_home(slot, base)
    if not identity(home):
        raise SetupError(f'{slot}: сначала выполните вход.')
    with locked_file(home / 'data' / 'run.lock'):
        client = client_for(slot, base)
        try:
            await client.connect()
            if not await client.is_user_authorized():
                raise SetupError(f'{slot}: сессия недействительна, нужен повторный вход.')
            validate_identity(slot, await client.get_me(), base)
            matches = {key: {} for key in wanted}
            # Resolve only explicitly requested names; no history iteration/export.
            async for dialog in client.iter_dialogs():
                key = normalize(dialog.name or '')
                if key in matches:
                    matches[key][dialog.id] = {'id': dialog.id, 'title': dialog.name,
                                               'forum': bool(getattr(dialog.entity, 'forum', False))}
            for key, found in matches.items():
                if len(found) != 1:
                    raise SetupError(f'Чат «{wanted[key]}»: совпадений {len(found)}. Настройки не изменены; нужен точный ID.')
            resolved = [next(iter(found.values())) for found in matches.values()]
            config_path = home / 'config.json'
            config = json.loads(config_path.read_text(encoding='utf-8'))
            config['chats'] = [item['id'] for item in resolved]
            atomic_json(config_path, config)
            atomic_json(home / 'targets-resolved.json', {'account_id': slot, 'targets': resolved})
            print(json.dumps({'account_id': slot, 'targets': resolved, 'collection_started': False}, ensure_ascii=False))
            return resolved
        finally:
            await client.disconnect()


async def run(command, slots, base, titles=None):
    if command == 'status':
        print(json.dumps(local_status(base), ensure_ascii=False))
        return
    # Prevent two concurrent login processes from registering the same identity.
    with locked_file(base / 'accounts' / 'authorization.lock') if command == 'login' else nullcontext():
        for index, slot in enumerate(slots, 1):
            print(f'\n=== {slot} ({index}/{len(slots)}) ===', flush=True)
            if command == 'login':
                await login_one(slot, base)
            elif command == 'targets':
                await configure_targets(slot, titles, base)
            else:
                await check_one(slot, base)


def main(argv=None):
    os.umask(0o077)
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('command', choices=('login', 'check', 'status', 'targets'))
    parser.add_argument('account', nargs='?', choices=(*SLOTS, 'both'), default='both')
    parser.add_argument('titles', nargs='*')
    args = parser.parse_args(argv)
    try:
        if args.command == 'targets' and (args.account == 'both' or not args.titles):
            raise SetupError('Укажите один аккаунт и названия разрешённых чатов.')
        if args.command != 'targets' and args.titles:
            raise SetupError('Названия чатов допустимы только для команды targets.')
        if args.command == 'login' and not sys.stdin.isatty():
            raise SetupError('Вход требует интерактивного терминала. Коды и пароли вводите только там.')
        asyncio.run(run(args.command, SLOTS if args.account == 'both' else (args.account,), BASE, args.titles))
        return 0
    except SetupError as exc:
        print(str(exc))
    except KeyboardInterrupt:
        print('\nОстановлено. Уже подтверждённые аккаунты сохранены.')
    except Exception as exc:
        # No Telegram exception messages: they can contain phone numbers or secrets.
        name = type(exc).__name__
        if name == 'FloodWaitError':
            print('Telegram ограничил частоту входов. Не повторяйте вход сразу; сообщите о FloodWaitError.')
        elif name == 'PhoneCodeInvalidError':
            print('Неверный код Telegram. Повторите вход только после проверки кода.')
        elif name == 'PasswordHashInvalidError':
            print('Неверный пароль двухэтапной проверки.')
        elif isinstance(exc, BlockingIOError):
            print('Сессия занята другим процессом. Параллельный вход или сбор не запущен.')
        else:
            print(f'Ошибка ({name}). Секретные значения не выводятся.')
    return 1


if __name__ == '__main__':
    raise SystemExit(main())
