from aiogram.types import InlineKeyboardButton, InlineKeyboardMarkup

from .helpers import html


def main_menu(open_count: int, closed_count: int, is_technician: bool):
    text = (
        "<b>🧑‍💻 Главное меню</b>\n\n"
        f"<b>📬 Открытых задач:</b> {open_count}\n"
        f"<b>📭 Завершённых задач:</b> {closed_count}\n\n"
        "Выберите действие:"
    )
    buttons = [
        [
            InlineKeyboardButton(text="📥 Мои задачи", callback_data="my_ticket"),
            InlineKeyboardButton(text="📤 Новая задача", callback_data="new_ticket"),
        ],
        [InlineKeyboardButton(text="🔗 Postback", callback_data="postbacks")],
    ]
    if is_technician:
        buttons.append([
            InlineKeyboardButton(text="🤘 Все задачи", callback_data="admin_panel")
        ])
    return text, InlineKeyboardMarkup(inline_keyboard=buttons)


def new_ticket_prompt():
    text = (
        "<b>📤 Создание новой задачи</b>\n\n"
        "Опишите задачу и укажите все важные детали."
    )
    keyboard = InlineKeyboardMarkup(inline_keyboard=[
        [InlineKeyboardButton(text="⬅️ Назад", callback_data="new_ticket")]
    ])
    return text, keyboard


def ticket_type_prompt():
    text = (
        "<b>📤 Создание новой задачи</b>\n\n"
        "Выберите тип задачи."
    )
    keyboard = InlineKeyboardMarkup(inline_keyboard=[
        [InlineKeyboardButton(
            text="🎯 Destination",
            callback_data="ticket_type_destination",
        )],
        [InlineKeyboardButton(
            text="📝 Другая задача",
            callback_data="ticket_type_general",
        )],
        [InlineKeyboardButton(text="⬅️ Назад", callback_data="main_menu")],
    ])
    return text, keyboard


def destination_advertiser_prompt():
    text = (
        "<b>🎯 Destination</b>\n\n"
        "Укажите Advertiser.\n\n"
        "Например: <code>V.Partners</code>"
    )
    keyboard = InlineKeyboardMarkup(inline_keyboard=[
        [InlineKeyboardButton(text="⬅️ Назад", callback_data="new_ticket")]
    ])
    return text, keyboard


def destination_offer_prompt(advertiser: str):
    text = (
        "<b>🎯 Destination</b>\n\n"
        f"<b>Advertiser:</b> <code>{html(advertiser)}</code>\n\n"
        "Отправьте полное тело оффера одним сообщением."
    )
    keyboard = InlineKeyboardMarkup(inline_keyboard=[
        [InlineKeyboardButton(
            text="⬅️ Назад",
            callback_data="ticket_type_destination",
        )]
    ])
    return text, keyboard


def ticket_created(ticket_id: int, *, formatting_failed: bool = False):
    text = f"✅ Задача зарегистрирована.\n\n<b>Номер:</b> <code>#{ticket_id}</code>."
    if formatting_failed:
        text += (
            "\n\n⚠️ Автоматическое оформление не сработало. "
            "Исходный оффер сохранён, задача передана технарю."
        )
    keyboard = InlineKeyboardMarkup(inline_keyboard=[
        [InlineKeyboardButton(text="Открыть задачу", callback_data=f"user_ticket_{ticket_id}")],
        [InlineKeyboardButton(text="🧑‍💻 Главное меню", callback_data="main_menu")],
    ])
    return text, keyboard
