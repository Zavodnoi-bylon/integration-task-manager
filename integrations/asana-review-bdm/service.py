from __future__ import annotations

import asyncio
import html
import logging
from datetime import UTC, date, datetime, timedelta
from typing import Any

import httpx

from .clients import AsanaAuthorizationError, AsanaClient, TelegramClient
from .config import Config
from .db import Database, iso_now


LOGGER = logging.getLogger(__name__)


def assignee_gid(task: dict[str, Any]) -> str | None:
    assignee = task.get("assignee") or {}
    return str(assignee.get("gid")) if assignee.get("gid") else None


REVIEW_BDM = "review bdm"


def in_review_bdm(task: dict[str, Any], project_gids: tuple[str, ...]) -> bool:
    """Use structured section/enum values, never comments or task-title text."""
    for membership in task.get("memberships") or []:
        if str((membership.get("project") or {}).get("gid")) not in project_gids:
            continue
        if ((membership.get("section") or {}).get("name") or "").strip().casefold() == REVIEW_BDM:
            return True
    for field in task.get("custom_fields") or []:
        values = [field.get("enum_value") or {}, *(field.get("multi_enum_values") or [])]
        if any((value.get("name") or "").strip().casefold() == REVIEW_BDM for value in values):
            return True
    return False


def task_link(task: dict[str, Any]) -> str:
    name = html.escape(task.get("name") or "Без названия")
    url = html.escape(task.get("permalink_url") or "", quote=True)
    return f'<a href="{url}">{name}</a>' if url else f"<b>{name}</b>"


def due_label(task: dict[str, Any]) -> str:
    return str(task.get("due_at") or task.get("due_on") or "не указан")


def description_block(task: dict[str, Any], limit: int = 1600) -> str:
    description = str(task.get("notes") or "").strip()
    if not description:
        return ""
    if len(description) > limit:
        description = description[: limit - 3].rstrip() + "..."
    return f"\n\n<b>Описание:</b>\n{html.escape(description)}"


def parse_datetime(value: str) -> datetime:
    return datetime.fromisoformat(value.replace("Z", "+00:00"))


class AlertService:
    def __init__(
        self,
        config: Config,
        database: Database,
        asana: AsanaClient,
        telegram: TelegramClient,
    ):
        self.config = config
        self.db = database
        self.asana = asana
        self.telegram = telegram
        self.last_asana_success: datetime | None = None
        self.last_telegram_success: datetime | None = None
        self.last_error: str | None = None

    async def initialize(self) -> None:
        bot = await self.telegram.get_me()
        self.last_telegram_success = datetime.now(UTC)
        if bot.get("username", "").lower() != "alertfromasanabot":
            LOGGER.warning(
                "Configured Telegram bot is @%s, expected @AlertfromasanaBot",
                bot.get("username", "unknown"),
            )

        me = await self.asana.me()
        self.last_asana_success = datetime.now(UTC)
        self.db.ensure_invite(
            self.config.bootstrap_link_code,
            str(me["gid"]),
            me.get("name") or me.get("email") or "Asana user",
        )
        LOGGER.info(
            "Connected to Asana as %s (%s); Telegram bot @%s",
            me.get("name"),
            me.get("gid"),
            bot.get("username"),
        )

    async def run(self) -> None:
        await self.initialize()
        tasks = [
            asyncio.create_task(self.telegram_loop(), name="telegram-loop"),
            asyncio.create_task(self.asana_loop(), name="asana-loop"),
            asyncio.create_task(self.reminder_loop(), name="reminder-loop"),
            asyncio.create_task(self.health_server(), name="health-server"),
        ]
        try:
            await asyncio.gather(*tasks)
        finally:
            for task in tasks:
                task.cancel()
            await asyncio.gather(*tasks, return_exceptions=True)

    async def telegram_loop(self) -> None:
        offset = int(self.db.get_meta("telegram_offset", "0") or "0")
        while True:
            try:
                updates = await self.telegram.get_updates(offset)
                self.last_telegram_success = datetime.now(UTC)
                self.last_error = None
                for update in updates:
                    offset = int(update["update_id"]) + 1
                    await self.handle_telegram_update(update)
                    self.db.set_meta("telegram_offset", str(offset))
            except asyncio.CancelledError:
                raise
            except (httpx.HTTPError, RuntimeError, KeyError, ValueError) as exc:
                self.last_error = f"telegram: {type(exc).__name__}"
                LOGGER.exception("Telegram polling failed")
                await asyncio.sleep(5)

    async def handle_telegram_update(self, update: dict[str, Any]) -> None:
        message = update.get("message") or {}
        chat = message.get("chat") or {}
        sender = message.get("from") or {}
        text = str(message.get("text") or "").strip()
        chat_id = chat.get("id")
        if not chat_id or not text:
            return

        if chat.get("type") != "private":
            await self.telegram.send_message(
                int(chat_id),
                "Этот бот отправляет персональные уведомления. Откройте его в личном чате.",
            )
            return

        command, _, argument = text.partition(" ")
        command = command.split("@", 1)[0].lower()
        argument = argument.strip()

        if command == "/start":
            if argument:
                await self.link_user(int(chat_id), sender, argument)
                return
            link = self.db.get_link_by_chat(int(chat_id))
            if link:
                await self.telegram.send_message(
                    int(chat_id),
                    f"✅ Вы подключены как <b>{html.escape(link['asana_name'])}</b>.\n"
                    "Команды: /status, /mute, /unmute",
                )
            else:
                await self.telegram.send_message(
                    int(chat_id),
                    "Привет! Я отправляю личные алерты по задачам Asana.\n\n"
                    "Для подключения отправьте:\n<code>/link КОД</code>",
                )
            return

        if command == "/link":
            await self.link_user(int(chat_id), sender, argument)
            return

        if command == "/status":
            link = self.db.get_link_by_chat(int(chat_id))
            if not link:
                await self.telegram.send_message(
                    int(chat_id), "Вы ещё не подключены. Используйте <code>/link КОД</code>."
                )
            else:
                state = "приостановлены" if link["muted"] else "включены"
                await self.telegram.send_message(
                    int(chat_id),
                    f"Asana: <b>{html.escape(link['asana_name'])}</b>\n"
                    f"Уведомления: <b>{state}</b>",
                )
            return

        if command in ("/mute", "/unmute"):
            muted = command == "/mute"
            if self.db.set_muted(int(chat_id), muted):
                text_reply = (
                    "🔕 Уведомления приостановлены. Вернуть: /unmute"
                    if muted
                    else "🔔 Уведомления снова включены."
                )
            else:
                text_reply = "Сначала подключите Asana через <code>/link КОД</code>."
            await self.telegram.send_message(int(chat_id), text_reply)
            return

        await self.telegram.send_message(
            int(chat_id),
            "Команды:\n/start — подключение\n/status — состояние\n"
            "/mute — пауза\n/unmute — возобновить",
        )

    async def link_user(
        self, chat_id: int, sender: dict[str, Any], code: str
    ) -> None:
        if not code:
            await self.telegram.send_message(chat_id, "Формат: <code>/link КОД</code>")
            return
        invite = self.db.consume_invite(
            code=code,
            telegram_chat_id=chat_id,
            telegram_user_id=int(sender.get("id") or chat_id),
            telegram_username=sender.get("username"),
        )
        if not invite:
            await self.telegram.send_message(
                chat_id, "❌ Код неверный, уже использован или истёк."
            )
            return
        await self.telegram.send_message(
            chat_id,
            f"✅ Подключено. Asana-пользователь: "
            f"<b>{html.escape(invite['asana_name'])}</b>.\n"
            "Теперь сюда будут приходить только ваши задачи.",
        )

    async def asana_loop(self) -> None:
        while True:
            started = datetime.now(UTC)
            try:
                await self.poll_asana()
                self.last_asana_success = datetime.now(UTC)
                self.last_error = None
            except asyncio.CancelledError:
                raise
            except (httpx.HTTPError, RuntimeError, KeyError, ValueError) as exc:
                self.last_error = f"asana: {type(exc).__name__}"
                LOGGER.exception("Asana polling failed")
            elapsed = (datetime.now(UTC) - started).total_seconds()
            await asyncio.sleep(max(1, self.config.poll_seconds - elapsed))

    async def poll_asana(self) -> None:
        tasks_by_gid: dict[str, dict[str, Any]] = {}
        for project_gid in self.config.project_gids:
            for task in await self.asana.project_tasks(project_gid):
                tasks_by_gid[str(task["gid"])] = task

        baseline_complete = self.db.get_meta("baseline_complete", "0") == "1"
        now = iso_now()
        for task in tasks_by_gid.values():
            previous = self.db.get_task(str(task["gid"]))
            if previous is None:
                self.db.upsert_task(task, now)
                if baseline_complete:
                    await self.notify_assignee(
                        task,
                        f"🆕 <b>Новая задача</b>\n{task_link(task)}"
                        + self.format_due(task)
                        + description_block(task),
                    )
                continue

            old_task = previous["payload"]
            await self.process_task_changes(old_task, task, previous["last_seen_at"])
            self.db.upsert_task(task, now)

        # Completed tasks disappear from the incomplete-project listing. Re-fetch
        # known tasks once to detect completion or removal from the project.
        for previous in self.db.list_active_tasks():
            task_gid = str(previous["task_gid"])
            if task_gid in tasks_by_gid:
                continue
            try:
                current = await self.asana.task(task_gid)
            except AsanaAuthorizationError as exc:
                if exc.status_code == 403:
                    LOGGER.warning(
                        "Stopping checks for inaccessible Asana task %s (HTTP 403)",
                        task_gid,
                    )
                    self.db.mark_task_unavailable(task_gid, "http_403")
                    continue
                raise
            except httpx.HTTPStatusError as exc:
                if exc.response.status_code == 404:
                    LOGGER.info(
                        "Stopping checks for removed Asana task %s (HTTP 404)",
                        task_gid,
                    )
                    self.db.mark_task_unavailable(task_gid, "http_404")
                    continue
                raise
            await self.process_task_changes(
                previous["payload"], current, previous["last_seen_at"]
            )
            self.db.upsert_task(current, now)

        if not baseline_complete:
            self.db.set_meta("baseline_complete", "1")
            LOGGER.info("Initial Asana baseline recorded: %d tasks", len(tasks_by_gid))

    async def process_task_changes(
        self,
        old: dict[str, Any],
        new: dict[str, Any],
        last_seen_at: str,
    ) -> None:
        old_assignee = assignee_gid(old)
        new_assignee = assignee_gid(new)

        if old_assignee != new_assignee:
            if old_assignee:
                await self.notify_gid(
                    old_assignee,
                    f"↗️ <b>Задача передана другому исполнителю</b>\n{task_link(new)}"
                    + description_block(new),
                )
            if new_assignee:
                await self.notify_gid(
                    new_assignee,
                    f"📥 <b>Вам назначена задача</b>\n{task_link(new)}"
                    + self.format_due(new)
                    + description_block(new),
                )

        if (old.get("due_on"), old.get("due_at")) != (
            new.get("due_on"),
            new.get("due_at"),
        ):
            await self.notify_assignee(
                new,
                f"📅 <b>Изменён срок задачи</b>\n{task_link(new)}\n"
                f"Новый срок: <b>{html.escape(due_label(new))}</b>"
                + description_block(new),
            )

        if not old.get("completed") and new.get("completed"):
            await self.notify_assignee(
                new,
                f"✅ <b>Задача завершена</b>\n{task_link(new)}"
                + description_block(new),
            )

        # Old persisted snapshots lack these fields. Seed once without replaying
        # all tasks already in Review BDM when upgrading the running service.
        has_baseline = "memberships" in old and "custom_fields" in old
        if has_baseline and not in_review_bdm(old, self.config.project_gids) and in_review_bdm(new, self.config.project_gids):
            recipient = new_assignee or old_assignee
            await self.notify_gid(
                recipient,
                f"✅ <b>Задача передана в Review BDM</b>\n{task_link(new)}\n"
                "Работа интеграции завершена. Передано на проверку BDM.",
            )

    async def reminder_loop(self) -> None:
        while True:
            try:
                await self.scan_reminders()
            except asyncio.CancelledError:
                raise
            except Exception:  # keep scheduled reminders from killing the service
                LOGGER.exception("Reminder scan failed")
            await asyncio.sleep(self.config.reminder_scan_seconds)

    async def scan_reminders(self) -> None:
        now = datetime.now(self.config.timezone)
        today = now.date()
        for record in self.db.list_active_tasks():
            task = record["payload"]
            if not assignee_gid(task):
                continue
            due_at = task.get("due_at")
            due_on = task.get("due_on")
            if due_at:
                due = parse_datetime(due_at).astimezone(self.config.timezone)
                seconds = (due - now).total_seconds()
                if 0 <= seconds <= 86400:
                    await self.send_reminder_once(task, due_at, "within_24h", "⏰ До срока меньше 24 часов")
                elif seconds < 0:
                    await self.send_reminder_once(task, due_at, "overdue", "🚨 Срок задачи истёк")
            elif due_on:
                due_date = date.fromisoformat(due_on)
                if due_date == today:
                    await self.send_reminder_once(task, due_on, "due_today", "⏰ Срок задачи сегодня")
                elif due_date < today:
                    await self.send_reminder_once(task, due_on, "overdue", "🚨 Задача просрочена")

    async def send_reminder_once(
        self, task: dict[str, Any], due_key: str, kind: str, title: str
    ) -> None:
        task_gid = str(task["gid"])
        if self.db.reminder_sent(task_gid, due_key, kind):
            return
        if await self.notify_assignee(
            task,
            f"{title}\n{task_link(task)}\nСрок: <b>{html.escape(due_label(task))}</b>"
            + description_block(task),
        ):
            self.db.mark_reminder_sent(task_gid, due_key, kind)

    async def notify_assignee(self, task: dict[str, Any], text: str) -> bool:
        gid = assignee_gid(task)
        return await self.notify_gid(gid, text) if gid else False

    async def notify_gid(self, gid: str | None, text: str) -> bool:
        link = self.db.get_link_by_asana(gid)
        if not link or link["muted"]:
            return False
        await self.telegram.send_message(int(link["telegram_chat_id"]), text)
        return True

    @staticmethod
    def format_due(task: dict[str, Any]) -> str:
        if not task.get("due_on") and not task.get("due_at"):
            return ""
        return f"\nСрок: <b>{html.escape(due_label(task))}</b>"

    async def health_server(self) -> None:
        server = await asyncio.start_server(
            self.handle_health_request, "127.0.0.1", self.config.health_port
        )
        async with server:
            await server.serve_forever()

    async def handle_health_request(
        self, reader: asyncio.StreamReader, writer: asyncio.StreamWriter
    ) -> None:
        try:
            await asyncio.wait_for(reader.read(2048), timeout=2)
        except TimeoutError:
            pass
        now = datetime.now(UTC)
        asana_ok = bool(
            self.last_asana_success
            and now - self.last_asana_success
            < timedelta(seconds=max(self.config.poll_seconds * 4, 300))
        )
        telegram_ok = bool(
            self.last_telegram_success
            and now - self.last_telegram_success < timedelta(seconds=180)
        )
        healthy = asana_ok and telegram_ok
        status = "200 OK" if healthy else "503 Service Unavailable"
        body = (
            f'{{"status":"{"ok" if healthy else "error"}",'
            f'"asana":{str(asana_ok).lower()},'
            f'"telegram":{str(telegram_ok).lower()}}}'
        ).encode()
        writer.write(
            f"HTTP/1.1 {status}\r\nContent-Type: application/json\r\n"
            f"Content-Length: {len(body)}\r\nConnection: close\r\n\r\n".encode()
            + body
        )
        await writer.drain()
        writer.close()
        await writer.wait_closed()
