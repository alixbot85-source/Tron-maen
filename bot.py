"""ربات تلگرام شبیه‌سازی ماینینگ TRON با aiogram 3 و SQLite."""

from __future__ import annotations

import asyncio
import logging
import re
import time
from decimal import Decimal, InvalidOperation, ROUND_HALF_UP, localcontext
from pathlib import Path
from typing import Optional

from aiogram import Bot, Dispatcher, F, Router
from aiogram.exceptions import TelegramAPIError, TelegramBadRequest
from aiogram.filters import Command, CommandStart, StateFilter
from aiogram.fsm.context import FSMContext
from aiogram.fsm.state import State, StatesGroup
from aiogram.fsm.storage.memory import MemoryStorage
from aiogram.types import CallbackQuery, Message, ReplyKeyboardRemove

from config import (
    ADMIN_IDS,
    BASE_MINING_RATE,
    BOT_TOKEN,
    BOT_USERNAME,
    DATABASE_PATH,
    DEFAULT_MINING_CAP,
    MINING_CAP_OPTIONS,
    MINING_COOLDOWN_SECONDS,
    REFERRAL_FIXED_REWARD,
    REFERRAL_MINING_PERCENTAGE,
    SPEED_PACKAGES,
    TRON_WALLET_ADDRESS,
)
from database import Database, Payment, User, Withdrawal
from keyboards import (
    account_keyboard,
    admin_balance_mode_keyboard,
    admin_change_confirmation_keyboard,
    admin_panel_keyboard,
    back_to_main_keyboard,
    balance_keyboard,
    cancel_keyboard,
    main_menu,
    mining_cap_keyboard,
    mining_result_keyboard,
    payment_confirmation_keyboard,
    payments_page_keyboard,
    speed_packages_keyboard,
    users_page_keyboard,
    withdrawal_confirmation_keyboard,
    withdrawal_locked_keyboard,
    withdrawals_page_keyboard,
)


logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s | %(levelname)s | %(name)s | %(message)s",
)
logger = logging.getLogger(__name__)

router = Router()
db = Database(DATABASE_PATH)
runtime_bot_username = BOT_USERNAME

# این اعداد فقط برای خوانایی صفحه‌ها و جلوگیری از ورودی بسیار بزرگ هستند؛
# در منطق مالی یا قیمت‌گذاری دخالتی ندارند.
USERS_PAGE_SIZE = 10
REQUESTS_PAGE_SIZE = 5
MAX_REASON_LENGTH = 300
MAX_NUMERIC_INPUT_LENGTH = 60
TXID_PATTERN = re.compile(r"^[A-Fa-f0-9]{64}$")
TRON_ADDRESS_PATTERN = re.compile(r"^T[1-9A-HJ-NP-Za-km-z]{33}$")
PACKAGE_PATTERN = re.compile(r"^(\d+(?:\.\d+)?)x$")

PERSIAN_DIGITS = "۰۱۲۳۴۵۶۷۸۹"
ARABIC_DIGITS = "٠١٢٣٤٥٦٧٨٩"
ASCII_DIGITS = "0123456789"
NUMBER_TRANSLATION = str.maketrans(
    PERSIAN_DIGITS + ARABIC_DIGITS + "٫٬",
    ASCII_DIGITS + ASCII_DIGITS + ".,",
)


class PaymentState(StatesGroup):
    waiting_txid = State()


class WithdrawalState(StatesGroup):
    waiting_address = State()


class AdminSearchState(StatesGroup):
    waiting_user_id = State()


class AdminBalanceState(StatesGroup):
    waiting_user_id = State()
    waiting_mode = State()
    waiting_amount = State()
    waiting_reason = State()
    waiting_confirmation = State()


class AdminCapState(StatesGroup):
    waiting_user_id = State()
    waiting_cap = State()
    waiting_confirmation = State()


class AdminSpeedState(StatesGroup):
    waiting_user_id = State()
    waiting_multiplier = State()
    waiting_confirmation = State()


class AdminWalletState(StatesGroup):
    waiting_address = State()
    waiting_confirmation = State()


def is_admin(user_id: Optional[int]) -> bool:
    return user_id is not None and user_id in ADMIN_IDS


def sender_id(message: Message | CallbackQuery) -> Optional[int]:
    user = message.from_user
    return user.id if user else None


def normalize_number_text(value: str) -> str:
    """اعداد فارسی/عربی و جداکنندهٔ هزارگان را به شکل قابل‌خواندن برای Decimal تبدیل می‌کند."""
    return value.strip().translate(NUMBER_TRANSLATION).replace(",", "")


def parse_positive_decimal(value: Optional[str]) -> Optional[Decimal]:
    if value is None:
        return None
    normalized = normalize_number_text(value)
    if not normalized or len(normalized) > MAX_NUMERIC_INPUT_LENGTH:
        return None
    if not re.fullmatch(r"\d+(?:\.\d+)?", normalized):
        return None
    try:
        number = Decimal(normalized)
    except (InvalidOperation, ValueError):
        return None
    if not number.is_finite() or number <= Decimal("0"):
        return None
    return number


def parse_user_id(value: Optional[str]) -> Optional[int]:
    if value is None:
        return None
    normalized = normalize_number_text(value)
    if not re.fullmatch(r"\d{1,19}", normalized):
        return None
    try:
        user_id = int(normalized)
    except ValueError:
        return None
    if not 0 < user_id <= 9_223_372_036_854_775_807:
        return None
    return user_id


def parse_callback_id(value: str) -> Optional[int]:
    if not re.fullmatch(r"\d{1,18}", value):
        return None
    record_id = int(value)
    return record_id if record_id > 0 else None


def format_number(value: Decimal | int | str) -> str:
    """عدد را با حداکثر چهار رقم اعشار برای متن‌های کاربری نمایش می‌دهد."""
    amount = Decimal(str(value))
    with localcontext() as context:
        context.prec = max(40, len(amount.as_tuple().digits) + 10)
        rounded = amount.quantize(Decimal("0.0001"), rounding=ROUND_HALF_UP)
    text = format(rounded, "f")
    if "." in text:
        text = text.rstrip("0").rstrip(".")
    return text or "0"


def format_tron(value: Decimal | int | str) -> str:
    return f"{format_number(value)} TRON"


def format_multiplier(value: Decimal | int | str) -> str:
    return f"×{format_number(value)}"


def format_remaining(seconds: int) -> str:
    minutes, remainder = divmod(max(0, seconds), 60)
    if minutes:
        return f"{minutes} دقیقه و {remainder} ثانیه"
    return f"{remainder} ثانیه"


def one_line(value: Optional[str], max_length: int = 45) -> str:
    cleaned = (value or "").replace("\n", " ").replace("\r", " ").strip()
    if len(cleaned) > max_length:
        return f"{cleaned[: max_length - 1]}…"
    return cleaned


def display_user_name(user_id: int, username: Optional[str], first_name: str) -> str:
    name = one_line(first_name) or "بدون نام"
    username_text = f" (@{one_line(username, 32)})" if username else ""
    return f"{name}{username_text} — {user_id}"


def valid_tron_address(address: str) -> bool:
    return bool(TRON_ADDRESS_PATTERN.fullmatch(address.strip()))


def extract_referrer(payload: str) -> Optional[int]:
    if not payload.startswith("REF_"):
        return None
    return parse_user_id(payload.removeprefix("REF_"))


def package_multiplier(package: str) -> Optional[Decimal]:
    match = PACKAGE_PATTERN.fullmatch(package)
    if not match:
        return None
    try:
        multiplier = Decimal(match.group(1))
    except InvalidOperation:
        return None
    if not multiplier.is_finite() or multiplier <= Decimal("0"):
        return None
    return multiplier


def get_wallet_address() -> str:
    """آدرس ذخیره‌شده توسط ادمین را با اولویت نسبت به config برمی‌گرداند."""
    saved_address = db.get_setting("tron_wallet_address")
    return (saved_address or TRON_WALLET_ADDRESS).strip()


PANEL_DIVIDER = "────────────"


def panel(title: str, lines: list[str]) -> str:
    """قالب کوتاه و یکپارچهٔ پیام‌های ربات."""
    return "\n".join([title, PANEL_DIVIDER, *lines])


def cooldown_remaining(user: User) -> int:
    elapsed = max(0, int(time.time()) - user.last_mining_time)
    return max(0, MINING_COOLDOWN_SECONDS - elapsed)


def main_menu_text(user: User) -> str:
    if user.total_mined >= user.mining_cap:
        status = "برداشت وجه: آماده"
    else:
        remaining = cooldown_remaining(user)
        status = "برداشت ماین: آماده" if remaining == 0 else f"برداشت بعدی: {format_remaining(remaining)}"
    return panel(
        "🔺 TRON",
        [
            "شبیه‌ساز ماینینگ TRON",
            f"نرخ پایه: {format_tron(BASE_MINING_RATE)} / دقیقه",
            f"سرعت: {format_multiplier(user.speed_multiplier)}",
            status,
        ],
    )


def account_text(user: User, withdrawal_pending: bool) -> str:
    if withdrawal_pending:
        mining_status = "وضعیت برداشت: در انتظار بررسی"
    elif user.total_mined >= user.mining_cap:
        mining_status = "وضعیت برداشت: آماده ثبت"
    else:
        remaining = cooldown_remaining(user)
        mining_status = "برداشت ماین: آماده" if remaining == 0 else f"برداشت بعدی: {format_remaining(remaining)}"
    return panel(
        "👤 حساب من",
        [
            f"شناسه تلگرام: {user.user_id}",
            f"موجودی: {format_tron(user.balance)}",
            f"مجموع ماین‌شده: {format_tron(user.total_mined)}",
            f"ضریب سرعت: {format_multiplier(user.speed_multiplier)}",
            f"سقف ماین: {format_tron(user.mining_cap)}",
            mining_status,
        ],
    )


def balance_text(user: User) -> str:
    return panel(
        "💰 موجودی",
        [
            f"موجودی قابل برداشت: {format_tron(user.balance)}",
            f"ماین این دوره: {format_tron(user.total_mined)} از {format_tron(user.mining_cap)}",
            f"سرعت فعلی: {format_multiplier(user.speed_multiplier)}",
        ],
    )


def help_text() -> str:
    return panel(
        "📊 راهنما",
        [
            "این ربات یک شبیه‌ساز ماینینگ TRON است.",
            "ماین را هر ۶۰ ثانیه دریافت کنید.",
            "پس از رسیدن به سقف، برداشت وجه را ثبت کنید.",
            "پرداخت سرعت و برداشت توسط ادمین بررسی می‌شود.",
        ],
    )


def user_details_text(user: User) -> str:
    username = f"@{one_line(user.username, 32)}" if user.username else "ندارد"
    referral = str(user.referrer_id) if user.referrer_id is not None else "ندارد"
    return panel(
        "👤 جزئیات کاربر",
        [
            f"شناسه: {user.user_id}",
            f"نام: {one_line(user.first_name) or 'ندارد'}",
            f"نام کاربری: {username}",
            f"موجودی: {format_tron(user.balance)}",
            f"ماین: {format_tron(user.total_mined)} از {format_tron(user.mining_cap)}",
            f"سرعت: {format_multiplier(user.speed_multiplier)}",
            f"معرف: {referral}",
        ],
    )


def admin_panel_text() -> str:
    return panel("🛠 پنل مدیریت", ["یک بخش را انتخاب کنید."])


def users_page_text(users: list[User], total: int, offset: int) -> str:
    if not users:
        return panel("👥 کاربران", ["کاربری برای نمایش وجود ندارد."])
    start = offset + 1
    end = offset + len(users)
    lines = [f"نمایش {start} تا {end} از {total}"]
    for index, user in enumerate(users):
        if index:
            lines.append("────────")
        lines.extend(
            [
                f"• {display_user_name(user.user_id, user.username, user.first_name)}",
                f"  {format_tron(user.balance)} | ماین: {format_tron(user.total_mined)}/{format_tron(user.mining_cap)}",
                f"  سرعت: {format_multiplier(user.speed_multiplier)}",
            ]
        )
    return panel("👥 کاربران", lines)


def payments_page_text(payments: list[Payment], total: int, offset: int) -> str:
    if not payments:
        return panel("💳 پرداخت‌های در انتظار", ["درخواستی برای بررسی نیست."])
    start = offset + 1
    end = offset + len(payments)
    lines = [f"نمایش {start} تا {end} از {total}"]
    for index, payment in enumerate(payments):
        if index:
            lines.append("────────")
        lines.extend(
            [
                f"#{payment.id} | کاربر: {payment.user_id}",
                f"پکیج: {payment.package} | مبلغ: {format_tron(payment.amount_tron)}",
                f"TXID: {payment.txid}",
                "وضعیت: در انتظار بررسی",
            ]
        )
    return panel("💳 پرداخت‌های در انتظار", lines)


def withdrawals_page_text(withdrawals: list[Withdrawal], total: int, offset: int) -> str:
    if not withdrawals:
        return panel("📤 برداشت‌های در انتظار", ["درخواستی برای بررسی نیست."])
    start = offset + 1
    end = offset + len(withdrawals)
    lines = [f"نمایش {start} تا {end} از {total}"]
    for index, withdrawal in enumerate(withdrawals):
        if index:
            lines.append("────────")
        lines.extend(
            [
                f"#{withdrawal.id} | کاربر: {withdrawal.user_id}",
                f"مبلغ: {format_tron(withdrawal.amount)}",
                f"آدرس: {withdrawal.tron_address}",
                "وضعیت: در انتظار بررسی",
            ]
        )
    return panel("📤 برداشت‌های در انتظار", lines)


def payment_confirmation_text(payment: Payment, action: str) -> str:
    action_text = "تأیید پرداخت" if action == "approve" else "رد درخواست"
    return panel(
        "⚠️ تأیید نهایی",
        [
            f"درخواست پرداخت: #{payment.id}",
            f"کاربر: {payment.user_id}",
            f"پکیج: {payment.package}",
            f"مبلغ: {format_tron(payment.amount_tron)}",
            f"TXID: {payment.txid}",
            f"اقدام: {action_text}",
            "پس از تأیید، این عمل اجرا می‌شود.",
        ],
    )


def withdrawal_confirmation_text(withdrawal: Withdrawal, action: str) -> str:
    action_text = "تأیید واریز" if action == "approve" else "رد درخواست"
    return panel(
        "⚠️ تأیید نهایی",
        [
            f"درخواست برداشت: #{withdrawal.id}",
            f"کاربر: {withdrawal.user_id}",
            f"مبلغ: {format_tron(withdrawal.amount)}",
            f"آدرس: {withdrawal.tron_address}",
            f"اقدام: {action_text}",
            "پس از تأیید، این عمل اجرا می‌شود.",
        ],
    )


async def edit_callback_message(
    callback: CallbackQuery,
    text: str,
    reply_markup: object | None = None,
) -> None:
    """در صورت منقضی‌بودن پیام، پاسخ را به‌صورت پیام تازه می‌فرستد."""
    if not isinstance(callback.message, Message):
        return
    try:
        await callback.message.edit_text(text, reply_markup=reply_markup)
    except TelegramBadRequest:
        await callback.message.answer(text, reply_markup=reply_markup)


async def notify_user(bot: Bot, user_id: int, text: str) -> None:
    try:
        await bot.send_message(user_id, text)
    except TelegramAPIError:
        # ممکن است کاربر ربات را مسدود کرده باشد؛ عملیات ادمین نباید به‌خاطر آن برگردد.
        logger.info("ارسال اعلان به کاربر %s ممکن نشد.", user_id)


async def ensure_registered(message: Message) -> Optional[User]:
    user_id = sender_id(message)
    if user_id is None:
        return None
    user = db.get_user(user_id)
    if not user:
        await message.answer("ابتدا با دستور /start وارد ربات شوید.")
    return user


async def ensure_registered_callback(callback: CallbackQuery) -> Optional[User]:
    user_id = sender_id(callback)
    if user_id is None:
        await callback.answer("کاربر نامعتبر است.", show_alert=True)
        return None
    user = db.get_user(user_id)
    if not user:
        await callback.answer("ابتدا با /start وارد ربات شوید.", show_alert=True)
    return user


async def show_main_menu_callback(callback: CallbackQuery, user: User) -> None:
    await edit_callback_message(
        callback,
        main_menu_text(user),
        main_menu(is_admin(user.user_id)),
    )


async def ensure_admin_callback(callback: CallbackQuery) -> bool:
    if is_admin(sender_id(callback)):
        return True
    await callback.answer("شما به پنل مدیریت دسترسی ندارید.", show_alert=True)
    return False


async def ensure_admin_message(message: Message, state: FSMContext) -> bool:
    if is_admin(sender_id(message)):
        return True
    await state.clear()
    await message.answer("شما به پنل مدیریت دسترسی ندارید.")
    return False


async def show_users_page(callback: CallbackQuery, offset: int) -> None:
    users, total = db.get_users_page(USERS_PAGE_SIZE, offset)
    # اگر آخرین صفحه بعد از تغییر داده خالی شد، نزدیک‌ترین صفحهٔ موجود نمایش داده شود.
    if not users and total and offset > 0:
        offset = max(0, ((total - 1) // USERS_PAGE_SIZE) * USERS_PAGE_SIZE)
        users, total = db.get_users_page(USERS_PAGE_SIZE, offset)
    await edit_callback_message(
        callback,
        users_page_text(users, total, offset),
        users_page_keyboard(offset, total, USERS_PAGE_SIZE),
    )


async def show_payments_page(callback: CallbackQuery, offset: int) -> None:
    payments, total = db.get_pending_payments_page(REQUESTS_PAGE_SIZE, offset)
    if not payments and total and offset > 0:
        offset = max(0, ((total - 1) // REQUESTS_PAGE_SIZE) * REQUESTS_PAGE_SIZE)
        payments, total = db.get_pending_payments_page(REQUESTS_PAGE_SIZE, offset)
    await edit_callback_message(
        callback,
        payments_page_text(payments, total, offset),
        payments_page_keyboard(payments, offset, total, REQUESTS_PAGE_SIZE),
    )


async def show_withdrawals_page(callback: CallbackQuery, offset: int) -> None:
    withdrawals, total = db.get_pending_withdrawals_page(REQUESTS_PAGE_SIZE, offset)
    if not withdrawals and total and offset > 0:
        offset = max(0, ((total - 1) // REQUESTS_PAGE_SIZE) * REQUESTS_PAGE_SIZE)
        withdrawals, total = db.get_pending_withdrawals_page(REQUESTS_PAGE_SIZE, offset)
    await edit_callback_message(
        callback,
        withdrawals_page_text(withdrawals, total, offset),
        withdrawals_page_keyboard(withdrawals, offset, total, REQUESTS_PAGE_SIZE),
    )


@router.message(CommandStart())
async def start_handler(message: Message, state: FSMContext) -> None:
    if not message.from_user or message.from_user.is_bot:
        return
    await state.clear()
    parts = (message.text or "").split(maxsplit=1)
    payload = parts[1].strip() if len(parts) > 1 else ""
    result = db.register_user(
        user_id=message.from_user.id,
        username=message.from_user.username,
        first_name=message.from_user.first_name,
        referrer_candidate=extract_referrer(payload),
        fixed_referral_reward=REFERRAL_FIXED_REWARD,
        default_mining_cap=DEFAULT_MINING_CAP,
    )

    welcome_lines = ["حساب شما آماده است."]
    if result.created and result.referrer_id is not None:
        welcome_lines.append("ثبت‌نام با لینک دعوت انجام شد.")
    await message.answer(panel("🔺 TRON", welcome_lines), reply_markup=ReplyKeyboardRemove())
    await message.answer(
        main_menu_text(result.user),
        reply_markup=main_menu(is_admin(message.from_user.id)),
    )


@router.message(Command("cancel"))
async def cancel_command_handler(message: Message, state: FSMContext) -> None:
    await state.clear()
    user_id = sender_id(message)
    user = db.get_user(user_id) if user_id is not None else None
    if user:
        await message.answer(
            panel("🔺 TRON", ["عملیات لغو شد.", "از منوی زیر انتخاب کنید."]),
            reply_markup=main_menu(is_admin(user.user_id)),
        )
    elif is_admin(user_id):
        await message.answer(admin_panel_text(), reply_markup=admin_panel_keyboard())
    else:
        await message.answer("ابتدا با /start وارد ربات شوید.")


@router.callback_query(F.data == "cancel")
async def cancel_callback_handler(callback: CallbackQuery, state: FSMContext) -> None:
    await state.clear()
    user_id = sender_id(callback)
    user = db.get_user(user_id) if user_id is not None else None
    await callback.answer("عملیات لغو شد.")
    if user:
        await edit_callback_message(
            callback,
            panel("🔺 TRON", ["عملیات لغو شد.", "از منوی زیر انتخاب کنید."]),
            main_menu(is_admin(user.user_id)),
        )
    elif is_admin(user_id):
        await edit_callback_message(callback, admin_panel_text(), admin_panel_keyboard())


@router.callback_query(F.data == "menu:main")
async def main_menu_callback_handler(callback: CallbackQuery, state: FSMContext) -> None:
    await state.clear()
    user_id = sender_id(callback)
    user = db.get_user(user_id) if user_id is not None else None
    if user:
        await callback.answer()
        await show_main_menu_callback(callback, user)
    elif is_admin(user_id):
        await callback.answer()
        await edit_callback_message(callback, admin_panel_text(), admin_panel_keyboard())
    else:
        await callback.answer("ابتدا با /start وارد ربات شوید.", show_alert=True)


@router.message(Command("help"))
async def help_command_handler(message: Message) -> None:
    await message.answer(help_text(), reply_markup=back_to_main_keyboard())


@router.callback_query(StateFilter(None), F.data == "menu:help")
async def help_callback_handler(callback: CallbackQuery) -> None:
    user = await ensure_registered_callback(callback)
    if not user:
        return
    await callback.answer()
    await edit_callback_message(callback, help_text(), back_to_main_keyboard())


async def show_admin_panel_message(message: Message, state: FSMContext) -> None:
    if not await ensure_admin_message(message, state):
        return
    await state.clear()
    await message.answer(admin_panel_text(), reply_markup=admin_panel_keyboard())


@router.message(Command("admin"))
async def admin_command_handler(message: Message, state: FSMContext) -> None:
    await show_admin_panel_message(message, state)


@router.callback_query(StateFilter(None), F.data == "menu:balance")
async def balance_callback_handler(callback: CallbackQuery) -> None:
    user = await ensure_registered_callback(callback)
    if not user:
        return
    await callback.answer()
    await edit_callback_message(callback, balance_text(user), balance_keyboard())


@router.callback_query(StateFilter(None), F.data == "menu:account")
async def account_callback_handler(callback: CallbackQuery) -> None:
    user = await ensure_registered_callback(callback)
    if not user:
        return
    withdrawal_pending = db.has_pending_withdrawal(user.user_id)
    can_withdraw = user.total_mined >= user.mining_cap and user.balance > Decimal("0") and not withdrawal_pending
    await callback.answer()
    await edit_callback_message(
        callback,
        account_text(user, withdrawal_pending),
        account_keyboard(can_withdraw),
    )


@router.callback_query(StateFilter(None), F.data == "menu:mine")
async def mine_callback_handler(callback: CallbackQuery) -> None:
    user = await ensure_registered_callback(callback)
    if not user:
        return
    result = db.claim_mining(
        user_id=user.user_id,
        base_rate_per_minute=BASE_MINING_RATE,
        cooldown_seconds=MINING_COOLDOWN_SECONDS,
        referral_percentage=REFERRAL_MINING_PERCENTAGE,
    )
    if result.status == "claimed":
        remaining_to_cap = max(Decimal("0"), result.mining_cap - result.total_mined)
        lines = [
            f"این نوبت: {format_tron(result.amount)}",
            f"موجودی جدید: {format_tron(result.balance)}",
        ]
        if user.referrer_id is not None:
            lines.append(f"پاداش معرف: {format_tron(result.referral_bonus)}")
        if result.total_mined >= result.mining_cap:
            lines.append("وضعیت برداشت وجه: آماده")
        else:
            lines.append(f"مانده تا سقف: {format_tron(remaining_to_cap)}")
        text = panel("⛏️ برداشت ماین", lines)
        can_withdraw = result.total_mined >= result.mining_cap
    elif result.status == "too_soon":
        remaining_to_cap = max(Decimal("0"), result.mining_cap - result.total_mined)
        text = panel(
            "⏳ برداشت ماین",
            [
                f"زمان باقی‌مانده: {format_remaining(result.remaining_seconds)}",
                f"مانده تا سقف: {format_tron(remaining_to_cap)}",
            ],
        )
        can_withdraw = False
    elif result.status == "cap_reached":
        text = panel(
            "⛏️ برداشت ماین",
            [
                "سقف ماین کامل است.",
                f"موجودی: {format_tron(result.balance)}",
                "برداشت وجه را ثبت کنید.",
            ],
        )
        can_withdraw = result.balance > Decimal("0")
    elif result.status == "withdrawal_pending":
        text = panel(
            "📤 برداشت وجه",
            [
                "وضعیت درخواست: در انتظار بررسی ادمین",
                "تا پایان بررسی، برداشت ماین متوقف است.",
            ],
        )
        can_withdraw = False
    else:
        await callback.answer("حساب شما پیدا نشد.", show_alert=True)
        return

    await callback.answer()
    await edit_callback_message(callback, text, mining_result_keyboard(can_withdraw))


@router.callback_query(StateFilter(None), F.data == "menu:cap")
async def mining_cap_menu_callback_handler(callback: CallbackQuery) -> None:
    user = await ensure_registered_callback(callback)
    if not user:
        return
    await callback.answer()
    await edit_callback_message(
        callback,
        panel("🎯 سقف ماین", [f"سقف فعلی: {format_tron(user.mining_cap)}", "یک گزینه را انتخاب کنید."]),
        mining_cap_keyboard(user.mining_cap),
    )


@router.callback_query(StateFilter(None), F.data.startswith("cap:"))
async def mining_cap_callback_handler(callback: CallbackQuery) -> None:
    user_id = sender_id(callback)
    if user_id is None:
        await callback.answer("کاربر نامعتبر است.", show_alert=True)
        return
    raw_cap = (callback.data or "").split(":", maxsplit=1)[1]
    try:
        cap = int(raw_cap)
    except ValueError:
        await callback.answer("سقف انتخاب‌شده معتبر نیست.", show_alert=True)
        return
    if cap not in MINING_CAP_OPTIONS:
        await callback.answer("این سقف در دسترس نیست.", show_alert=True)
        return
    user = db.set_user_mining_cap(user_id, Decimal(str(cap)))
    if not user:
        await callback.answer("ابتدا با /start وارد ربات شوید.", show_alert=True)
        return
    await callback.answer("سقف ماین به‌روزرسانی شد.")
    await edit_callback_message(
        callback,
        panel("🎯 سقف ماین", [f"سقف جدید: {format_tron(user.mining_cap)}", "تغییر ذخیره شد."]),
        mining_cap_keyboard(user.mining_cap),
    )


@router.callback_query(StateFilter(None), F.data == "menu:referral")
async def referral_callback_handler(callback: CallbackQuery, bot: Bot) -> None:
    user = await ensure_registered_callback(callback)
    if not user:
        return
    global runtime_bot_username
    if not runtime_bot_username:
        try:
            me = await bot.get_me()
            runtime_bot_username = me.username or ""
        except TelegramAPIError:
            runtime_bot_username = ""
    if not runtime_bot_username:
        await callback.answer()
        await edit_callback_message(
            callback,
            panel("👥 دعوت دوستان", ["لینک دعوت فعلاً در دسترس نیست."]),
            back_to_main_keyboard(),
        )
        return

    invite_link = f"https://t.me/{runtime_bot_username}?start=REF_{user.user_id}"
    referral_percent = format_number(REFERRAL_MINING_PERCENTAGE * Decimal("100"))
    successful_referrals = db.count_referrals(user.user_id)
    await callback.answer()
    await edit_callback_message(
        callback,
        panel(
            "👥 دعوت دوستان",
            [
                "لینک اختصاصی:",
                invite_link,
                f"دعوت موفق: {successful_referrals}",
                f"پاداش ثبت‌نام: {format_tron(REFERRAL_FIXED_REWARD)}",
                f"پاداش ماین: {referral_percent}٪",
            ],
        ),
        back_to_main_keyboard(),
    )


@router.callback_query(StateFilter(None), F.data == "menu:speed")
async def speed_menu_callback_handler(callback: CallbackQuery) -> None:
    user = await ensure_registered_callback(callback)
    if not user:
        return
    wallet_address = get_wallet_address()
    if not valid_tron_address(wallet_address):
        await callback.answer()
        await edit_callback_message(
            callback,
            panel("⚡ ارتقای سرعت", ["پرداخت فعلاً در دسترس نیست.", "آدرس TRON هنوز تنظیم نشده است."]),
            back_to_main_keyboard(),
        )
        return
    package_lines = [
        f"سرعت {format_multiplier(multiplier)} — {format_tron(price)}"
        for multiplier, price in sorted(SPEED_PACKAGES.items())
    ]
    await callback.answer()
    await edit_callback_message(
        callback,
        panel(
            "⚡ ارتقای سرعت",
            [f"سرعت فعلی: {format_multiplier(user.speed_multiplier)}", *package_lines, "یک پکیج انتخاب کنید."],
        ),
        speed_packages_keyboard(),
    )


@router.callback_query(StateFilter(None), F.data.startswith("speed:"))
async def speed_package_callback_handler(callback: CallbackQuery, state: FSMContext) -> None:
    user = await ensure_registered_callback(callback)
    if not user:
        return
    raw_multiplier = (callback.data or "").split(":", maxsplit=1)[1]
    try:
        multiplier = int(raw_multiplier)
    except ValueError:
        await callback.answer("پکیج انتخاب‌شده معتبر نیست.", show_alert=True)
        return
    price = SPEED_PACKAGES.get(multiplier)
    if price is None:
        await callback.answer("این پکیج در دسترس نیست.", show_alert=True)
        return
    wallet_address = get_wallet_address()
    if not valid_tron_address(wallet_address):
        await callback.answer("آدرس پرداخت تنظیم نشده است.", show_alert=True)
        return

    await state.set_state(PaymentState.waiting_txid)
    await state.update_data(package=f"{multiplier}x", amount=str(price))
    await callback.answer()
    await edit_callback_message(
        callback,
        panel(
            "💳 پرداخت سرعت",
            [
                f"پکیج: سرعت {format_multiplier(multiplier)}",
                f"مبلغ دقیق: {format_tron(price)}",
                "آدرس TRON:",
                wallet_address,
                "اکنون TXID را ارسال کنید.",
            ],
        ),
        cancel_keyboard(),
    )


@router.message(PaymentState.waiting_txid)
async def payment_txid_handler(message: Message, state: FSMContext) -> None:
    user_id = sender_id(message)
    if user_id is None:
        return
    txid = (message.text or "").strip()
    if not TXID_PATTERN.fullmatch(txid):
        await message.answer(
            panel("💳 پرداخت سرعت", ["TXID معتبر نیست.", "هش تراکنش ۶۴ کاراکتری را دوباره ارسال کنید."]),
            reply_markup=cancel_keyboard(),
        )
        return
    data = await state.get_data()
    package = str(data.get("package", ""))
    amount = parse_positive_decimal(str(data.get("amount", "")))
    multiplier = package_multiplier(package)
    if amount is None or multiplier is None:
        await state.clear()
        user = db.get_user(user_id)
        if user:
            await message.answer(
                panel("⚡ ارتقای سرعت", ["اطلاعات پکیج پیدا نشد.", "دوباره از منوی اصلی شروع کنید."]),
                reply_markup=main_menu(is_admin(user.user_id)),
            )
        else:
            await message.answer("حساب شما پیدا نشد. لطفاً دوباره /start را بفرستید.")
        return

    result = db.create_payment(
        user_id=user_id,
        package=package,
        amount_tron=amount,
        txid=txid.upper(),
    )
    if result.status == "duplicate_txid":
        await message.answer(
            panel("💳 پرداخت سرعت", ["این TXID قبلاً ثبت شده است.", "هش تراکنش دیگری را ارسال کنید."]),
            reply_markup=cancel_keyboard(),
        )
        return
    await state.clear()
    user = db.get_user(user_id)
    if result.status == "created" and result.payment and user:
        await message.answer(
            panel(
                "💳 درخواست پرداخت",
                [
                    f"شناسه درخواست: #{result.payment.id}",
                    f"پکیج: سرعت {format_multiplier(multiplier)}",
                    f"مبلغ: {format_tron(amount)}",
                    "وضعیت: در انتظار بررسی ادمین",
                ],
            ),
            reply_markup=main_menu(is_admin(user.user_id)),
        )
    else:
        await message.answer("حساب شما پیدا نشد. لطفاً دوباره /start را بفرستید.")


@router.callback_query(StateFilter(None), F.data == "menu:withdraw")
async def withdrawal_start_callback_handler(callback: CallbackQuery, state: FSMContext) -> None:
    user = await ensure_registered_callback(callback)
    if not user:
        return
    if user.total_mined < user.mining_cap:
        remaining = user.mining_cap - user.total_mined
        await callback.answer()
        await edit_callback_message(
            callback,
            panel(
                "📤 برداشت وجه",
                ["وضعیت: هنوز فعال نیست", f"مانده تا سقف: {format_tron(remaining)}"],
            ),
            withdrawal_locked_keyboard(),
        )
        return
    if user.balance <= Decimal("0"):
        await callback.answer()
        await edit_callback_message(
            callback,
            panel("📤 برداشت وجه", ["موجودی قابل برداشت ندارید."]),
            back_to_main_keyboard(),
        )
        return
    if db.has_pending_withdrawal(user.user_id):
        await callback.answer()
        await edit_callback_message(
            callback,
            panel("📤 برداشت وجه", ["وضعیت: در انتظار بررسی ادمین"]),
            back_to_main_keyboard(),
        )
        return
    await state.set_state(WithdrawalState.waiting_address)
    await callback.answer()
    await edit_callback_message(
        callback,
        panel(
            "📤 برداشت وجه",
            [
                "وضعیت: آماده ثبت",
                f"مبلغ درخواست: {format_tron(user.balance)}",
                "آدرس TRON خود را ارسال کنید.",
            ],
        ),
        cancel_keyboard(),
    )


@router.message(WithdrawalState.waiting_address)
async def withdrawal_address_handler(message: Message, state: FSMContext) -> None:
    user_id = sender_id(message)
    if user_id is None:
        return
    tron_address = (message.text or "").strip()
    if not valid_tron_address(tron_address):
        await message.answer(
            panel("📤 برداشت وجه", ["آدرس TRON معتبر نیست.", "آدرس ۳۴ کاراکتری را دوباره ارسال کنید."]),
            reply_markup=cancel_keyboard(),
        )
        return

    result = db.create_withdrawal(user_id, tron_address)
    if result.status == "created" and result.withdrawal:
        await state.clear()
        user = db.get_user(user_id)
        if user:
            await message.answer(
                panel(
                    "📤 درخواست برداشت",
                    [
                        f"شناسه درخواست: #{result.withdrawal.id}",
                        f"مبلغ: {format_tron(result.withdrawal.amount)}",
                        "وضعیت: در انتظار بررسی ادمین",
                    ],
                ),
                reply_markup=main_menu(is_admin(user.user_id)),
            )
        return

    await state.clear()
    messages = {
        "not_eligible": ["وضعیت: هنوز فعال نیست", "ابتدا به سقف ماین برسید."],
        "empty_balance": ["موجودی قابل برداشت ندارید."],
        "already_pending": ["وضعیت: در انتظار بررسی ادمین"],
        "user_not_found": ["حساب شما پیدا نشد.", "دوباره /start را بفرستید."],
    }
    user = db.get_user(user_id)
    await message.answer(
        panel("📤 برداشت وجه", messages.get(result.status, ["ثبت درخواست ممکن نشد."])),
        reply_markup=main_menu(is_admin(user_id)) if user else None,
    )

@router.callback_query(F.data == "admin:home")
async def admin_home_callback_handler(callback: CallbackQuery, state: FSMContext) -> None:
    if not await ensure_admin_callback(callback):
        return
    await state.clear()
    await callback.answer()
    await edit_callback_message(callback, admin_panel_text(), admin_panel_keyboard())


@router.callback_query(F.data == "admin:users")
async def admin_users_callback_handler(callback: CallbackQuery, state: FSMContext) -> None:
    if not await ensure_admin_callback(callback):
        return
    await state.clear()
    await callback.answer()
    await show_users_page(callback, 0)


@router.callback_query(F.data.startswith("admin_users:"))
async def admin_users_page_callback_handler(callback: CallbackQuery, state: FSMContext) -> None:
    if not await ensure_admin_callback(callback):
        return
    await state.clear()
    raw_offset = (callback.data or "").split(":", maxsplit=1)[1]
    if not re.fullmatch(r"\d{1,9}", raw_offset):
        await callback.answer("شماره صفحه معتبر نیست.", show_alert=True)
        return
    await callback.answer()
    await show_users_page(callback, int(raw_offset))


@router.callback_query(F.data == "admin:search")
async def admin_search_callback_handler(callback: CallbackQuery, state: FSMContext) -> None:
    if not await ensure_admin_callback(callback):
        return
    await state.clear()
    await state.set_state(AdminSearchState.waiting_user_id)
    await callback.answer()
    if isinstance(callback.message, Message):
        await callback.message.answer(
            panel("🔎 جست‌وجوی کاربر", ["شناسه عددی کاربر را ارسال کنید."]),
            reply_markup=cancel_keyboard(),
        )


@router.message(AdminSearchState.waiting_user_id)
async def admin_search_user_handler(message: Message, state: FSMContext) -> None:
    if not await ensure_admin_message(message, state):
        return
    target_user_id = parse_user_id(message.text)
    if target_user_id is None:
        await message.answer("شناسه کاربر باید یک عدد معتبر باشد.", reply_markup=cancel_keyboard())
        return
    user = db.get_user(target_user_id)
    if not user:
        await message.answer("کاربری با این شناسه پیدا نشد. شناسه دیگری بفرستید یا /cancel را بزنید.")
        return
    await state.clear()
    await message.answer(user_details_text(user), reply_markup=admin_panel_keyboard())


@router.callback_query(F.data == "admin:balance")
async def admin_balance_callback_handler(callback: CallbackQuery, state: FSMContext) -> None:
    if not await ensure_admin_callback(callback):
        return
    await state.clear()
    await state.set_state(AdminBalanceState.waiting_user_id)
    await callback.answer()
    if isinstance(callback.message, Message):
        await callback.message.answer(
            panel("💰 تغییر موجودی", ["شناسه عددی کاربر را ارسال کنید."]),
            reply_markup=cancel_keyboard(),
        )


@router.message(AdminBalanceState.waiting_user_id)
async def admin_balance_user_handler(message: Message, state: FSMContext) -> None:
    if not await ensure_admin_message(message, state):
        return
    target_user_id = parse_user_id(message.text)
    if target_user_id is None:
        await message.answer("شناسه کاربر باید یک عدد معتبر باشد.", reply_markup=cancel_keyboard())
        return
    if not db.get_user(target_user_id):
        await message.answer("کاربری با این شناسه پیدا نشد. شناسه دیگری بفرستید یا /cancel را بزنید.")
        return
    await state.update_data(target_user_id=target_user_id)
    await state.set_state(AdminBalanceState.waiting_mode)
    await message.answer(
        panel("💰 تغییر موجودی", [f"کاربر: {target_user_id}", "نوع تغییر را انتخاب کنید."]),
        reply_markup=admin_balance_mode_keyboard(),
    )


@router.callback_query(AdminBalanceState.waiting_mode, F.data.startswith("admin_balance:"))
async def admin_balance_mode_handler(callback: CallbackQuery, state: FSMContext) -> None:
    if not await ensure_admin_callback(callback):
        return
    mode = (callback.data or "").split(":", maxsplit=1)[1]
    if mode not in {"increase", "decrease"}:
        await callback.answer("نوع تغییر معتبر نیست.", show_alert=True)
        return
    await state.update_data(balance_mode=mode)
    await state.set_state(AdminBalanceState.waiting_amount)
    await callback.answer()
    if isinstance(callback.message, Message):
        direction = "افزایش" if mode == "increase" else "کاهش"
        await callback.message.answer(
            panel("💰 تغییر موجودی", [f"عملیات: {direction}", "مقدار TRON را ارسال کنید."]),
            reply_markup=cancel_keyboard(),
        )


@router.message(AdminBalanceState.waiting_amount)
async def admin_balance_amount_handler(message: Message, state: FSMContext) -> None:
    if not await ensure_admin_message(message, state):
        return
    amount = parse_positive_decimal(message.text)
    if amount is None:
        await message.answer("مقدار باید یک عدد مثبت و معتبر باشد.", reply_markup=cancel_keyboard())
        return
    await state.update_data(balance_amount=str(amount))
    await state.set_state(AdminBalanceState.waiting_reason)
    await message.answer(
        panel("💰 تغییر موجودی", ["دلیل تغییر را بنویسید."]),
        reply_markup=cancel_keyboard(),
    )


@router.message(AdminBalanceState.waiting_reason)
async def admin_balance_reason_handler(message: Message, state: FSMContext) -> None:
    if not await ensure_admin_message(message, state):
        return
    reason = (message.text or "").strip()
    if not reason or len(reason) > MAX_REASON_LENGTH:
        await message.answer(
            panel("💰 تغییر موجودی", [f"دلیل باید تا {MAX_REASON_LENGTH} کاراکتر باشد."]),
            reply_markup=cancel_keyboard(),
        )
        return
    data = await state.get_data()
    target_user_id = data.get("target_user_id")
    mode = data.get("balance_mode")
    amount = parse_positive_decimal(str(data.get("balance_amount", "")))
    if not isinstance(target_user_id, int) or mode not in {"increase", "decrease"} or amount is None:
        await state.clear()
        await message.answer(panel("💰 تغییر موجودی", ["اطلاعات عملیات ناقص است.", "دوباره از پنل مدیریت شروع کنید."]))
        return
    await state.update_data(balance_reason=reason)
    await state.set_state(AdminBalanceState.waiting_confirmation)
    direction = "افزایش" if mode == "increase" else "کاهش"
    await message.answer(
        panel(
            "⚠️ تأیید نهایی",
            [
                f"کاربر: {target_user_id}",
                f"عملیات: {direction}",
                f"مبلغ: {format_tron(amount)}",
                f"دلیل: {one_line(reason, 80)}",
            ],
        ),
        reply_markup=admin_change_confirmation_keyboard("balance"),
    )


@router.callback_query(AdminBalanceState.waiting_confirmation, F.data == "admin_change:confirm:balance")
async def admin_balance_confirmation_handler(callback: CallbackQuery, state: FSMContext) -> None:
    if not await ensure_admin_callback(callback):
        return
    data = await state.get_data()
    target_user_id = data.get("target_user_id")
    mode = data.get("balance_mode")
    amount = parse_positive_decimal(str(data.get("balance_amount", "")))
    reason = str(data.get("balance_reason", "")).strip()
    if not isinstance(target_user_id, int) or mode not in {"increase", "decrease"} or amount is None or not reason:
        await state.clear()
        await callback.answer("اطلاعات عملیات ناقص است.", show_alert=True)
        await edit_callback_message(callback, admin_panel_text(), admin_panel_keyboard())
        return

    change = amount if mode == "increase" else -amount
    result = db.adjust_balance(
        admin_id=sender_id(callback) or 0,
        target_user_id=target_user_id,
        change=change,
        reason=reason,
    )
    await state.clear()
    if result.status == "updated":
        action = "افزایش" if change > Decimal("0") else "کاهش"
        text = panel(
            "💰 تغییر موجودی",
            [
                f"عملیات: {action}",
                f"مبلغ: {format_tron(abs(change))}",
                f"موجودی جدید: {format_tron(result.balance)}",
            ],
        )
        await callback.answer("تغییر موجودی ثبت شد.")
    elif result.status == "insufficient_balance":
        text = panel("💰 تغییر موجودی", [f"عملیات انجام نشد.", f"موجودی فعلی: {format_tron(result.balance)}"])
        await callback.answer("موجودی کافی نیست.", show_alert=True)
    else:
        text = panel("💰 تغییر موجودی", ["کاربر پیدا نشد."])
        await callback.answer("کاربر پیدا نشد.", show_alert=True)
    await edit_callback_message(callback, text, admin_panel_keyboard())

@router.callback_query(F.data == "admin:cap")
async def admin_cap_callback_handler(callback: CallbackQuery, state: FSMContext) -> None:
    if not await ensure_admin_callback(callback):
        return
    await state.clear()
    await state.set_state(AdminCapState.waiting_user_id)
    await callback.answer()
    if isinstance(callback.message, Message):
        await callback.message.answer(
            panel("🎯 تغییر سقف ماین", ["شناسه عددی کاربر را ارسال کنید."]),
            reply_markup=cancel_keyboard(),
        )


@router.message(AdminCapState.waiting_user_id)
async def admin_cap_user_handler(message: Message, state: FSMContext) -> None:
    if not await ensure_admin_message(message, state):
        return
    target_user_id = parse_user_id(message.text)
    if target_user_id is None:
        await message.answer("شناسه کاربر باید یک عدد معتبر باشد.", reply_markup=cancel_keyboard())
        return
    if not db.get_user(target_user_id):
        await message.answer("کاربری با این شناسه پیدا نشد. شناسه دیگری بفرستید یا /cancel را بزنید.")
        return
    await state.update_data(target_user_id=target_user_id)
    await state.set_state(AdminCapState.waiting_cap)
    await message.answer(
        panel("🎯 تغییر سقف ماین", [f"کاربر: {target_user_id}", "سقف جدید را به TRON ارسال کنید."]),
        reply_markup=cancel_keyboard(),
    )


@router.message(AdminCapState.waiting_cap)
async def admin_cap_value_handler(message: Message, state: FSMContext) -> None:
    if not await ensure_admin_message(message, state):
        return
    mining_cap = parse_positive_decimal(message.text)
    if mining_cap is None:
        await message.answer("سقف ماین باید یک عدد مثبت و معتبر باشد.", reply_markup=cancel_keyboard())
        return
    data = await state.get_data()
    target_user_id = data.get("target_user_id")
    if not isinstance(target_user_id, int):
        await state.clear()
        await message.answer(panel("🎯 تغییر سقف ماین", ["اطلاعات عملیات ناقص است.", "دوباره شروع کنید."]))
        return
    await state.update_data(new_mining_cap=str(mining_cap))
    await state.set_state(AdminCapState.waiting_confirmation)
    await message.answer(
        panel(
            "⚠️ تأیید نهایی",
            [f"کاربر: {target_user_id}", f"سقف جدید: {format_tron(mining_cap)}"],
        ),
        reply_markup=admin_change_confirmation_keyboard("cap"),
    )


@router.callback_query(AdminCapState.waiting_confirmation, F.data == "admin_change:confirm:cap")
async def admin_cap_confirmation_handler(callback: CallbackQuery, state: FSMContext) -> None:
    if not await ensure_admin_callback(callback):
        return
    data = await state.get_data()
    target_user_id = data.get("target_user_id")
    mining_cap = parse_positive_decimal(str(data.get("new_mining_cap", "")))
    if not isinstance(target_user_id, int) or mining_cap is None:
        await state.clear()
        await callback.answer("اطلاعات عملیات ناقص است.", show_alert=True)
        await edit_callback_message(callback, admin_panel_text(), admin_panel_keyboard())
        return
    user = db.admin_set_mining_cap(sender_id(callback) or 0, target_user_id, mining_cap)
    await state.clear()
    if user:
        text = panel("🎯 تغییر سقف ماین", [f"کاربر: {target_user_id}", f"سقف جدید: {format_tron(user.mining_cap)}"])
        await callback.answer("سقف ماین ثبت شد.")
    else:
        text = panel("🎯 تغییر سقف ماین", ["کاربر پیدا نشد."])
        await callback.answer("کاربر پیدا نشد.", show_alert=True)
    await edit_callback_message(callback, text, admin_panel_keyboard())

@router.callback_query(F.data == "admin:speed")
async def admin_speed_callback_handler(callback: CallbackQuery, state: FSMContext) -> None:
    if not await ensure_admin_callback(callback):
        return
    await state.clear()
    await state.set_state(AdminSpeedState.waiting_user_id)
    await callback.answer()
    if isinstance(callback.message, Message):
        await callback.message.answer(
            panel("⚡ تغییر سرعت", ["شناسه عددی کاربر را ارسال کنید."]),
            reply_markup=cancel_keyboard(),
        )


@router.message(AdminSpeedState.waiting_user_id)
async def admin_speed_user_handler(message: Message, state: FSMContext) -> None:
    if not await ensure_admin_message(message, state):
        return
    target_user_id = parse_user_id(message.text)
    if target_user_id is None:
        await message.answer("شناسه کاربر باید یک عدد معتبر باشد.", reply_markup=cancel_keyboard())
        return
    if not db.get_user(target_user_id):
        await message.answer("کاربری با این شناسه پیدا نشد. شناسه دیگری بفرستید یا /cancel را بزنید.")
        return
    await state.update_data(target_user_id=target_user_id)
    await state.set_state(AdminSpeedState.waiting_multiplier)
    await message.answer(
        panel("⚡ تغییر سرعت", [f"کاربر: {target_user_id}", "ضریب جدید را ارسال کنید؛ مانند 2 یا 3.5."]),
        reply_markup=cancel_keyboard(),
    )


@router.message(AdminSpeedState.waiting_multiplier)
async def admin_speed_value_handler(message: Message, state: FSMContext) -> None:
    if not await ensure_admin_message(message, state):
        return
    multiplier = parse_positive_decimal(message.text)
    if multiplier is None:
        await message.answer("ضریب سرعت باید یک عدد مثبت و معتبر باشد.", reply_markup=cancel_keyboard())
        return
    data = await state.get_data()
    target_user_id = data.get("target_user_id")
    if not isinstance(target_user_id, int):
        await state.clear()
        await message.answer(panel("⚡ تغییر سرعت", ["اطلاعات عملیات ناقص است.", "دوباره شروع کنید."]))
        return
    await state.update_data(new_speed_multiplier=str(multiplier))
    await state.set_state(AdminSpeedState.waiting_confirmation)
    await message.answer(
        panel(
            "⚠️ تأیید نهایی",
            [f"کاربر: {target_user_id}", f"ضریب جدید: {format_multiplier(multiplier)}"],
        ),
        reply_markup=admin_change_confirmation_keyboard("speed"),
    )


@router.callback_query(AdminSpeedState.waiting_confirmation, F.data == "admin_change:confirm:speed")
async def admin_speed_confirmation_handler(callback: CallbackQuery, state: FSMContext) -> None:
    if not await ensure_admin_callback(callback):
        return
    data = await state.get_data()
    target_user_id = data.get("target_user_id")
    multiplier = parse_positive_decimal(str(data.get("new_speed_multiplier", "")))
    if not isinstance(target_user_id, int) or multiplier is None:
        await state.clear()
        await callback.answer("اطلاعات عملیات ناقص است.", show_alert=True)
        await edit_callback_message(callback, admin_panel_text(), admin_panel_keyboard())
        return
    user = db.admin_set_speed_multiplier(sender_id(callback) or 0, target_user_id, multiplier)
    await state.clear()
    if user:
        text = panel("⚡ تغییر سرعت", [f"کاربر: {target_user_id}", f"ضریب جدید: {format_multiplier(user.speed_multiplier)}"])
        await callback.answer("ضریب سرعت ثبت شد.")
    else:
        text = panel("⚡ تغییر سرعت", ["کاربر پیدا نشد."])
        await callback.answer("کاربر پیدا نشد.", show_alert=True)
    await edit_callback_message(callback, text, admin_panel_keyboard())

@router.callback_query(F.data == "admin:payments")
async def admin_payments_callback_handler(callback: CallbackQuery, state: FSMContext) -> None:
    if not await ensure_admin_callback(callback):
        return
    await state.clear()
    await callback.answer()
    await show_payments_page(callback, 0)


@router.callback_query(F.data.startswith("admin_payments:"))
async def admin_payments_page_callback_handler(callback: CallbackQuery, state: FSMContext) -> None:
    if not await ensure_admin_callback(callback):
        return
    await state.clear()
    raw_offset = (callback.data or "").split(":", maxsplit=1)[1]
    if not re.fullmatch(r"\d{1,9}", raw_offset):
        await callback.answer("شماره صفحه معتبر نیست.", show_alert=True)
        return
    await callback.answer()
    await show_payments_page(callback, int(raw_offset))


@router.callback_query(F.data.startswith("admin_payment:"))
async def admin_payment_confirmation_callback_handler(callback: CallbackQuery, state: FSMContext) -> None:
    if not await ensure_admin_callback(callback):
        return
    await state.clear()
    parts = (callback.data or "").split(":")
    if len(parts) != 3 or parts[1] not in {"approve", "reject"}:
        await callback.answer("درخواست نامعتبر است.", show_alert=True)
        return
    payment_id = parse_callback_id(parts[2])
    if payment_id is None:
        await callback.answer("شناسه درخواست معتبر نیست.", show_alert=True)
        return
    payment = db.get_payment(payment_id)
    if not payment:
        await callback.answer("درخواست پرداخت پیدا نشد.", show_alert=True)
        return
    if payment.status != "pending":
        await callback.answer("این درخواست قبلاً بررسی شده است.", show_alert=True)
        return
    if parts[1] == "approve" and package_multiplier(payment.package) is None:
        await callback.answer("پکیج ذخیره‌شده معتبر نیست.", show_alert=True)
        return

    await callback.answer()
    await edit_callback_message(
        callback,
        payment_confirmation_text(payment, parts[1]),
        payment_confirmation_keyboard(parts[1], payment.id),
    )


@router.callback_query(F.data.startswith("admin_payment_confirm:"))
async def admin_payment_review_callback_handler(callback: CallbackQuery, bot: Bot, state: FSMContext) -> None:
    if not await ensure_admin_callback(callback):
        return
    await state.clear()
    parts = (callback.data or "").split(":")
    if len(parts) != 3 or parts[1] not in {"approve", "reject"}:
        await callback.answer("درخواست نامعتبر است.", show_alert=True)
        return
    payment_id = parse_callback_id(parts[2])
    if payment_id is None:
        await callback.answer("شناسه درخواست معتبر نیست.", show_alert=True)
        return
    payment = db.get_payment(payment_id)
    if not payment or payment.status != "pending":
        await callback.answer("این درخواست قبلاً بررسی شده است.", show_alert=True)
        return

    approved = parts[1] == "approve"
    multiplier = package_multiplier(payment.package) if approved else None
    if approved and multiplier is None:
        await callback.answer("پکیج ذخیره‌شده معتبر نیست.", show_alert=True)
        return
    result = db.review_payment(
        payment_id=payment_id,
        approved=approved,
        admin_id=sender_id(callback) or 0,
        speed_multiplier=multiplier,
    )
    if result.status not in {"approved", "rejected"} or not result.payment:
        await callback.answer("این درخواست هم‌اکنون بررسی شده است.", show_alert=True)
        return

    reviewed = result.payment
    if result.status == "approved":
        await notify_user(
            bot,
            reviewed.user_id,
            panel("💳 پرداخت سرعت", ["وضعیت: تأیید شد", f"سرعت جدید: {format_multiplier(multiplier or Decimal('1'))}"]),
        )
        await callback.answer("پرداخت تأیید شد.")
    else:
        await notify_user(
            bot,
            reviewed.user_id,
            panel("💳 پرداخت سرعت", ["وضعیت: رد شد", "در صورت نیاز با پشتیبانی تماس بگیرید."]),
        )
        await callback.answer("درخواست رد شد.")
    await show_payments_page(callback, 0)

@router.callback_query(F.data == "admin:withdrawals")
async def admin_withdrawals_callback_handler(callback: CallbackQuery, state: FSMContext) -> None:
    if not await ensure_admin_callback(callback):
        return
    await state.clear()
    await callback.answer()
    await show_withdrawals_page(callback, 0)


@router.callback_query(F.data.startswith("admin_withdrawals:"))
async def admin_withdrawals_page_callback_handler(callback: CallbackQuery, state: FSMContext) -> None:
    if not await ensure_admin_callback(callback):
        return
    await state.clear()
    raw_offset = (callback.data or "").split(":", maxsplit=1)[1]
    if not re.fullmatch(r"\d{1,9}", raw_offset):
        await callback.answer("شماره صفحه معتبر نیست.", show_alert=True)
        return
    await callback.answer()
    await show_withdrawals_page(callback, int(raw_offset))


@router.callback_query(F.data.startswith("admin_withdrawal:"))
async def admin_withdrawal_confirmation_callback_handler(callback: CallbackQuery, state: FSMContext) -> None:
    if not await ensure_admin_callback(callback):
        return
    await state.clear()
    parts = (callback.data or "").split(":")
    if len(parts) != 3 or parts[1] not in {"approve", "reject"}:
        await callback.answer("درخواست نامعتبر است.", show_alert=True)
        return
    withdrawal_id = parse_callback_id(parts[2])
    if withdrawal_id is None:
        await callback.answer("شناسه درخواست معتبر نیست.", show_alert=True)
        return
    withdrawal = db.get_withdrawal(withdrawal_id)
    if not withdrawal:
        await callback.answer("درخواست برداشت پیدا نشد.", show_alert=True)
        return
    if withdrawal.status != "pending":
        await callback.answer("این درخواست قبلاً بررسی شده است.", show_alert=True)
        return

    await callback.answer()
    await edit_callback_message(
        callback,
        withdrawal_confirmation_text(withdrawal, parts[1]),
        withdrawal_confirmation_keyboard(parts[1], withdrawal.id),
    )


@router.callback_query(F.data.startswith("admin_withdrawal_confirm:"))
async def admin_withdrawal_review_callback_handler(callback: CallbackQuery, bot: Bot, state: FSMContext) -> None:
    if not await ensure_admin_callback(callback):
        return
    await state.clear()
    parts = (callback.data or "").split(":")
    if len(parts) != 3 or parts[1] not in {"approve", "reject"}:
        await callback.answer("درخواست نامعتبر است.", show_alert=True)
        return
    withdrawal_id = parse_callback_id(parts[2])
    if withdrawal_id is None:
        await callback.answer("شناسه درخواست معتبر نیست.", show_alert=True)
        return
    withdrawal = db.get_withdrawal(withdrawal_id)
    if not withdrawal or withdrawal.status != "pending":
        await callback.answer("این درخواست قبلاً بررسی شده است.", show_alert=True)
        return

    approved = parts[1] == "approve"
    result = db.review_withdrawal(
        withdrawal_id=withdrawal_id,
        approved=approved,
        admin_id=sender_id(callback) or 0,
    )
    if result.status == "not_found":
        await callback.answer("درخواست برداشت پیدا نشد.", show_alert=True)
        return
    if result.status == "already_reviewed" or not result.withdrawal:
        await callback.answer("این درخواست هم‌اکنون بررسی شده است.", show_alert=True)
        return

    reviewed = result.withdrawal
    if result.status == "approved":
        await notify_user(
            bot,
            reviewed.user_id,
            panel(
                "📤 برداشت وجه",
                [
                    "وضعیت: واریز تأیید شد",
                    f"مبلغ: {format_tron(reviewed.amount)}",
                    "موجودی و ماین این دوره صفر شد.",
                ],
            ),
        )
        await callback.answer("واریز تأیید شد.")
    else:
        await notify_user(
            bot,
            reviewed.user_id,
            panel("📤 برداشت وجه", ["وضعیت: درخواست رد شد", "می‌توانید دوباره درخواست ثبت کنید."]),
        )
        await callback.answer("درخواست رد شد.")
    await show_withdrawals_page(callback, 0)

@router.callback_query(F.data == "admin:stats")
async def admin_stats_callback_handler(callback: CallbackQuery, state: FSMContext) -> None:
    if not await ensure_admin_callback(callback):
        return
    await state.clear()
    stats = db.get_stats()
    await callback.answer()
    await edit_callback_message(
        callback,
        panel(
            "📊 آمار کلی",
            [
                f"تعداد کاربران: {stats['users_count']}",
                f"مجموع موجودی‌ها: {format_tron(stats['total_balance'])}",
                f"مجموع ماین‌شده: {format_tron(stats['total_mined'])}",
            ],
        ),
        admin_panel_keyboard(),
    )


@router.callback_query(F.data == "admin:wallet")
async def admin_wallet_callback_handler(callback: CallbackQuery, state: FSMContext) -> None:
    if not await ensure_admin_callback(callback):
        return
    await state.clear()
    await state.set_state(AdminWalletState.waiting_address)
    current_address = get_wallet_address()
    await callback.answer()
    if isinstance(callback.message, Message):
        current_text = current_address if valid_tron_address(current_address) else "تنظیم نشده"
        await callback.message.answer(
            panel("🏷 آدرس TRON", [f"آدرس فعلی: {current_text}", "آدرس جدید را ارسال کنید."]),
            reply_markup=cancel_keyboard(),
        )


@router.message(AdminWalletState.waiting_address)
async def admin_wallet_address_handler(message: Message, state: FSMContext) -> None:
    if not await ensure_admin_message(message, state):
        return
    wallet_address = (message.text or "").strip()
    if not valid_tron_address(wallet_address):
        await message.answer(
            panel("🏷 آدرس TRON", ["آدرس معتبر نیست.", "آدرس ۳۴ کاراکتری را دوباره ارسال کنید."]),
            reply_markup=cancel_keyboard(),
        )
        return
    await state.update_data(new_wallet_address=wallet_address)
    await state.set_state(AdminWalletState.waiting_confirmation)
    await message.answer(
        panel("⚠️ تأیید نهایی", ["آدرس جدید TRON:", wallet_address]),
        reply_markup=admin_change_confirmation_keyboard("wallet"),
    )


@router.callback_query(AdminWalletState.waiting_confirmation, F.data == "admin_change:confirm:wallet")
async def admin_wallet_confirmation_handler(callback: CallbackQuery, state: FSMContext) -> None:
    if not await ensure_admin_callback(callback):
        return
    data = await state.get_data()
    wallet_address = str(data.get("new_wallet_address", "")).strip()
    if not valid_tron_address(wallet_address):
        await state.clear()
        await callback.answer("آدرس معتبر نیست.", show_alert=True)
        await edit_callback_message(callback, admin_panel_text(), admin_panel_keyboard())
        return
    db.set_wallet_address(sender_id(callback) or 0, wallet_address)
    await state.clear()
    await callback.answer("آدرس ذخیره شد.")
    await edit_callback_message(
        callback,
        panel("🏷 آدرس TRON", ["آدرس جدید ذخیره شد.", wallet_address]),
        admin_panel_keyboard(),
    )

@router.message(StateFilter(None))
async def fallback_message_handler(message: Message) -> None:
    await message.answer("لطفاً از دکمه‌های منو استفاده کنید یا /start را بفرستید.")


@router.callback_query()
async def fallback_callback_handler(callback: CallbackQuery) -> None:
    await callback.answer("این دکمه دیگر معتبر نیست.", show_alert=True)


def validate_configuration() -> None:
    if not BOT_TOKEN:
        raise RuntimeError("متغیر محیطی BOT_TOKEN تنظیم نشده است.")
    if not BASE_MINING_RATE.is_finite() or BASE_MINING_RATE <= Decimal("0"):
        raise RuntimeError("BASE_MINING_RATE باید یک عدد مثبت باشد.")
    if not DEFAULT_MINING_CAP.is_finite() or DEFAULT_MINING_CAP <= Decimal("0"):
        raise RuntimeError("DEFAULT_MINING_CAP باید یک عدد مثبت باشد.")
    if MINING_COOLDOWN_SECONDS < 1:
        raise RuntimeError("MINING_COOLDOWN_SECONDS باید حداقل ۱ باشد.")
    if not REFERRAL_FIXED_REWARD.is_finite() or REFERRAL_FIXED_REWARD < Decimal("0"):
        raise RuntimeError("REFERRAL_FIXED_REWARD نمی‌تواند منفی باشد.")
    if not REFERRAL_MINING_PERCENTAGE.is_finite() or REFERRAL_MINING_PERCENTAGE < Decimal("0"):
        raise RuntimeError("REFERRAL_MINING_PERCENTAGE نمی‌تواند منفی باشد.")
    for multiplier, price in SPEED_PACKAGES.items():
        if int(multiplier) <= 0 or not Decimal(str(price)).is_finite() or Decimal(str(price)) <= Decimal("0"):
            raise RuntimeError("SPEED_PACKAGES دارای مقدار نامعتبر است.")
    if not ADMIN_IDS:
        logger.warning("ADMIN_IDS خالی است؛ تا زمان تنظیم آن هیچ‌کس به پنل مدیریت دسترسی ندارد.")


async def main() -> None:
    validate_configuration()
    database_path = Path(DATABASE_PATH)
    database_path.parent.mkdir(parents=True, exist_ok=True)
    db.initialize()

    bot = Bot(token=BOT_TOKEN)
    dispatcher = Dispatcher(storage=MemoryStorage())
    dispatcher.include_router(router)

    global runtime_bot_username
    try:
        me = await bot.get_me()
        runtime_bot_username = runtime_bot_username or me.username or ""
        logger.info("ربات با نام @%s اجرا شد.", runtime_bot_username or "بدون نام کاربری")
        await dispatcher.start_polling(bot, allowed_updates=dispatcher.resolve_used_update_types())
    finally:
        await bot.session.close()


if __name__ == "__main__":
    asyncio.run(main())
