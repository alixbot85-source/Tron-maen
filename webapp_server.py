"""وب‌سرور امن و سبک برای Telegram Web App ربات.

این سرویس با همان SQLite ربات کار می‌کند و فقط دادهٔ امضاشدهٔ Telegram WebApp
را می‌پذیرد؛ شناسهٔ کاربر هرگز از بدنهٔ درخواست گرفته نمی‌شود.
"""

from __future__ import annotations

import hashlib
import hmac
import json
import logging
import time
from decimal import Decimal, ROUND_HALF_UP, localcontext
from pathlib import Path
from typing import Optional
from urllib.parse import parse_qsl

from aiohttp import web

from database import ClaimResult, Database, User
from runtime_settings import RuntimeSettings


logger = logging.getLogger(__name__)
STATIC_DIR = Path(__file__).resolve().parent / "webapp"
# محدودکردن عمر دادهٔ امضاشده، از استفادهٔ مجدد از نشست قدیمی جلوگیری می‌کند.
INIT_DATA_MAX_AGE_SECONDS = 24 * 60 * 60


def _format_amount(value: Decimal) -> str:
    with localcontext() as context:
        context.prec = max(40, len(value.as_tuple().digits) + 10)
        rounded = value.quantize(Decimal("0.0001"), rounding=ROUND_HALF_UP)
    text = format(rounded, "f").rstrip("0").rstrip(".") if "." in format(rounded, "f") else format(rounded, "f")
    return text or "0"


def _remaining_seconds(user: User, cooldown_seconds: int) -> int:
    elapsed = max(0, int(time.time()) - user.last_mining_time)
    return max(0, cooldown_seconds - elapsed)


def _validate_init_data(init_data: str, bot_token: str) -> Optional[int]:
    """امضای initData تلگرام را طبق روش رسمی HMAC بررسی می‌کند."""
    if not init_data or not bot_token:
        return None
    values = dict(parse_qsl(init_data, keep_blank_values=True))
    received_hash = values.pop("hash", "")
    raw_user = values.get("user")
    try:
        auth_date = int(values["auth_date"])
    except (KeyError, TypeError, ValueError):
        return None
    now = int(time.time())
    if (
        not received_hash
        or not raw_user
        or auth_date <= 0
        or auth_date > now + 5 * 60
        or now - auth_date > INIT_DATA_MAX_AGE_SECONDS
    ):
        return None

    data_check_string = "\n".join(f"{key}={value}" for key, value in sorted(values.items()))
    secret_key = hmac.new(b"WebAppData", bot_token.encode("utf-8"), hashlib.sha256).digest()
    calculated_hash = hmac.new(
        secret_key,
        data_check_string.encode("utf-8"),
        hashlib.sha256,
    ).hexdigest()
    if not hmac.compare_digest(calculated_hash, received_hash):
        return None

    try:
        user_data = json.loads(raw_user)
        user_id = int(user_data["id"])
    except (ValueError, TypeError, KeyError, json.JSONDecodeError):
        return None
    return user_id if user_id > 0 else None


def _profile_payload(user: User, settings: RuntimeSettings, database: Database) -> dict[str, object]:
    runtime = settings.snapshot()
    remaining = _remaining_seconds(user, runtime.mining_cooldown_seconds)
    withdrawal_pending = database.has_pending_withdrawal(user.user_id)
    if withdrawal_pending:
        mining_status = "در انتظار بررسی برداشت"
    elif user.total_mined >= user.mining_cap:
        mining_status = "آماده برداشت وجه"
    elif remaining:
        mining_status = f"{remaining} ثانیه تا برداشت بعدی"
    else:
        mining_status = "آماده برداشت ماین"
    return {
        "user_id": user.user_id,
        "first_name": user.first_name,
        "balance": _format_amount(user.balance),
        "total_mined": _format_amount(user.total_mined),
        "mining_cap": _format_amount(user.mining_cap),
        "speed_multiplier": _format_amount(user.speed_multiplier),
        "base_rate": _format_amount(runtime.base_mining_rate),
        "remaining_seconds": remaining,
        "can_withdraw": user.total_mined >= user.mining_cap and user.balance > Decimal("0") and not withdrawal_pending,
        "mining_status": mining_status,
    }


async def _authenticated_user(request: web.Request) -> tuple[Optional[User], Optional[web.Response]]:
    try:
        body = await request.json()
    except (json.JSONDecodeError, ValueError):
        return None, web.json_response({"error": "درخواست معتبر نیست."}, status=400)
    init_data = str(body.get("initData", "")) if isinstance(body, dict) else ""
    user_id = _validate_init_data(init_data, request.app["bot_token"])
    if user_id is None:
        return None, web.json_response({"error": "دسترسی وب‌اپ معتبر نیست."}, status=401)
    user = request.app["database"].get_user(user_id)
    if not user:
        return None, web.json_response({"error": "ابتدا ربات را با /start فعال کنید."}, status=404)
    return user, None


async def index_handler(request: web.Request) -> web.FileResponse:
    return web.FileResponse(STATIC_DIR / "index.html")


async def health_handler(request: web.Request) -> web.Response:
    return web.json_response({"status": "ok"})


async def profile_handler(request: web.Request) -> web.Response:
    user, error = await _authenticated_user(request)
    if error:
        return error
    assert user is not None
    return web.json_response(_profile_payload(user, request.app["settings"], request.app["database"]))


async def mine_handler(request: web.Request) -> web.Response:
    user, error = await _authenticated_user(request)
    if error:
        return error
    assert user is not None
    database: Database = request.app["database"]
    settings: RuntimeSettings = request.app["settings"]
    runtime = settings.snapshot()
    result: ClaimResult = database.claim_mining(
        user_id=user.user_id,
        base_rate_per_minute=runtime.base_mining_rate,
        cooldown_seconds=runtime.mining_cooldown_seconds,
        referral_percentage=runtime.referral_mining_percentage,
    )
    refreshed = database.get_user(user.user_id)
    payload: dict[str, object] = {
        "status": result.status,
        "amount": _format_amount(result.amount),
        "referral_bonus": _format_amount(result.referral_bonus),
        "remaining_seconds": result.remaining_seconds,
    }
    if refreshed:
        payload["profile"] = _profile_payload(refreshed, settings, database)
    return web.json_response(payload)


def create_web_app(database: Database, settings: RuntimeSettings, bot_token: str) -> web.Application:
    app = web.Application()
    app["database"] = database
    app["settings"] = settings
    app["bot_token"] = bot_token
    app.router.add_get("/", index_handler)
    app.router.add_get("/health", health_handler)
    app.router.add_post("/api/profile", profile_handler)
    app.router.add_post("/api/mine", mine_handler)
    app.router.add_static("/static/", STATIC_DIR, show_index=False)
    return app


async def start_web_app_server(
    database: Database,
    settings: RuntimeSettings,
    bot_token: str,
    host: str,
    port: int,
) -> web.AppRunner:
    app = create_web_app(database, settings, bot_token)
    runner = web.AppRunner(app)
    await runner.setup()
    site = web.TCPSite(runner, host=host, port=port)
    await site.start()
    logger.info("وب‌اپ روی %s:%s اجرا شد.", host, port)
    return runner
