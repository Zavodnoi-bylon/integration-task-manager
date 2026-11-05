"""Verify real password-based reader/writer access to trial PostgreSQL from SSH user.

The only writer probe is rolled back. No task or evidence is changed.
"""
from pathlib import Path
import secrets

import psycopg


def main():
    root = Path(__file__).resolve().parents[1]
    config = dict(line.split('=', 1) for line in (root / '.env.client').read_text().splitlines()
                  if line and not line.startswith('#'))
    common = dict(host='127.0.0.1', port=55432, dbname='activity_trial', connect_timeout=5)
    with psycopg.connect(**common, user='activity_reader',
                         password=config['ACTIVITY_READER_PASSWORD'], autocommit=True) as reader:
        assert reader.execute('SELECT current_user').fetchone()[0] == 'activity_reader'
        reader.execute('SELECT count(*) FROM work.tasks').fetchone()
        # Prove SQL privileges independently of default read-only transactions.
        reader.execute('SET default_transaction_read_only = off')
        try:
            reader.execute('UPDATE work.tasks SET title=title WHERE false')
        except psycopg.errors.InsufficientPrivilege:
            pass
        else:
            raise AssertionError('Reader unexpectedly has UPDATE permission')
    with psycopg.connect(**common, user='activity_writer',
                         password=config['ACTIVITY_WRITER_PASSWORD']) as writer:
        probe = secrets.token_hex(32)
        run_id = writer.execute(
            "INSERT INTO ops.import_runs(kind,content_hash,source_name,parser_version,status) "
            "VALUES ('trial_access_probe',%s,'synthetic-access-check','1','running') RETURNING id",
            (probe,),
        ).fetchone()[0]
        writer.execute('UPDATE ops.import_runs SET status=%s WHERE id=%s', ('failed', run_id))
        writer.rollback()
        assert writer.execute('SELECT count(*) FROM ops.import_runs WHERE content_hash=%s',
                              (probe,)).fetchone()[0] == 0
    print('PASS: reader SELECT, reader UPDATE denied, writer INSERT/UPDATE rolled back; no sudo/socket.')


if __name__ == '__main__':
    main()
