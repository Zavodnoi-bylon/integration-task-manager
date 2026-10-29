from aiogram.types import InlineKeyboardButton, InlineKeyboardMarkup

from .helpers import split_plain_text


def postback_menu(tenants: dict[str, str]):
    buttons = [
        [InlineKeyboardButton(text=name, callback_data=f"postback_{key}")]
        for key, name in tenants.items()
    ]
    buttons.append([InlineKeyboardButton(text="⬅️ Назад", callback_data="main_menu")])
    return (
        "<b>🔗 Postback</b>\n\nВыберите тенант:",
        InlineKeyboardMarkup(inline_keyboard=buttons),
    )


def postback_result_keyboard():
    return InlineKeyboardMarkup(inline_keyboard=[
        [InlineKeyboardButton(text="⬅️ К выбору тенанта", callback_data="postbacks")],
        [InlineKeyboardButton(text="🧑‍💻 Главное меню", callback_data="main_menu")],
    ])


__all__ = ["postback_menu", "postback_result_keyboard", "split_plain_text"]
