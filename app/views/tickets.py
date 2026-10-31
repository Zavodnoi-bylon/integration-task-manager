from collections.abc import Mapping, Sequence

from aiogram.types import InlineKeyboardButton, InlineKeyboardMarkup

from app.domain import (
    EVENT_COMMENT,
    EVENT_CREATED,
    EVENT_PUBLIC_MESSAGE,
    EVENT_STATUS_CHANGED,
    STATUS_COMPLETED,
    STATUS_OPEN,
    TICKET_TYPE_DESTINATION,
    TicketEventRecord,
    TicketPage,
    TicketRecord,
)

from .helpers import html, short_text


def render_event_history(
    events: Sequence[TicketEventRecord],
    actor_names: Mapping[int, str],
) -> str:
    if not events:
        return "<i>История появится после следующего изменения задачи.</i>"

    lines: list[str] = []
    for event in events:
        actor_name = html(actor_names.get(event.actor_id, f"ID {event.actor_id}"))
        if event.event_type == EVENT_CREATED:
            action = "создал задачу"
        elif event.event_type == EVENT_COMMENT:
            action = f"добавил внутреннюю заметку: {html(event.text or '')}"
        elif event.event_type == EVENT_PUBLIC_MESSAGE:
            action = f"отправил сообщение клиенту: {html(event.text or '')}"
        elif event.event_type == EVENT_STATUS_CHANGED:
            action = (
                f"изменил статус: {html(event.from_status or '')} → "
                f"{html(event.to_status or '')}"
            )
        else:
            action = html(event.event_type)
        lines.append(f"• {event.created_at} — {actor_name} {action}")
    return "\n".join(lines)


def render_public_conversation(
    events: Sequence[TicketEventRecord],
    actor_names: Mapping[int, str],
    viewer_id: int,
    *,
    admin_view: bool,
) -> str:
    messages = [event for event in events if event.event_type == EVENT_PUBLIC_MESSAGE]
    if not messages:
        return "<i>Сообщений пока нет.</i>"
    lines = []
    for event in messages:
        if admin_view:
            author = html(actor_names.get(event.actor_id, f"ID {event.actor_id}"))
        else:
            author = "Вы" if event.actor_id == viewer_id else "Поддержка"
        lines.append(f"• <b>{author}:</b> {html(event.text or '')}")
    return "\n".join(lines)


def ticket_card(
    ticket: TicketRecord,
    owner_name: str,
    *,
    admin_view: bool,
    events: Sequence[TicketEventRecord] = (),
    actor_names: Mapping[int, str] | None = None,
    viewer_id: int | None = None,
):
    text = f"<b>Задача <code>#{ticket.ticket_id}</code></b>\n\n"
    if admin_view:
        ticket_type = (
            "Destination"
            if ticket.ticket_type == TICKET_TYPE_DESTINATION
            else "Другая задача"
        )
        text += (
            f"<b>Автор:</b> <a href='tg://user?id={ticket.owner_id}'>{html(owner_name)}</a>\n"
            f"<b>Тип:</b> {ticket_type}\n"
            f"<b>Статус:</b> {html(ticket.status)}\n"
            f"<b>Создана:</b> {ticket.created_at}\n"
            f"<b>Обновлена:</b> {ticket.updated_at}\n"
        )
        if ticket.closed_at:
            text += f"<b>Завершена:</b> {ticket.closed_at}\n"
        text += "\n"
    text += f"<b>Описание:</b>\n{html(ticket.body)}"

    text += (
        "\n\n<b>Переписка:</b>\n"
        f"{render_public_conversation(events, actor_names or {}, viewer_id or 0, admin_view=admin_view)}"
    )

    if admin_view:
        text += (
            "\n\n<b>История:</b>\n"
            f"{render_event_history(events, actor_names or {})}"
        )

    buttons: list[list[InlineKeyboardButton]] = []
    if admin_view and ticket.status == STATUS_OPEN:
        buttons.extend([
            [InlineKeyboardButton(
                text="💬 Ответить клиенту",
                callback_data=f"public_message_{ticket.ticket_id}",
            )],
            [InlineKeyboardButton(
                text="📝 Внутренняя заметка",
                callback_data=f"add_comment_{ticket.ticket_id}",
            )],
            [InlineKeyboardButton(
                text="✅ Завершить",
                callback_data=f"complete_{ticket.ticket_id}",
            )],
        ])
    elif not admin_view and ticket.status == STATUS_OPEN:
        buttons.append([InlineKeyboardButton(
            text="💬 Написать в тикет",
            callback_data=f"public_message_{ticket.ticket_id}",
        )])
    elif ticket.status == STATUS_COMPLETED:
        buttons.append([InlineKeyboardButton(
            text="↩️ Вернуть в работу",
            callback_data=f"reopen_{ticket.ticket_id}",
        )])

    if admin_view:
        back = "admin_panel" if ticket.status == STATUS_OPEN else "completed_tickets"
    else:
        back = "my_ticket" if ticket.status == STATUS_OPEN else "my_ticket_history"
    buttons.append([InlineKeyboardButton(text="⬅️ Назад", callback_data=back)])
    return text, InlineKeyboardMarkup(inline_keyboard=buttons)


def user_ticket_list(page: TicketPage, *, completed: bool):
    if page.items:
        title = "📨 Завершённые задачи" if completed else "📥 Мои задачи в работе"
        text = f"<b>{title}</b>\n\n<b>Количество:</b> {page.total}"
    else:
        text = (
            "<b>📨 Завершённые задачи</b>\n\nИстория пока пуста."
            if completed
            else "<b>📥 Мои задачи</b>\n\nУ вас пока нет открытых задач."
        )

    buttons = [
        [InlineKeyboardButton(
            text=f"#{ticket.ticket_id} · {short_text(ticket.body)}",
            callback_data=f"user_ticket_{ticket.ticket_id}",
        )]
        for ticket in page.items
    ]
    nav: list[InlineKeyboardButton] = []
    prefix = "history_page" if completed else "open_page"
    if page.has_previous:
        nav.append(InlineKeyboardButton(text="←", callback_data=f"{prefix}_{page.page - 1}"))
    if page.has_next:
        nav.append(InlineKeyboardButton(text="→", callback_data=f"{prefix}_{page.page + 1}"))
    if nav:
        buttons.append(nav)

    if not completed:
        buttons.append([InlineKeyboardButton(
            text="☑️ История задач",
            callback_data="my_ticket_history",
        )])
        back = "main_menu"
    else:
        back = "my_ticket"
    buttons.append([InlineKeyboardButton(text="⬅️ Назад", callback_data=back)])
    return text, InlineKeyboardMarkup(inline_keyboard=buttons)


def technician_ticket_list(page: TicketPage, *, completed: bool, other_total: int):
    if completed:
        text = (
            "<b>✅ Последние завершённые задачи</b>\n\n"
            "Выберите задачу, чтобы посмотреть её или вернуть в работу."
        ) if page.items else "<b>✅ Завершённые задачи</b>\n\nЗавершённых задач пока нет."
    else:
        text = (
            "<b>🤘 Тикет меню</b>\n\n"
            f"<b>🔥 Заявок в работе:</b> {page.total}\n"
            f"<b>👍 Завершённых заявок:</b> {other_total}"
        )

    callback_prefix = "completed_ticket" if completed else "ticket"
    buttons = [
        [InlineKeyboardButton(
            text=f"#{ticket.ticket_id} · {short_text(ticket.body)}",
            callback_data=f"{callback_prefix}_{ticket.ticket_id}",
        )]
        for ticket in page.items
    ]
    nav: list[InlineKeyboardButton] = []
    page_prefix = "completed_page" if completed else "admin_page"
    if page.has_previous:
        nav.append(InlineKeyboardButton(text="←", callback_data=f"{page_prefix}_{page.page - 1}"))
    if page.has_next:
        nav.append(InlineKeyboardButton(text="→", callback_data=f"{page_prefix}_{page.page + 1}"))
    if nav:
        buttons.append(nav)

    if completed:
        back = "admin_panel"
    else:
        buttons.append([InlineKeyboardButton(
            text="✅ Завершённые задачи",
            callback_data="completed_tickets",
        )])
        back = "main_menu"
    buttons.append([InlineKeyboardButton(text="⬅️ Назад", callback_data=back)])
    return text, InlineKeyboardMarkup(inline_keyboard=buttons)


def comment_prompt(ticket_id: int):
    keyboard = InlineKeyboardMarkup(inline_keyboard=[
        [InlineKeyboardButton(text="⬅️ Назад", callback_data=f"ticket_{ticket_id}")]
    ])
    return (
        f"<b>Внутренняя заметка к задаче <code>#{ticket_id}</code></b>\n\n"
        "Эту запись увидят только администраторы и технарь.",
        keyboard,
    )


def comment_saved(ticket_id: int, comment: str):
    keyboard = InlineKeyboardMarkup(inline_keyboard=[
        [InlineKeyboardButton(text="📝 Добавить ещё", callback_data=f"add_comment_{ticket_id}")],
        [InlineKeyboardButton(text="✅ Завершить задачу", callback_data=f"complete_{ticket_id}")],
        [InlineKeyboardButton(text="⬅️ К задаче", callback_data=f"ticket_{ticket_id}")],
    ])
    return (
        f"<b>Внутренняя заметка к задаче <code>#{ticket_id}</code> сохранена.</b>\n\n"
        f"{html(comment)}",
        keyboard,
    )


def public_message_prompt(ticket_id: int, *, admin_view: bool):
    back = f"ticket_{ticket_id}" if admin_view else f"user_ticket_{ticket_id}"
    keyboard = InlineKeyboardMarkup(inline_keyboard=[
        [InlineKeyboardButton(text="⬅️ Назад", callback_data=back)]
    ])
    return (
        f"<b>Сообщение в задачу <code>#{ticket_id}</code></b>\n\n"
        "Отправьте текст. Он будет виден клиенту и поддержке.",
        keyboard,
    )


def public_message_saved(ticket_id: int, *, admin_view: bool):
    back = f"ticket_{ticket_id}" if admin_view else f"user_ticket_{ticket_id}"
    keyboard = InlineKeyboardMarkup(inline_keyboard=[
        [InlineKeyboardButton(text="💬 Написать ещё", callback_data=f"public_message_{ticket_id}")],
        [InlineKeyboardButton(text="⬅️ К задаче", callback_data=back)],
    ])
    return "Сообщение сохранено и отправлено.", keyboard
