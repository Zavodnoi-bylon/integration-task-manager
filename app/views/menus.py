from collections.abc import Sequence

from aiogram.types import InlineKeyboardButton, InlineKeyboardMarkup

from app.domain import AdvertiserEntry

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
            text="Destination",
            callback_data="ticket_type_destination",
        )],
        [InlineKeyboardButton(
            text="📝 Другая задача",
            callback_data="ticket_type_general",
        )],
        [InlineKeyboardButton(text="⬅️ Назад", callback_data="main_menu")],
    ])
    return text, keyboard


def _advertiser_rows(
    entries: Sequence[AdvertiserEntry],
) -> list[list[InlineKeyboardButton]]:
    buttons = [
        InlineKeyboardButton(
            text=entry.name,
            callback_data=f"dest_adv_select:{entry.key}",
        )
        for entry in entries
    ]
    return [buttons[index:index + 2] for index in range(0, len(buttons), 2)]


def destination_advertiser_prompt(
    popular: Sequence[AdvertiserEntry] = (),
):
    text = (
        "<b>Destination</b>\n\n"
        "Выберите advertiser."
    )
    if popular:
        text += "\n\nЧасто используемые:"
    rows = _advertiser_rows(popular)
    rows.extend([
        [
            InlineKeyboardButton(text="Поиск", callback_data="dest_adv_search"),
            InlineKeyboardButton(text="Все", callback_data="dest_adv_all:0"),
        ],
        [InlineKeyboardButton(
            text="Новый advertiser",
            callback_data="dest_adv_custom",
        )],
        [InlineKeyboardButton(text="Назад", callback_data="new_ticket")],
    ])
    keyboard = InlineKeyboardMarkup(inline_keyboard=rows)
    return text, keyboard


def destination_advertiser_search_prompt():
    text = (
        "<b>Поиск advertiser</b>\n\n"
        "Отправьте часть названия одним сообщением."
    )
    keyboard = InlineKeyboardMarkup(inline_keyboard=[
        [InlineKeyboardButton(text="Назад", callback_data="ticket_type_destination")]
    ])
    return text, keyboard


def destination_custom_advertiser_prompt():
    text = (
        "<b>Новый advertiser</b>\n\n"
        "Отправьте точное название одним сообщением. Для него серверный URL-хвост "
        "пока не настроен."
    )
    keyboard = InlineKeyboardMarkup(inline_keyboard=[
        [InlineKeyboardButton(text="Назад", callback_data="ticket_type_destination")]
    ])
    return text, keyboard


def destination_advertiser_results(
    title: str,
    entries: Sequence[AdvertiserEntry],
    *,
    page: int | None = None,
    total_pages: int | None = None,
):
    text = f"<b>{html(title)}</b>"
    if page is not None and total_pages is not None:
        text += f"\n\nСтраница {page + 1} из {total_pages}."
    rows = _advertiser_rows(entries)
    if page is not None and total_pages is not None:
        navigation: list[InlineKeyboardButton] = []
        if page > 0:
            navigation.append(InlineKeyboardButton(
                text="Назад",
                callback_data=f"dest_adv_all:{page - 1}",
            ))
        if page + 1 < total_pages:
            navigation.append(InlineKeyboardButton(
                text="Дальше",
                callback_data=f"dest_adv_all:{page + 1}",
            ))
        if navigation:
            rows.append(navigation)
    rows.extend([
        [InlineKeyboardButton(
            text="Новый advertiser",
            callback_data="dest_adv_custom",
        )],
        [InlineKeyboardButton(text="Поиск", callback_data="dest_adv_search")],
        [InlineKeyboardButton(text="К выбору", callback_data="ticket_type_destination")],
    ])
    return text, InlineKeyboardMarkup(inline_keyboard=rows)


def destination_cap_prompt(advertiser: str):
    text = (
        "<b>Destination</b>\n\n"
        f"<b>Advertiser:</b> <code>{html(advertiser)}</code>\n\n"
        "Укажите капу одним целым числом."
    )
    keyboard = InlineKeyboardMarkup(inline_keyboard=[
        [InlineKeyboardButton(
            text="Назад",
            callback_data="ticket_type_destination",
        )]
    ])
    return text, keyboard


def destination_offer_prompt(advertiser: str, cap: int):
    text = (
        "<b>Destination</b>\n\n"
        f"<b>Advertiser:</b> <code>{html(advertiser)}</code>\n"
        f"<b>Капа:</b> <code>{cap}</code>\n\n"
        "Отправьте полное тело оффера одним сообщением."
    )
    keyboard = InlineKeyboardMarkup(inline_keyboard=[
        [InlineKeyboardButton(
            text="Назад",
            callback_data="dest_cap_edit",
        )]
    ])
    return text, keyboard


def ticket_created(ticket_id: int):
    text = f"Задача зарегистрирована.\n\n<b>Номер:</b> <code>#{ticket_id}</code>."
    keyboard = InlineKeyboardMarkup(inline_keyboard=[
        [InlineKeyboardButton(text="Открыть задачу", callback_data=f"user_ticket_{ticket_id}")],
        [InlineKeyboardButton(text="🧑‍💻 Главное меню", callback_data="main_menu")],
    ])
    return text, keyboard
