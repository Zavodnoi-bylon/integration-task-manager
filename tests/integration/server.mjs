import fs from 'node:fs/promises';
import { PGlite } from '@electric-sql/pglite';
import { PGLiteSocketServer } from '@electric-sql/pglite-socket';
const db = await PGlite.create();
await db.exec('CREATE ROLE activity_reader NOLOGIN; CREATE ROLE activity_writer NOLOGIN;');
const sql = (await fs.readFile(new URL('../../init/10-schema.sql', import.meta.url), 'utf8'))
  .split('\n').filter(line => !line.startsWith('\\set ')).join('\n');
await db.exec(sql);
const server = new PGLiteSocketServer({ db, host: '127.0.0.1', port: 55432 });
await server.start();
console.log(JSON.stringify({status:'pglite_ready',version:(await db.query('SELECT version()')).rows[0].version}));
for (const signal of ['SIGINT','SIGTERM']) process.on(signal, async () => { await server.stop(); await db.close(); process.exit(0); });
