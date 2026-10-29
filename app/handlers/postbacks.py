import logging

from aiogram import F, Router
from aiogram.fsm.context import FSMContext
from aiogram.types import CallbackQuery

from app.services.postback_service import PostbackService
from app.views.postbacks import (
    postback_menu,
    postback_result_keyboard,
    split_plain_text,
)

from .helpers import edit_text_if_changed


router = Router(name="postbacks")


@router.callback_query(F.data == "postbacks")
async def show_postbacks(
    query: CallbackQuery,
    state: FSMContext,
    postback_service: PostbackService,
):
    tenants = postback_service.list_tenants()
    if not tenants:
        await query.answer("Для этого бота шаблоны не настроены", show_alert=True)
        return
    await state.clear()
    await query.answer()
    text, keyboard = postback_menu(tenants)
    await edit_text_if_changed(query.message, text, reply_markup=keyboard, parse_mode="HTML")


@router.callback_query(F.data.startswith("postback_"))
async def send_postback(
    query: CallbackQuery,
    state: FSMContext,
    postback_service: PostbackService,
):
    tenant_key = query.data.removeprefix("postback_")
    try:
        template = postback_service.get_template(tenant_key)
    except (KeyError, OSError):
        logging.exception("Не удалось загрузить postback-шаблон %s", tenant_key)
        await query.answer("Шаблон недоступен", show_alert=True)
        return

    await state.clear()
    await query.answer()
    chunks = split_plain_text(template)
    keyboard = postback_result_keyboard()
    for index, chunk in enumerate(chunks):
        await query.message.answer(
            chunk,
            reply_markup=keyboard if index == len(chunks) - 1 else None,
        )
