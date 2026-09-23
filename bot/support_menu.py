from aiogram.types import InlineKeyboardButton, InlineKeyboardMarkup

import config
from bot import texts

SUPPORT_CB = "support"


def support_keyboard() -> InlineKeyboardMarkup:
    rows = [
        [
            InlineKeyboardButton(
                text=texts.BTN_SUBSCRIBE,
                callback_data=f"{SUPPORT_CB}:subscribe",
            )
        ],
        *[
            [
                InlineKeyboardButton(
                    text=label, callback_data=f"{SUPPORT_CB}:cat:{key}"
                )
            ]
            for key, label in texts.SUPPORT_CATEGORIES.items()
        ],
    ]
    rows.append(
        [
            InlineKeyboardButton(
                text="إلغاء / إنهاء", callback_data=f"{SUPPORT_CB}:end"
            )
        ]
    )
    return InlineKeyboardMarkup(inline_keyboard=rows)


def support_start_keyboard() -> InlineKeyboardMarkup:
    if config.SUPPORT_DEDICATED and config.SUPPORT_BOT_USERNAME:
        return InlineKeyboardMarkup(
            inline_keyboard=[
                [
                    InlineKeyboardButton(
                        text=texts.BTN_SUPPORT,
                        url=f"https://t.me/{config.SUPPORT_BOT_USERNAME}",
                    )
                ]
            ]
        )
    return InlineKeyboardMarkup(
        inline_keyboard=[
            [
                InlineKeyboardButton(
                    text=texts.BTN_SUPPORT,
                    callback_data=f"{SUPPORT_CB}:start",
                )
            ]
        ]
    )


def satisfaction_keyboard(msg_id: int) -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup(
        inline_keyboard=[
            [
                InlineKeyboardButton(
                    text=texts.BTN_SATISFIED,
                    callback_data=f"{SUPPORT_CB}:satisfy:{msg_id}",
                )
            ],
            [
                InlineKeyboardButton(
                    text=texts.BTN_REOPEN,
                    callback_data=f"{SUPPORT_CB}:reopen:{msg_id}",
                )
            ],
        ]
    )