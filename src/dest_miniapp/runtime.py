from __future__ import annotations

import argparse
import asyncio
import os
import signal
import sys
import time
from pathlib import Path
from urllib.request import urlopen

import uvicorn

from .composition import build_postgres_runtime
from .notifications import TelegramNotificationDelivery
from .outbox import PostgresOutboxWorker
from .postgres import PostgresDatabase
from .settings import Settings


def _required(name: str) -> str:
    value = os.getenv(name, "").strip()
    if not value:
        raise RuntimeError(f"{name} is required")
    return value


def _ids(name: str) -> frozenset[int]:
    return frozenset(
        int(item.strip())
        for item in os.getenv(name, "").split(",")
        if item.strip()
    )


def _canonical_services():
    root = Path(os.getenv("CANONICAL_APP_PATH", "/opt/integration-task-manager"))
    if not (root / "app").is_dir():
        raise RuntimeError(f"canonical Integration Task Manager is absent: {root}")
    sys.path.insert(0, str(root))
    from app.services.advertiser_catalog import AdvertiserCatalog
    from app.services.destination_service import DestinationService
    from app.services.postback_service import PostbackService
    from app.settings import Settings as CanonicalSettings

    profile = _required("BOT_KEY")
    admins = _ids("ADMIN_USERS")
    technicians = _ids("TECHNICIAN_USERS")
    admin_message = int(_required("ADMIN_MESSAGE"))
    if admin_message not in admins | technicians:
        raise RuntimeError("ADMIN_MESSAGE must be configured as active staff")
    canonical = CanonicalSettings(
        bot_token=_required("BOT_TOKEN"),
        admin_users=admins,
        admin_message=admin_message,
        bot_key=profile,
        technician_users=technicians,
        openrouter_api_key=os.getenv("OPENROUTER_API_KEY", ""),
        destination_model=os.getenv("DESTINATION_MODEL", "~openai/gpt-sol-latest"),
        destination_prompt_path=os.getenv(
            "DESTINATION_PROMPT_PATH", "content/destination/system_prompt.md"
        ),
        destination_catalog_path=os.getenv(
            "DESTINATION_CATALOG_PATH", "content/destination/advertisers.json"
        ),
    )
    return (
        AdvertiserCatalog(
            bot_key=profile, catalog_path=canonical.destination_catalog_path
        ),
        DestinationService(
            api_key=canonical.openrouter_api_key,
            model=canonical.destination_model,
            prompt_path=canonical.destination_prompt_path,
        ),
        PostbackService(canonical),
        admins,
        technicians,
    )


def create_application():
    profile = _required("BOT_KEY")
    profile_name = os.getenv("PROFILE_NAME", profile.capitalize()).strip()
    catalog, destination, postbacks, admins, technicians = _canonical_services()
    runtime = build_postgres_runtime(
        settings=Settings(
            bot_token=_required("BOT_TOKEN"),
            profile_key=profile,
            profile_name=profile_name,
            allowed_origin=_required("ALLOWED_ORIGIN"),
            session_cookie_name=os.getenv(
                "SESSION_COOKIE_NAME", f"{profile}_miniapp_session"
            ),
            init_data_max_age_seconds=int(
                os.getenv("INIT_DATA_MAX_AGE_SECONDS", "300")
            ),
            session_ttl_seconds=int(os.getenv("SESSION_TTL_SECONDS", "43200")),
        ),
        database_url=_required("DATABASE_URL"),
        advertiser_catalog=catalog,
        destination_service=destination,
        postback_service=postbacks,
        admin_ids=admins,
        technician_ids=technicians,
    )
    @runtime.app.get("/healthz", include_in_schema=False)
    async def healthz():
        async with runtime.database.pool.connection() as connection:
            row = await (await connection.execute("SELECT 1 AS ok")).fetchone()
        return {"ok": row["ok"] == 1, "profile": profile}

    runtime.app.state.miniapp_runtime = runtime
    return runtime.app


async def _worker() -> None:
    database = PostgresDatabase(_required("DATABASE_URL"))
    delivery = TelegramNotificationDelivery(_required("BOT_TOKEN"))
    worker = PostgresOutboxWorker(
        database, delivery, profile_key=_required("BOT_KEY")
    )
    heartbeat = Path(os.getenv("WORKER_HEARTBEAT", "/tmp/miniapp-worker-heartbeat"))
    stop = asyncio.Event()
    loop = asyncio.get_running_loop()
    for name in ("SIGINT", "SIGTERM"):
        if hasattr(signal, name):
            try:
                loop.add_signal_handler(getattr(signal, name), stop.set)
            except NotImplementedError:
                pass
    await database.open()
    try:
        while not stop.is_set():
            count = await worker.run_once()
            heartbeat.write_text(str(time.time()), encoding="ascii")
            try:
                await asyncio.wait_for(stop.wait(), timeout=0.25 if count else 2.0)
            except TimeoutError:
                pass
    finally:
        await database.close()


def _health_worker() -> None:
    heartbeat = Path(os.getenv("WORKER_HEARTBEAT", "/tmp/miniapp-worker-heartbeat"))
    if not heartbeat.is_file() or time.time() - heartbeat.stat().st_mtime > 15:
        raise SystemExit(1)


def _health_web() -> None:
    with urlopen("http://127.0.0.1:8000/healthz", timeout=3) as response:
        if response.status != 200:
            raise SystemExit(1)


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("mode", choices=("web", "worker", "health-web", "health-worker"))
    args = parser.parse_args()
    if args.mode == "web":
        uvicorn.run(
            "dest_miniapp.runtime:create_application",
            factory=True,
            host="0.0.0.0",
            port=8000,
            proxy_headers=True,
            forwarded_allow_ips=os.getenv("FORWARDED_ALLOW_IPS", "127.0.0.1"),
        )
    elif args.mode == "worker":
        asyncio.run(_worker())
    elif args.mode == "health-web":
        _health_web()
    else:
        _health_worker()


if __name__ == "__main__":
    main()
