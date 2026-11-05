#!/usr/bin/env python3
"""Create NEW local credentials without displaying them. Never overwrite a file."""
import os
import secrets
from pathlib import Path


def main():
    target = Path(__file__).resolve().parents[1] / '.env'
    payload = '\n'.join(f'{name}={secrets.token_urlsafe(36)}' for name in (
        'ACTIVITY_ADMIN_PASSWORD', 'ACTIVITY_WRITER_PASSWORD', 'ACTIVITY_READER_PASSWORD')) + '\n'
    fd = os.open(target, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
    with os.fdopen(fd, 'w', encoding='utf-8') as stream:
        stream.write(payload)
    print('Created a new .env with mode 0600. Values were not displayed.')


if __name__ == '__main__':
    main()

