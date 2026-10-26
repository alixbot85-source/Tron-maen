"""کیبوردهای Inline و چیدمان یکپارچهٔ رابط کاربری ربات."""

from __future__ import annotations

from decimal import Decimal, ROUND_HALF_UP, localcontext
from typing import Iterable

from aiogram.types import InlineKeyboardButton, InlineKeyboardMarkup

from config import MINING_CAP_OPTIONS, SPEED_PACKAGES


BACK_TO_MAIN = "🔙 منوی اصلی"
BACK_TO_ADMIN = "🔙 پنل مدیریت"


def _short_amount(value: Decimal | int | str) -> str:
    """مبالغ روی دکمه‌ها را حداکثر با چهار رقم اعشار نمایش می‌دهد."""
    amount = Decimal(str(value))
    with localcontext() as context:
        context.prec = max(40, len(amount.as_tuple().digits) + 10)
        amount = amount.quantize(Decimal("0.0001"), rounding=ROUND_HALF_UP)
    text = format(amount, "f")
    if "." in text:
        text = text.rstrip("0").rstrip(".")
    return text or "0"


def main_menu(is_admin: bool = False) -> InlineKeyboardMarkup:
    """منوی اصلی کاربر؛ همهٔ مسیرهای اصلی در چهار ردیف کوتاه قرار دارند."""
    rows = [
        [
            InlineKeyboardButton(text="⛏️ برداشت ماین", callback_data="menu:mine"),
            InlineKeyboardButton(text="💰 موجودی", callback_data="menu:balance"),
        ],
        [
            InlineKeyboardButton(text="⚡ ارتقای سرعت", callback_data="menu:speed"),
            InlineKeyboardButton(text="🎯 سقف ماین", callback_data="menu:cap"),
        ],
        [
            InlineKeyboardButton(text="👥 دعوت دوستان", callback_data="menu:referral"),
            InlineKeyboardButton(text="📤 برداشت وجه", callback_data="menu:withdraw"),
        ],
        [
            InlineKeyboardButton(text="📊 راهنما", callback_data="menu:help"),
            InlineKeyboardButton(text="👤 حساب من", callback_data="menu:account"),
        ],
    ]
    if is_admin:
        rows.append([InlineKeyboardButton(text="🛠 پنل مدیریت", callback_data="admin:home")])
    return InlineKeyboardMarkup(inline_keyboard=rows)


def back_to_main_keyboard() -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup(
        inline_keyboard=[[InlineKeyboardButton(text=BACK_TO_MAIN, callback_data="menu:main")]]
    )


def account_keyboard(can_withdraw: bool) -> InlineKeyboardMarkup:
    rows: list[list[InlineKeyboardButton]] = [
        [InlineKeyboardButton(text="⛏️ برداشت ماین", callback_data="menu:mine")],
    ]
    if can_withdraw:
        rows.append([InlineKeyboardButton(text="📤 برداشت وجه", callback_data="menu:withdraw")])
    rows.append([InlineKeyboardButton(text=BACK_TO_MAIN, callback_data="menu:main")])
    return InlineKeyboardMarkup(inline_keyboard=rows)


def balance_keyboard() -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup(
        inline_keyboard=[
            [InlineKeyboardButton(text="👤 حساب من", callback_data="menu:account")],
            [InlineKeyboardButton(text=BACK_TO_MAIN, callback_data="menu:main")],
        ]
    )


def mining_result_keyboard(can_withdraw: bool) -> InlineKeyboardMarkup:
    rows: list[list[InlineKeyboardButton]] = [
        [InlineKeyboardButton(text="🔄 به‌روزرسانی ماین", callback_data="menu:mine")],
    ]
    if can_withdraw:
        rows.append([InlineKeyboardButton(text="📤 برداشت وجه", callback_data="menu:withdraw")])
    rows.append([InlineKeyboardButton(text=BACK_TO_MAIN, callback_data="menu:main")])
    return InlineKeyboardMarkup(inline_keyboard=rows)


def mining_cap_keyboard(current_cap: Decimal | int | str) -> InlineKeyboardMarkup:
    current = Decimal(str(current_cap))
    buttons: list[list[InlineKeyboardButton]] = []
    caps = list(MINING_CAP_OPTIONS)
    for index in range(0, len(caps), 2):
        row: list[InlineKeyboardButton] = []
        for cap in caps[index : index + 2]:
            selected = Decimal(str(cap)) == current
            label = f"{'✓ ' if selected else ''}{cap:,} TRON"
            row.append(InlineKeyboardButton(text=label, callback_data=f"cap:{cap}"))
        buttons.append(row)
    buttons.append([InlineKeyboardButton(text=BACK_TO_MAIN, callback_data="menu:main")])
    return InlineKeyboardMarkup(inline_keyboard=buttons)


def speed_packages_keyboard() -> InlineKeyboardMarkup:
    """چهار پکیج سرعت را به‌صورت دو‌تایی و قابل‌خواندن روی موبایل نمایش می‌دهد."""
    package_buttons = [
        InlineKeyboardButton(
            text=f"×{multiplier} | {_short_amount(price)} TRON",
            callback_data=f"speed:{multiplier}",
        )
        for multiplier, price in sorted(SPEED_PACKAGES.items())
    ]
    rows = [package_buttons[index : index + 2] for index in range(0, len(package_buttons), 2)]
    rows.append([InlineKeyboardButton(text=BACK_TO_MAIN, callback_data="menu:main")])
    return InlineKeyboardMarkup(inline_keyboard=rows)


def withdrawal_locked_keyboard() -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup(
        inline_keyboard=[
            [InlineKeyboardButton(text="⛏️ برداشت ماین", callback_data="menu:mine")],
            [InlineKeyboardButton(text=BACK_TO_MAIN, callback_data="menu:main")],
        ]
    )


def cancel_keyboard() -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup(
        inline_keyboard=[[InlineKeyboardButton(text="↩️ لغو و منوی اصلی", callback_data="cancel")]]
    )


def admin_panel_keyboard() -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup(
        inline_keyboard=[
            [
                InlineKeyboardButton(text="👥 کاربران", callback_data="admin:users"),
                InlineKeyboardButton(text="🔎 جستجو", callback_data="admin:search"),
            ],
            [
                InlineKeyboardButton(text="💳 پرداخت‌ها", callback_data="admin:payments"),
                InlineKeyboardButton(text="📤 برداشت‌ها", callback_data="admin:withdrawals"),
            ],
            [
                InlineKeyboardButton(text="💰 موجودی", callback_data="admin:balance"),
                InlineKeyboardButton(text="🎯 سقف ماین", callback_data="admin:cap"),
            ],
            [
                InlineKeyboardButton(text="⚡ سرعت", callback_data="admin:speed"),
                InlineKeyboardButton(text="🏷 آدرس TRON", callback_data="admin:wallet"),
            ],
            [InlineKeyboardButton(text="📊 آمار", callback_data="admin:stats")],
            [InlineKeyboardButton(text=BACK_TO_MAIN, callback_data="menu:main")],
        ]
    )


def admin_balance_mode_keyboard() -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup(
        inline_keyboard=[
            [
                InlineKeyboardButton(text="➕ افزایش", callback_data="admin_balance:increase"),
                InlineKeyboardButton(text="➖ کاهش", callback_data="admin_balance:decrease"),
            ],
            [InlineKeyboardButton(text=BACK_TO_ADMIN, callback_data="admin:home")],
        ]
    )


def admin_change_confirmation_keyboard(kind: str) -> InlineKeyboardMarkup:
    labels = {
        "balance": "✅ ثبت تغییر موجودی",
        "cap": "✅ ثبت سقف ماین",
        "speed": "✅ ثبت ضریب سرعت",
        "wallet": "✅ ذخیره آدرس",
    }
    return InlineKeyboardMarkup(
        inline_keyboard=[
            [
                InlineKeyboardButton(
                    text=labels[kind],
                    callback_data=f"admin_change:confirm:{kind}",
                )
            ],
            [InlineKeyboardButton(text="↩️ لغو", callback_data="cancel")],
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
    rows.append([InlineKeyboardButton(text=BACK_TO_ADMIN, callback_data="admin:home")])
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
                    text=f"❌ رد درخواست #{payment_id}",
                    callback_data=f"admin_payment:reject:{payment_id}",
                ),
            ]
        )
    navigation = _pagination_row("admin_payments", offset, total, page_size)
    if navigation:
        rows.append(navigation)
    rows.append([InlineKeyboardButton(text=BACK_TO_ADMIN, callback_data="admin:home")])
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
                    text=f"❌ رد درخواست #{withdrawal_id}",
                    callback_data=f"admin_withdrawal:reject:{withdrawal_id}",
                ),
            ]
        )
    navigation = _pagination_row("admin_withdrawals", offset, total, page_size)
    if navigation:
        rows.append(navigation)
    rows.append([InlineKeyboardButton(text=BACK_TO_ADMIN, callback_data="admin:home")])
    return InlineKeyboardMarkup(inline_keyboard=rows)


def payment_confirmation_keyboard(action: str, payment_id: int) -> InlineKeyboardMarkup:
    label = "✅ بله، تأیید پرداخت" if action == "approve" else "❌ بله، رد درخواست"
    return InlineKeyboardMarkup(
        inline_keyboard=[
            [
                InlineKeyboardButton(
                    text=label,
                    callback_data=f"admin_payment_confirm:{action}:{payment_id}",
                )
            ],
            [InlineKeyboardButton(text="↩️ بازگشت به پرداخت‌ها", callback_data="admin:payments")],
        ]
    )


def withdrawal_confirmation_keyboard(action: str, withdrawal_id: int) -> InlineKeyboardMarkup:
    label = "✅ بله، تأیید واریز" if action == "approve" else "❌ بله، رد درخواست"
    return InlineKeyboardMarkup(
        inline_keyboard=[
            [
                InlineKeyboardButton(
                    text=label,
                    callback_data=f"admin_withdrawal_confirm:{action}:{withdrawal_id}",
                )
            ],
            [InlineKeyboardButton(text="↩️ بازگشت به برداشت‌ها", callback_data="admin:withdrawals")],
        ]
    )
