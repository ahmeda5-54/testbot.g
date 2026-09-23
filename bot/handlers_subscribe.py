from aiogram import F, Router
from aiogram.fsm.context import FSMContext
from aiogram.types import CallbackQuery

import db
from bot import flow, texts
from bot.handlers_verify import (
    on_account,
    on_account_invalid,
    on_broker,
    on_broker_invalid,
    on_mt_choice,
    on_photo,
    on_server,
    on_server_invalid,
)
from bot.states import VerifyState
from bot.support_menu import SUPPORT_CB, support_keyboard

router = Router()

router.message(VerifyState.account, F.text)(on_account)
router.message(VerifyState.account)(on_account_invalid)
router.message(VerifyState.broker, F.text)(on_broker)
router.message(VerifyState.broker)(on_broker_invalid)
router.callback_query(F.data.startswith(f"{flow.MT_BUTTONS}:"))(on_mt_choice)
router.message(VerifyState.server, F.text)(on_server)
router.message(VerifyState.server)(on_server_invalid)
router.message(VerifyState.photo)(on_photo)


@router.callback_query(F.data == f"{SUPPORT_CB}:subscribe")
async def on_subscribe(callback: CallbackQuery, state: FSMContext) -> None:
    await callback.answer()
    user = callback.from_user
    if user is None:
        return

    member = db.get_member(user.id)
    if member is not None and member["status"] in (db.SUBMITTED, db.UNDER_REVIEW):
        try:
            await callback.message.answer(
                texts.VERIFY_IN_REVIEW, reply_markup=support_keyboard()
            )
        except Exception:
            pass
        return
    if member is not None and member["status"] == db.VERIFIED:
        try:
            await callback.message.answer(
                texts.SUBSCRIBE_ALREADY, reply_markup=support_keyboard()
            )
        except Exception:
            pass
        return

    await state.clear()
    try:
        await callback.message.answer(texts.SUBSCRIBE_INTRO)
    except Exception:
        pass
    await flow.begin_for(
        user.id, user.username, user.full_name, callback.message, state
    )