import logging

from aiogram import Bot
from aiogram.types import InlineKeyboardButton, InlineKeyboardMarkup

from app.domain import TICKET_TYPE_DESTINATION, TicketRecord
from app.settings import Settings
from app.views.helpers import html


class NotificationService:
    def __init__(self, bot: Bot, settings: Settings) -> None:
        self._bot = bot
        self._settings = settings

    async def new_ticket(self, ticket: TicketRecord, author_name: str) -> None:
        ticket_type = (
            "Destination"
            if ticket.ticket_type == TICKET_TYPE_DESTINATION
            else "Другая задача"
        )
        text = (
            f"📬 <b>Создана задача <code>#{ticket.ticket_id}</code></b>\n\n"
            f"<b>Автор:</b> <a href='tg://user?id={ticket.owner_id}'>{html(author_name)}</a>\n"
            f"<b>Telegram ID:</b> <code>{ticket.owner_id}</code>\n\n"
            f"<b>Тип:</b> {ticket_type}\n\n"
            f"<b>Описание:</b>\n{html(ticket.body)}"
        )
        if ticket.latest_comment:
            text += f"\n\n<b>Внутренняя пометка:</b>\n{html(ticket.latest_comment)}"
        keyboard = InlineKeyboardMarkup(inline_keyboard=[
            [InlineKeyboardButton(text="Открыть задачу", callback_data=f"ticket_{ticket.ticket_id}")],
            [InlineKeyboardButton(text="🤘 Все задачи", callback_data="admin_panel")],
        ])
        try:
            await self._bot.send_message(
                self._settings.admin_message,
                text,
                reply_markup=keyboard,
                parse_mode="HTML",
            )
        except Exception:
            logging.exception(
                "Не удалось уведомить технаря о новой задаче %s",
                ticket.ticket_id,
            )

    async def completed(
        self,
        ticket: TicketRecord,
        actor_id: int,
        latest_comment: str,
    ) -> None:
        text = (
            f"🎉 Задача <code>#{ticket.ticket_id}</code> выполнена!\n"
            "\n"
            f"<b>Ответ исполнителя:</b> {html(latest_comment)}\n\n"
            "<em>Пожалуйста, проверьте корректность исполнения задачи.</em>"
        )
        user_keyboard = InlineKeyboardMarkup(inline_keyboard=[
            [InlineKeyboardButton(text="↩️ Вернуть в работу", callback_data=f"reopen_{ticket.ticket_id}")],
            [
                InlineKeyboardButton(text="☑️ История задач", callback_data="my_ticket_history"),
                InlineKeyboardButton(text="🧑‍💻 Главное меню", callback_data="main_menu"),
            ],
        ])
        technician_keyboard = InlineKeyboardMarkup(inline_keyboard=[
            [InlineKeyboardButton(text="↩️ Вернуть в работу", callback_data=f"reopen_{ticket.ticket_id}")],
            [InlineKeyboardButton(text="🤘 Все задачи", callback_data="admin_panel")],
        ])
        try:
            await self._bot.send_message(
                ticket.owner_id,
                text,
                reply_markup=user_keyboard,
                parse_mode="HTML",
            )
        except Exception:
            logging.exception(
                "Не удалось уведомить владельца о завершении задачи %s",
                ticket.ticket_id,
            )
        if actor_id != ticket.owner_id:
            try:
                await self._bot.send_message(
                    actor_id,
                    text,
                    reply_markup=technician_keyboard,
                    parse_mode="HTML",
                )
            except Exception:
                logging.exception(
                    "Не удалось подтвердить завершение задачи %s технарю",
                    ticket.ticket_id,
                )

    async def reopened(self, ticket: TicketRecord, actor_id: int) -> None:
        if actor_id == ticket.owner_id:
            recipient = ticket.assigned_to or self._settings.admin_message
            text = (
                f"↩️ Пользователь вернул задачу "
                f"<code>#{ticket.ticket_id}</code> в работу."
            )
            callback = f"ticket_{ticket.ticket_id}"
            button_text = "Открыть задачу"
        else:
            recipient = ticket.owner_id
            text = f"↩️ Задача <code>#{ticket.ticket_id}</code> возвращена в работу."
            callback = "my_ticket"
            button_text = "📥 Мои задачи"

        try:
            await self._bot.send_message(
                recipient,
                text,
                reply_markup=InlineKeyboardMarkup(inline_keyboard=[
                    [InlineKeyboardButton(text=button_text, callback_data=callback)]
                ]),
                parse_mode="HTML",
            )
        except Exception:
            logging.exception(
                "Не удалось отправить уведомление о возврате задачи %s",
                ticket.ticket_id,
            )

    async def public_message(
        self,
        ticket: TicketRecord,
        actor_id: int,
        message_text: str,
    ) -> None:
        if actor_id == ticket.owner_id:
            recipient = ticket.assigned_to or self._settings.admin_message
            callback = f"ticket_{ticket.ticket_id}"
            sender = "Клиент"
        else:
            recipient = ticket.owner_id
            callback = f"user_ticket_{ticket.ticket_id}"
            sender = "Поддержка"
        if recipient == actor_id:
            return
        text = (
            f"💬 <b>{sender} написал в задачу <code>#{ticket.ticket_id}</code></b>\n\n"
            f"{html(message_text)}"
        )
        try:
            await self._bot.send_message(
                recipient,
                text,
                reply_markup=InlineKeyboardMarkup(inline_keyboard=[
                    [InlineKeyboardButton(text="Открыть задачу", callback_data=callback)]
                ]),
                parse_mode="HTML",
            )
        except Exception:
            logging.exception(
                "Не удалось отправить сообщение по задаче %s",
                ticket.ticket_id,
            )
