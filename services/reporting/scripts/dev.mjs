#!/usr/bin/env node
// One managed preview entry point. Demo mode must be chosen explicitly.
import {spawn, spawnSync} from 'node:child_process';
import {existsSync, mkdirSync} from 'node:fs';
import {fileURLToPath} from 'node:url';
import path from 'node:path';

const root = path.resolve(path.dirname(fileURLToPath(import.meta.url)), '..');
if (!process.argv.includes('--demo')) {
  console.error('For local demonstration run npm run dev:demo. Production uses Docker and PostgreSQL.');
  process.exit(2);
}
const venvPython = path.join(root, '.venv', process.platform === 'win32' ? 'Scripts/python.exe' : 'bin/python');
const projectPackages = path.join(root, '.venv', 'lib', 'python3.12', 'site-packages');
const cleanPythonEnv = {...process.env};
delete cleanPythonEnv.PYTHONHOME;
delete cleanPythonEnv.PYTHONPATH;
const projectPythonEnv = existsSync(projectPackages)
  ? {...cleanPythonEnv, PYTHONPATH: projectPackages}
  : cleanPythonEnv;
// A copied checkout can preserve a venv symlink whose original runtime is absent.
// Reuse only its installed project packages with a compatible native interpreter.
// No package installation or network access happens in this supervisor.
const preflight = 'import sys; sys.exit(95) if sys.version_info[:2] != (3, 12) else None; import fastapi, uvicorn, psycopg, pydantic';
function canRunPython(command, env) {
  const result = spawnSync(command, ['-c', preflight], {
    cwd: root, env, stdio: 'ignore', timeout: 10000,
  });
  return !result.error && result.status === 0;
}
const override = process.env.ACTIVITY_PYTHON?.trim();
const candidates = override
  ? [{command: override, env: projectPythonEnv, label: 'ACTIVITY_PYTHON'}]
  : [
      {command: venvPython, env: cleanPythonEnv, label: 'project venv'},
      ...(existsSync(projectPackages) ? [
        {command: '/usr/bin/python3', env: projectPythonEnv, label: 'native Python with project packages'},
        {command: 'python3', env: projectPythonEnv, label: 'native Python with project packages'},
      ] : []),
    ];
const runtime = candidates.find(candidate => canRunPython(candidate.command, candidate.env));
if (!runtime) {
  console.error(override
    ? 'ACTIVITY_PYTHON failed the Python 3.12 and dependency import preflight.'
    : existsSync(projectPackages)
      ? 'Project packages exist, but no compatible Python 3.12 runtime passed the dependency import preflight.'
      : 'Project .venv Python 3.12 packages are unavailable in this checkout. Create .venv and install requirements-dev.lock; see README.md.');
  process.exit(2);
}
const children = [];
let stopping = false;
function stop(code = 0) {
  if (stopping) return;
  stopping = true;
  for (const child of children) child.kill('SIGTERM');
  setTimeout(() => { for (const child of children) child.kill('SIGKILL'); process.exit(code); }, 1200).unref();
  process.exitCode = code;
}
function start(command, args, env) {
  const child = spawn(command, args, {cwd: root, env, stdio: 'inherit'});
  children.push(child);
  child.on('error', () => { console.error('Development service failed to start.'); stop(1); });
  child.on('exit', (code) => { if (!stopping) stop(code || 0); });
  return child;
}
console.log('DEMO: fictional tasks only — not a department report.');
console.log(`Python 3.12 dependency preflight passed (${runtime.label}).`);
const demoUiState = path.join(root, 'state', 'demo-ui');
mkdirSync(demoUiState, {recursive:true});
const api = start(runtime.command, ['-m', 'uvicorn', 'app:app', '--host', '127.0.0.1', '--port', '8000', '--no-access-log'],
  {...runtime.env, ACTIVITY_DEMO: '1', ACTIVITY_PROXY_TOKEN:'', ACTIVITY_UI_STATE_DIR:demoUiState});
let ready = false;
for (let attempt = 0; attempt < 80; attempt++) {
  if (stopping || api.exitCode !== null) break;
  try {
    const response = await fetch('http://127.0.0.1:8000/api/health');
    const result = await response.json();
    if (response.ok && result.mode === 'demo') { ready = true; break; }
  } catch {}
  await new Promise(resolve => setTimeout(resolve, 200));
}
if (!ready) {
  console.error('Demo API did not become ready. No fallback data were substituted.');
  stop(1);
} else {
  start(process.execPath, [path.join(root, 'node_modules/vite/bin/vite.js'), '--host', '127.0.0.1', '--port', '4173', '--strictPort'], process.env);
}
process.on('SIGINT', () => stop(0));
process.on('SIGTERM', () => stop(0));

