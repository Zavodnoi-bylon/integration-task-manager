from datetime import datetime, timezone
from pathlib import Path
import sqlite3
import os
import ingest

os.umask(0o077)
with ingest.locked():
    folder = ingest.HOME / 'backups'
    folder.mkdir(exist_ok=True, mode=0o700)
    target = folder / ('daily-' + datetime.now(timezone.utc).strftime('%Y%m%d') + '.sqlite3')
    temporary = target.with_suffix('.tmp')
    source = ingest.connect()
    dest = sqlite3.connect(temporary)
    try:
        source.backup(dest)
    finally:
        dest.close()
        source.close()
    temporary.replace(target)
    for old in sorted(folder.glob('daily-*.sqlite3'))[:-14]:
        old.unlink()
