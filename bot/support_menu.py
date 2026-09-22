from aiogram.types import InlineKeyboardButton, InlineKeyboardMarkup

from bot import texts

SUPPORT_CB = "support"


def support_keyboard() -> InlineKeyboardMarkup:
    rows = [
        [
            InlineKeyboardButton(
                text=label, callback_data=f"{SUPPORT_CB}:cat:{key}"
            )
        ]
        for key, label in texts.SUPPORT_CATEGORIES.items()
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
    return InlineKeyboardMarkup(
        inline_keyboard=[
            [
                InlineKeyboardButton(
                    text=texts.BTN_SUPPORT, callback_data=f"{SUPPORT_CB}:start"
                )
            ]
        ]
    )