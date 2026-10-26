"""کیبوردهای رابط کاربری ربات."""

from __future__ import annotations

from decimal import Decimal, ROUND_HALF_UP, localcontext
from typing import Iterable

from aiogram.types import InlineKeyboardButton, InlineKeyboardMarkup, KeyboardButton, ReplyKeyboardMarkup

from config import MINING_CAP_OPTIONS, SPEED_PACKAGES


BTN_MINE = "⛏ دریافت ماین"
BTN_BALANCE = "💰 موجودی من"
BTN_SPEED = "🚀 ارتقای سرعت"
BTN_REFERRAL = "👥 دعوت دوستان"
BTN_WITHDRAW = "🏦 درخواست برداشت"
BTN_CAP = "🎯 سقف ماین"
BTN_HELP = "ℹ️ راهنما"
BTN_ADMIN = "🛠 پنل مدیریت"


def _short_amount(value: Decimal | int | str) -> str:
    """مبلغ دکمه‌ها هم مانند سایر متن‌ها حداکثر چهار رقم اعشار دارد."""
    amount = Decimal(str(value))
    with localcontext() as context:
        context.prec = max(40, len(amount.as_tuple().digits) + 10)
        amount = amount.quantize(Decimal("0.0001"), rounding=ROUND_HALF_UP)
    text = format(amount, "f")
    if "." in text:
        text = text.rstrip("0").rstrip(".")
    return text or "0"


def main_menu(is_admin: bool = False) -> ReplyKeyboardMarkup:
    rows = [
        [KeyboardButton(text=BTN_MINE), KeyboardButton(text=BTN_BALANCE)],
        [KeyboardButton(text=BTN_SPEED), KeyboardButton(text=BTN_WITHDRAW)],
        [KeyboardButton(text=BTN_REFERRAL), KeyboardButton(text=BTN_CAP)],
        [KeyboardButton(text=BTN_HELP)],
    ]
    if is_admin:
        rows.append([KeyboardButton(text=BTN_ADMIN)])
    return ReplyKeyboardMarkup(keyboard=rows, resize_keyboard=True)


def cancel_keyboard() -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup(
        inline_keyboard=[[InlineKeyboardButton(text="لغو عملیات", callback_data="cancel")]]
    )


def mining_cap_keyboard(current_cap: Decimal | int | str) -> InlineKeyboardMarkup:
    current = Decimal(str(current_cap))
    buttons: list[list[InlineKeyboardButton]] = []
    for cap in MINING_CAP_OPTIONS:
        selected = Decimal(str(cap)) == current
        label = f"{'✅ ' if selected else ''}{cap:,} TRON"
        buttons.append([InlineKeyboardButton(text=label, callback_data=f"cap:{cap}")])
    return InlineKeyboardMarkup(inline_keyboard=buttons)


def speed_packages_keyboard() -> InlineKeyboardMarkup:
    buttons = []
    for multiplier, price in sorted(SPEED_PACKAGES.items()):
        buttons.append(
            [
                InlineKeyboardButton(
                    text=f"سرعت ×{multiplier} — {_short_amount(price)} TRON",
                    callback_data=f"speed:{multiplier}",
                )
            ]
        )
    buttons.append([InlineKeyboardButton(text="لغو", callback_data="cancel")])
    return InlineKeyboardMarkup(inline_keyboard=buttons)


def admin_panel_keyboard() -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup(
        inline_keyboard=[
            [
                InlineKeyboardButton(text="👥 فهرست کاربران", callback_data="admin:users"),
                InlineKeyboardButton(text="🔎 جست‌وجوی کاربر", callback_data="admin:search"),
            ],
            [
                InlineKeyboardButton(text="💱 تغییر موجودی", callback_data="admin:balance"),
                InlineKeyboardButton(text="🎯 تغییر سقف ماین", callback_data="admin:cap"),
            ],
            [
                InlineKeyboardButton(text="⚡ تغییر ضریب سرعت", callback_data="admin:speed"),
                InlineKeyboardButton(text="💳 پرداخت‌های در انتظار", callback_data="admin:payments"),
            ],
            [
                InlineKeyboardButton(text="🏦 برداشت‌های در انتظار", callback_data="admin:withdrawals"),
                InlineKeyboardButton(text="📊 آمار کلی", callback_data="admin:stats"),
            ],
            [InlineKeyboardButton(text="🏷 تغییر آدرس TRON", callback_data="admin:wallet")],
        ]
    )


def admin_balance_mode_keyboard() -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup(
        inline_keyboard=[
            [
                InlineKeyboardButton(text="➕ افزایش", callback_data="admin_balance:increase"),
                InlineKeyboardButton(text="➖ کاهش", callback_data="admin_balance:decrease"),
            ],
            [InlineKeyboardButton(text="لغو", callback_data="cancel")],
        ]
    )


def _pagination_row(prefix: str, offset: int, total: int, page_size: int) -> list[InlineKeyboardButton]:
    buttons: list[InlineKeyboardButton] = []
    if offset > 0:
        buttons.append(
            InlineKeyboardButton(
                text="◀️ قبلی",
                callback_data=f"{prefix}:{max(0, offset - page_size)}",
            )
        )
    if offset + page_size < total:
        buttons.append(
            InlineKeyboardButton(
                text="بعدی ▶️",
                callback_data=f"{prefix}:{offset + page_size}",
            )
        )
    return buttons


def users_page_keyboard(offset: int, total: int, page_size: int) -> InlineKeyboardMarkup:
    rows: list[list[InlineKeyboardButton]] = []
    navigation = _pagination_row("admin_users", offset, total, page_size)
    if navigation:
        rows.append(navigation)
    rows.append([InlineKeyboardButton(text="⬅️ پنل مدیریت", callback_data="admin:home")])
    return InlineKeyboardMarkup(inline_keyboard=rows)


def payments_page_keyboard(
    payments: Iterable[object], offset: int, total: int, page_size: int
) -> InlineKeyboardMarkup:
    rows: list[list[InlineKeyboardButton]] = []
    for payment in payments:
        payment_id = int(getattr(payment, "id"))
        rows.append(
            [
                InlineKeyboardButton(
                    text=f"✅ تأیید پرداخت #{payment_id}",
                    callback_data=f"admin_payment:approve:{payment_id}",
                ),
                InlineKeyboardButton(
                    text=f"❌ رد پرداخت #{payment_id}",
                    callback_data=f"admin_payment:reject:{payment_id}",
                ),
            ]
        )
    navigation = _pagination_row("admin_payments", offset, total, page_size)
    if navigation:
        rows.append(navigation)
    rows.append([InlineKeyboardButton(text="⬅️ پنل مدیریت", callback_data="admin:home")])
    return InlineKeyboardMarkup(inline_keyboard=rows)


def withdrawals_page_keyboard(
    withdrawals: Iterable[object], offset: int, total: int, page_size: int
) -> InlineKeyboardMarkup:
    rows: list[list[InlineKeyboardButton]] = []
    for withdrawal in withdrawals:
        withdrawal_id = int(getattr(withdrawal, "id"))
        rows.append(
            [
                InlineKeyboardButton(
                    text=f"✅ تأیید واریز #{withdrawal_id}",
                    callback_data=f"admin_withdrawal:approve:{withdrawal_id}",
                ),
                InlineKeyboardButton(
                    text=f"❌ رد برداشت #{withdrawal_id}",
                    callback_data=f"admin_withdrawal:reject:{withdrawal_id}",
                ),
            ]
        )
    navigation = _pagination_row("admin_withdrawals", offset, total, page_size)
    if navigation:
        rows.append(navigation)
    rows.append([InlineKeyboardButton(text="⬅️ پنل مدیریت", callback_data="admin:home")])
    return InlineKeyboardMarkup(inline_keyboard=rows)
