"""ربات تلگرام شبیه‌سازی ماینینگ TRON با aiogram 3 و SQLite."""

from __future__ import annotations

import asyncio
import logging
import re
from datetime import datetime, timezone
from decimal import Decimal, InvalidOperation, ROUND_HALF_UP, localcontext
from pathlib import Path
from typing import Optional

from aiogram import Bot, Dispatcher, F, Router
from aiogram.exceptions import TelegramAPIError, TelegramBadRequest
from aiogram.filters import Command, CommandStart, StateFilter
from aiogram.fsm.context import FSMContext
from aiogram.fsm.state import State, StatesGroup
from aiogram.fsm.storage.memory import MemoryStorage
from aiogram.types import CallbackQuery, Message

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
    BTN_ADMIN,
    BTN_BALANCE,
    BTN_CAP,
    BTN_HELP,
    BTN_MINE,
    BTN_REFERRAL,
    BTN_SPEED,
    BTN_WITHDRAW,
    admin_balance_mode_keyboard,
    admin_panel_keyboard,
    cancel_keyboard,
    main_menu,
    mining_cap_keyboard,
    payments_page_keyboard,
    speed_packages_keyboard,
    users_page_keyboard,
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


class AdminCapState(StatesGroup):
    waiting_user_id = State()
    waiting_cap = State()


class AdminSpeedState(StatesGroup):
    waiting_user_id = State()
    waiting_multiplier = State()


class AdminWalletState(StatesGroup):
    waiting_address = State()


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


def format_timestamp(timestamp: int) -> str:
    return datetime.fromtimestamp(timestamp, tz=timezone.utc).strftime("%Y-%m-%d %H:%M UTC")


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


def user_details_text(user: User) -> str:
    referral = str(user.referrer_id) if user.referrer_id is not None else "ندارد"
    username = f"@{one_line(user.username, 32)}" if user.username else "ندارد"
    return "\n".join(
        [
            "👤 اطلاعات کاربر",
            f"شناسه: {user.user_id}",
            f"نام: {one_line(user.first_name) or 'ندارد'}",
            f"نام کاربری: {username}",
            f"موجودی: {format_tron(user.balance)}",
            f"ماین این دوره: {format_tron(user.total_mined)} از {format_tron(user.mining_cap)}",
            f"ضریب سرعت: {format_multiplier(user.speed_multiplier)}",
            f"معرف: {referral}",
            f"زمان ثبت‌نام: {format_timestamp(user.created_at)}",
        ]
    )


def admin_panel_text() -> str:
    return "🛠 پنل مدیریت\nیکی از گزینه‌های زیر را انتخاب کنید."


def users_page_text(users: list[User], total: int, offset: int) -> str:
    if not users:
        return "👥 فهرست کاربران\nکاربری برای نمایش وجود ندارد."
    start = offset + 1
    end = offset + len(users)
    lines = [f"👥 فهرست کاربران ({start} تا {end} از {total})"]
    for user in users:
        lines.extend(
            [
                f"• {display_user_name(user.user_id, user.username, user.first_name)}",
                f"  موجودی: {format_tron(user.balance)} | ماین: {format_tron(user.total_mined)}/{format_tron(user.mining_cap)}",
                f"  سرعت: {format_multiplier(user.speed_multiplier)}",
            ]
        )
    return "\n".join(lines)


def payments_page_text(payments: list[Payment], total: int, offset: int) -> str:
    if not payments:
        return "💳 پرداخت‌های در انتظار\nدرخواستی در انتظار بررسی نیست."
    start = offset + 1
    end = offset + len(payments)
    lines = [f"💳 پرداخت‌های در انتظار ({start} تا {end} از {total})"]
    for payment in payments:
        lines.extend(
            [
                f"\n#{payment.id} | {display_user_name(payment.user_id, payment.user_username, payment.user_first_name)}",
                f"پکیج: {payment.package} | مبلغ: {format_tron(payment.amount_tron)}",
                f"TXID: {payment.txid}",
                f"ثبت: {format_timestamp(payment.created_at)}",
            ]
        )
    return "\n".join(lines)


def withdrawals_page_text(withdrawals: list[Withdrawal], total: int, offset: int) -> str:
    if not withdrawals:
        return "🏦 برداشت‌های در انتظار\nدرخواستی در انتظار بررسی نیست."
    start = offset + 1
    end = offset + len(withdrawals)
    lines = [f"🏦 برداشت‌های در انتظار ({start} تا {end} از {total})"]
    for withdrawal in withdrawals:
        lines.extend(
            [
                f"\n#{withdrawal.id} | {display_user_name(withdrawal.user_id, withdrawal.user_username, withdrawal.user_first_name)}",
                f"مبلغ: {format_tron(withdrawal.amount)}",
                f"آدرس: {withdrawal.tron_address}",
                f"ثبت: {format_timestamp(withdrawal.created_at)}",
            ]
        )
    return "\n".join(lines)


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

    if result.created:
        text = "خوش آمدید! حساب شما ساخته شد. از منوی زیر شروع کنید."
        if result.referrer_id is not None:
            text += "\nثبت‌نام شما با لینک دعوت انجام شد."
    else:
        text = "خوش آمدید! از منوی زیر استفاده کنید."
    await message.answer(text, reply_markup=main_menu(is_admin(message.from_user.id)))


@router.message(Command("cancel"))
async def cancel_command_handler(message: Message, state: FSMContext) -> None:
    await state.clear()
    await message.answer("عملیات لغو شد.")


@router.callback_query(F.data == "cancel")
async def cancel_callback_handler(callback: CallbackQuery, state: FSMContext) -> None:
    await state.clear()
    await callback.answer("عملیات لغو شد.")
    if isinstance(callback.message, Message):
        await callback.message.answer("عملیات لغو شد.")


@router.message(Command("help"))
@router.message(StateFilter(None), F.text == BTN_HELP)
async def help_handler(message: Message) -> None:
    await message.answer(
        "راهنما:\n"
        "۱) با «دریافت ماین» موجودی زمان گذشته را دریافت کنید.\n"
        "۲) سقف ماین خود را انتخاب کنید. پس از رسیدن ماین این دوره به سقف، درخواست برداشت فعال می‌شود.\n"
        "۳) برای سرعت بیشتر، پکیج را انتخاب و TXID پرداخت TRON را بفرستید.\n"
        "۴) با لینک دعوت، پاداش ثابت و درصدی دریافت می‌کنید.\n"
        "برای لغو هر مرحله، /cancel را بفرستید."
    )


async def show_admin_panel_message(message: Message, state: FSMContext) -> None:
    if not await ensure_admin_message(message, state):
        return
    await state.clear()
    await message.answer(admin_panel_text(), reply_markup=admin_panel_keyboard())


@router.message(Command("admin"))
async def admin_command_handler(message: Message, state: FSMContext) -> None:
    await show_admin_panel_message(message, state)


@router.message(StateFilter(None), F.text == BTN_ADMIN)
async def admin_button_handler(message: Message, state: FSMContext) -> None:
    await show_admin_panel_message(message, state)


@router.message(StateFilter(None), F.text == BTN_BALANCE)
async def balance_handler(message: Message) -> None:
    user = await ensure_registered(message)
    if not user:
        return
    await message.answer(
        "💰 وضعیت شما\n"
        f"موجودی: {format_tron(user.balance)}\n"
        f"ماین این دوره: {format_tron(user.total_mined)} از {format_tron(user.mining_cap)}\n"
        f"ضریب سرعت: {format_multiplier(user.speed_multiplier)}"
    )


@router.message(StateFilter(None), F.text == BTN_MINE)
async def mine_handler(message: Message) -> None:
    user = await ensure_registered(message)
    if not user:
        return
    result = db.claim_mining(
        user_id=user.user_id,
        base_rate_per_minute=BASE_MINING_RATE,
        cooldown_seconds=MINING_COOLDOWN_SECONDS,
        referral_percentage=REFERRAL_MINING_PERCENTAGE,
    )
    if result.status == "claimed":
        lines = [
            f"✅ {format_tron(result.amount)} به موجودی شما اضافه شد.",
            f"موجودی فعلی: {format_tron(result.balance)}",
            f"ماین این دوره: {format_tron(result.total_mined)} از {format_tron(result.mining_cap)}",
        ]
        if result.total_mined >= result.mining_cap:
            lines.append("سقف ماین پر شد؛ اکنون می‌توانید درخواست برداشت ثبت کنید.")
        await message.answer("\n".join(lines))
    elif result.status == "too_soon":
        await message.answer(
            f"هنوز برای دریافت ماین زود است. زمان باقی‌مانده: {format_remaining(result.remaining_seconds)}"
        )
    elif result.status == "cap_reached":
        await message.answer(
            "سقف ماین این دوره پر شده است. برای ادامه، ابتدا از بخش «درخواست برداشت» درخواست خود را ثبت کنید."
        )
    elif result.status == "withdrawal_pending":
        await message.answer("درخواست برداشت شما در انتظار بررسی است؛ تا پایان بررسی امکان دریافت ماین ندارید.")
    else:
        await message.answer("حساب شما پیدا نشد. لطفاً دوباره /start را بفرستید.")


@router.message(StateFilter(None), F.text == BTN_CAP)
async def mining_cap_handler(message: Message) -> None:
    user = await ensure_registered(message)
    if not user:
        return
    await message.answer(
        f"سقف فعلی شما {format_tron(user.mining_cap)} است. سقف جدید را انتخاب کنید:",
        reply_markup=mining_cap_keyboard(user.mining_cap),
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
        f"✅ سقف ماین شما روی {format_tron(user.mining_cap)} تنظیم شد.",
        mining_cap_keyboard(user.mining_cap),
    )


@router.message(StateFilter(None), F.text == BTN_REFERRAL)
async def referral_handler(message: Message, bot: Bot) -> None:
    user = await ensure_registered(message)
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
        await message.answer("نام کاربری ربات هنوز در تلگرام تنظیم نشده است؛ لینک دعوت فعلاً در دسترس نیست.")
        return

    invite_link = f"https://t.me/{runtime_bot_username}?start=REF_{user.user_id}"
    referral_percent = format_number(REFERRAL_MINING_PERCENTAGE * Decimal("100"))
    await message.answer(
        "👥 لینک دعوت اختصاصی شما:\n"
        f"{invite_link}\n\n"
        f"برای هر ثبت‌نام موفق: {format_tron(REFERRAL_FIXED_REWARD)}\n"
        f"از هر دریافت ماین دعوت‌شده: {referral_percent}٪"
    )


@router.message(StateFilter(None), F.text == BTN_SPEED)
async def speed_handler(message: Message) -> None:
    user = await ensure_registered(message)
    if not user:
        return
    wallet_address = get_wallet_address()
    if not valid_tron_address(wallet_address):
        await message.answer("آدرس دریافت TRON هنوز توسط ادمین تنظیم نشده است. کمی بعد دوباره تلاش کنید.")
        return
    await message.answer(
        f"ضریب سرعت فعلی شما: {format_multiplier(user.speed_multiplier)}\n"
        "پکیج موردنظر را انتخاب کنید:",
        reply_markup=speed_packages_keyboard(),
    )


@router.callback_query(StateFilter(None), F.data.startswith("speed:"))
async def speed_package_callback_handler(callback: CallbackQuery, state: FSMContext) -> None:
    user_id = sender_id(callback)
    if user_id is None:
        await callback.answer("کاربر نامعتبر است.", show_alert=True)
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
    if not db.get_user(user_id):
        await callback.answer("ابتدا با /start وارد ربات شوید.", show_alert=True)
        return
    wallet_address = get_wallet_address()
    if not valid_tron_address(wallet_address):
        await callback.answer("آدرس پرداخت تنظیم نشده است.", show_alert=True)
        return

    await state.set_state(PaymentState.waiting_txid)
    await state.update_data(
        package=f"{multiplier}x",
        amount=str(price),
    )
    await callback.answer()
    if isinstance(callback.message, Message):
        await callback.message.answer(
            f"برای فعال‌سازی سرعت {format_multiplier(multiplier)}، دقیقاً {format_tron(price)} به آدرس زیر بفرستید:\n"
            f"{wallet_address}\n\n"
            "سپس هش تراکنش (TXID) ۶۴ کاراکتری را ارسال کنید.",
            reply_markup=cancel_keyboard(),
        )


@router.message(PaymentState.waiting_txid)
async def payment_txid_handler(message: Message, state: FSMContext) -> None:
    user_id = sender_id(message)
    if user_id is None:
        return
    txid = (message.text or "").strip()
    if not TXID_PATTERN.fullmatch(txid):
        await message.answer(
            "TXID معتبر نیست. هش تراکنش TRON باید دقیقاً ۶۴ کاراکتر معتبر باشد.",
            reply_markup=cancel_keyboard(),
        )
        return
    data = await state.get_data()
    package = str(data.get("package", ""))
    amount = parse_positive_decimal(str(data.get("amount", "")))
    multiplier = package_multiplier(package)
    if amount is None or multiplier is None:
        await state.clear()
        await message.answer("اطلاعات پکیج پیدا نشد. لطفاً دوباره از بخش «ارتقای سرعت» شروع کنید.")
        return

    result = db.create_payment(
        user_id=user_id,
        package=package,
        amount_tron=amount,
        txid=txid.upper(),
    )
    if result.status == "duplicate_txid":
        await message.answer("این TXID قبلاً ثبت شده است. هش یک تراکنش فقط یک‌بار پذیرفته می‌شود.")
        return
    await state.clear()
    if result.status == "created" and result.payment:
        await message.answer(
            f"✅ درخواست پرداخت پکیج {package} ثبت شد. پس از بررسی ادمین، سرعت شما فعال می‌شود.\n"
            f"شناسه درخواست: #{result.payment.id}"
        )
    else:
        await message.answer("حساب شما پیدا نشد. لطفاً دوباره /start را بفرستید.")


@router.message(StateFilter(None), F.text == BTN_WITHDRAW)
async def withdrawal_start_handler(message: Message, state: FSMContext) -> None:
    user = await ensure_registered(message)
    if not user:
        return
    if user.total_mined < user.mining_cap:
        remaining = user.mining_cap - user.total_mined
        await message.answer(
            f"هنوز به سقف ماین نرسیده‌اید. {format_tron(remaining)} دیگر تا فعال‌شدن برداشت باقی مانده است."
        )
        return
    if user.balance <= Decimal("0"):
        await message.answer("موجودی قابل برداشت ندارید.")
        return
    if db.has_pending_withdrawal(user.user_id):
        await message.answer("یک درخواست برداشت در انتظار بررسی دارید.")
        return
    await state.set_state(WithdrawalState.waiting_address)
    await message.answer(
        f"مبلغ درخواست شما {format_tron(user.balance)} است.\n"
        "آدرس TRON خود را بفرستید. آدرس باید با T آغاز شود و ۳۴ کاراکتر داشته باشد.",
        reply_markup=cancel_keyboard(),
    )


@router.message(WithdrawalState.waiting_address)
async def withdrawal_address_handler(message: Message, state: FSMContext) -> None:
    user_id = sender_id(message)
    if user_id is None:
        return
    tron_address = (message.text or "").strip()
    if not valid_tron_address(tron_address):
        await message.answer(
            "آدرس TRON معتبر نیست. آدرس باید با T آغاز شود و ۳۴ کاراکتر داشته باشد.",
            reply_markup=cancel_keyboard(),
        )
        return

    result = db.create_withdrawal(user_id, tron_address)
    if result.status == "created" and result.withdrawal:
        await state.clear()
        await message.answer(
            f"✅ درخواست برداشت {format_tron(result.withdrawal.amount)} ثبت شد. پس از واریز دستی، ادمین آن را تأیید می‌کند.\n"
            f"شناسه درخواست: #{result.withdrawal.id}"
        )
        return

    await state.clear()
    messages = {
        "not_eligible": "هنوز به سقف ماین نرسیده‌اید و امکان ثبت برداشت ندارید.",
        "empty_balance": "موجودی قابل برداشت ندارید.",
        "already_pending": "یک درخواست برداشت در انتظار بررسی دارید.",
        "user_not_found": "حساب شما پیدا نشد. لطفاً دوباره /start را بفرستید.",
    }
    await message.answer(messages.get(result.status, "ثبت درخواست برداشت ممکن نشد."))


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
        await callback.message.answer("شناسه عددی کاربر را ارسال کنید.", reply_markup=cancel_keyboard())


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
    await message.answer(user_details_text(user))


@router.callback_query(F.data == "admin:balance")
async def admin_balance_callback_handler(callback: CallbackQuery, state: FSMContext) -> None:
    if not await ensure_admin_callback(callback):
        return
    await state.clear()
    await state.set_state(AdminBalanceState.waiting_user_id)
    await callback.answer()
    if isinstance(callback.message, Message):
        await callback.message.answer(
            "شناسه عددی کاربری را که می‌خواهید موجودی او را تغییر دهید ارسال کنید.",
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
    await message.answer("نوع تغییر موجودی را انتخاب کنید:", reply_markup=admin_balance_mode_keyboard())


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
            f"مقدار {direction} موجودی را به TRON ارسال کنید.",
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
        f"دلیل این تغییر را بنویسید (حداکثر {MAX_REASON_LENGTH} کاراکتر).",
        reply_markup=cancel_keyboard(),
    )


@router.message(AdminBalanceState.waiting_reason)
async def admin_balance_reason_handler(message: Message, state: FSMContext) -> None:
    if not await ensure_admin_message(message, state):
        return
    reason = (message.text or "").strip()
    if not reason or len(reason) > MAX_REASON_LENGTH:
        await message.answer(
            f"دلیل باید بین ۱ تا {MAX_REASON_LENGTH} کاراکتر باشد.",
            reply_markup=cancel_keyboard(),
        )
        return
    data = await state.get_data()
    target_user_id = data.get("target_user_id")
    mode = data.get("balance_mode")
    amount = parse_positive_decimal(str(data.get("balance_amount", "")))
    if not isinstance(target_user_id, int) or mode not in {"increase", "decrease"} or amount is None:
        await state.clear()
        await message.answer("اطلاعات عملیات ناقص است. لطفاً دوباره از پنل مدیریت شروع کنید.")
        return
    change = amount if mode == "increase" else -amount
    result = db.adjust_balance(
        admin_id=sender_id(message) or 0,
        target_user_id=target_user_id,
        change=change,
        reason=reason,
    )
    await state.clear()
    if result.status == "updated":
        action = "افزایش" if change > Decimal("0") else "کاهش"
        await message.answer(
            f"✅ {action} {format_tron(abs(change))} برای کاربر {target_user_id} ثبت شد.\n"
            f"موجودی جدید: {format_tron(result.balance)}"
        )
    elif result.status == "insufficient_balance":
        await message.answer(
            f"کاهش انجام نشد؛ موجودی فعلی کاربر فقط {format_tron(result.balance)} است."
        )
    else:
        await message.answer("کاربر پیدا نشد.")


@router.callback_query(F.data == "admin:cap")
async def admin_cap_callback_handler(callback: CallbackQuery, state: FSMContext) -> None:
    if not await ensure_admin_callback(callback):
        return
    await state.clear()
    await state.set_state(AdminCapState.waiting_user_id)
    await callback.answer()
    if isinstance(callback.message, Message):
        await callback.message.answer(
            "شناسه عددی کاربر را ارسال کنید تا سقف ماین او تغییر کند.",
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
    await message.answer("سقف جدید ماین را به TRON و به‌صورت یک عدد مثبت ارسال کنید.", reply_markup=cancel_keyboard())


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
        await message.answer("اطلاعات عملیات ناقص است. لطفاً دوباره از پنل مدیریت شروع کنید.")
        return
    user = db.admin_set_mining_cap(sender_id(message) or 0, target_user_id, mining_cap)
    await state.clear()
    if user:
        await message.answer(f"✅ سقف ماین کاربر {target_user_id} روی {format_tron(user.mining_cap)} تنظیم شد.")
    else:
        await message.answer("کاربر پیدا نشد.")


@router.callback_query(F.data == "admin:speed")
async def admin_speed_callback_handler(callback: CallbackQuery, state: FSMContext) -> None:
    if not await ensure_admin_callback(callback):
        return
    await state.clear()
    await state.set_state(AdminSpeedState.waiting_user_id)
    await callback.answer()
    if isinstance(callback.message, Message):
        await callback.message.answer(
            "شناسه عددی کاربر را ارسال کنید تا ضریب سرعت او تغییر کند.",
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
    await message.answer("ضریب سرعت جدید را به‌صورت یک عدد مثبت ارسال کنید؛ مانند 2 یا 3.5.", reply_markup=cancel_keyboard())


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
        await message.answer("اطلاعات عملیات ناقص است. لطفاً دوباره از پنل مدیریت شروع کنید.")
        return
    user = db.admin_set_speed_multiplier(sender_id(message) or 0, target_user_id, multiplier)
    await state.clear()
    if user:
        await message.answer(
            f"✅ ضریب سرعت کاربر {target_user_id} روی {format_multiplier(user.speed_multiplier)} تنظیم شد."
        )
    else:
        await message.answer("کاربر پیدا نشد.")


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
    if not payment:
        await callback.answer("درخواست پرداخت پیدا نشد.", show_alert=True)
        return
    if payment.status != "pending":
        await callback.answer("این درخواست قبلاً بررسی شده است.", show_alert=True)
        return

    approved = parts[1] == "approve"
    multiplier = package_multiplier(payment.package) if approved else None
    if approved and multiplier is None:
        await callback.answer("پکیج ذخیره‌شده نامعتبر است و تأیید نشد.", show_alert=True)
        return
    result = db.review_payment(
        payment_id=payment_id,
        approved=approved,
        admin_id=sender_id(callback) or 0,
        speed_multiplier=multiplier,
    )
    if result.status not in {"approved", "rejected"} or not result.payment:
        await callback.answer("این درخواست هم‌اکنون توسط ادمین دیگری بررسی شده است.", show_alert=True)
        return

    reviewed = result.payment
    if result.status == "approved":
        await notify_user(
            bot,
            reviewed.user_id,
            f"✅ پرداخت شما تأیید شد و سرعت حساب روی {format_multiplier(multiplier or Decimal('1'))} فعال شد.",
        )
        await callback.answer("پرداخت تأیید شد.")
    else:
        await notify_user(
            bot,
            reviewed.user_id,
            "❌ درخواست پرداخت شما رد شد. در صورت نیاز با پشتیبانی تماس بگیرید.",
        )
        await callback.answer("پرداخت رد شد.")
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
    if not withdrawal:
        await callback.answer("درخواست برداشت پیدا نشد.", show_alert=True)
        return
    if withdrawal.status != "pending":
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
        await callback.answer("این درخواست قبلاً بررسی شده است.", show_alert=True)
        return

    reviewed = result.withdrawal
    if result.status == "approved":
        await notify_user(
            bot,
            reviewed.user_id,
            f"✅ واریز دستی درخواست برداشت #{reviewed.id} به مبلغ {format_tron(reviewed.amount)} تأیید شد. "
            "موجودی و ماین این دوره شما صفر شد.",
        )
        await callback.answer("واریز تأیید شد.")
    else:
        await notify_user(
            bot,
            reviewed.user_id,
            "❌ درخواست برداشت شما رد شد. می‌توانید پس از بررسی، دوباره درخواست ثبت کنید.",
        )
        await callback.answer("درخواست برداشت رد شد.")
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
        "📊 آمار کلی\n"
        f"تعداد کاربران: {stats['users_count']}\n"
        f"مجموع موجودی‌ها: {format_tron(stats['total_balance'])}\n"
        f"مجموع ماین‌شده در دوره‌های جاری: {format_tron(stats['total_mined'])}",
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
            f"آدرس فعلی دریافت TRON: {current_text}\n"
            "آدرس جدید را ارسال کنید. آدرس باید با T آغاز شود و ۳۴ کاراکتر داشته باشد.",
            reply_markup=cancel_keyboard(),
        )


@router.message(AdminWalletState.waiting_address)
async def admin_wallet_address_handler(message: Message, state: FSMContext) -> None:
    if not await ensure_admin_message(message, state):
        return
    wallet_address = (message.text or "").strip()
    if not valid_tron_address(wallet_address):
        await message.answer(
            "آدرس TRON معتبر نیست. آدرس باید با T آغاز شود و ۳۴ کاراکتر داشته باشد.",
            reply_markup=cancel_keyboard(),
        )
        return
    db.set_wallet_address(sender_id(message) or 0, wallet_address)
    await state.clear()
    await message.answer("✅ آدرس دریافت TRON ذخیره شد و از این پس به کاربران نمایش داده می‌شود.")


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
