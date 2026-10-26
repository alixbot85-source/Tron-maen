"""تنظیمات اجرایی قابل‌کنترل از پنل ادمین.

مقادیر پیش‌فرض از config.py می‌آیند و مقدار معتبر ذخیره‌شده در SQLite بر آن‌ها
اولویت دارد. این ماژول منطق اعتبارسنجی مشترک ربات و وب‌اپ را نگه می‌دارد.
"""

from __future__ import annotations

from dataclasses import dataclass
from decimal import Decimal, InvalidOperation
from typing import Mapping
from urllib.parse import urlparse

from config import (
    BASE_MINING_RATE,
    MINING_COOLDOWN_SECONDS,
    REFERRAL_FIXED_REWARD,
    REFERRAL_MINING_PERCENTAGE,
    SPEED_PACKAGES,
    TRON_WALLET_ADDRESS,
    WEB_APP_URL,
)
from database import Database


SETTING_BASE_RATE = "base_mining_rate"
SETTING_MINING_COOLDOWN = "mining_cooldown_seconds"
SETTING_REFERRAL_FIXED_REWARD = "referral_fixed_reward"
SETTING_REFERRAL_PERCENTAGE = "referral_mining_percentage"
SETTING_TRON_WALLET_ADDRESS = "tron_wallet_address"
SETTING_WEB_APP_URL = "web_app_url"


def speed_package_setting_key(multiplier: int) -> str:
    return f"speed_package_{multiplier}"


def valid_web_app_url(value: str) -> bool:
    """وب‌اپ تلگرام به آدرس عمومی HTTPS در ریشهٔ دامنه نیاز دارد."""
    parsed = urlparse(value.strip())
    return (
        parsed.scheme == "https"
        and bool(parsed.netloc)
        and parsed.path in {"", "/"}
        and not parsed.params
        and not parsed.query
        and not parsed.fragment
    )


def normalize_web_app_url(value: str) -> str:
    return value.strip().rstrip("/")


def _positive_decimal(value: str | None, default: Decimal, allow_zero: bool = False) -> Decimal:
    if not value:
        return default
    try:
        parsed = Decimal(value)
    except (InvalidOperation, ValueError):
        return default
    if not parsed.is_finite() or parsed < Decimal("0") or (not allow_zero and parsed == Decimal("0")):
        return default
    return parsed


def _positive_integer(value: str | None, default: int) -> int:
    if not value or not value.isdigit():
        return default
    parsed = int(value)
    return parsed if parsed > 0 else default


@dataclass(frozen=True, slots=True)
class RuntimeConfig:
    base_mining_rate: Decimal
    mining_cooldown_seconds: int
    referral_fixed_reward: Decimal
    referral_mining_percentage: Decimal
    speed_packages: Mapping[int, Decimal]
    tron_wallet_address: str
    web_app_url: str


class RuntimeSettings:
    """تنظیمات فعلی را از SQLite می‌خواند و در نبود آن‌ها از config استفاده می‌کند."""

    def __init__(self, database: Database) -> None:
        self._database = database

    def snapshot(self) -> RuntimeConfig:
        values = self._database.get_all_settings()
        packages: dict[int, Decimal] = {}
        for multiplier, default_price in SPEED_PACKAGES.items():
            packages[int(multiplier)] = _positive_decimal(
                values.get(speed_package_setting_key(int(multiplier))),
                Decimal(str(default_price)),
            )

        percentage = _positive_decimal(
            values.get(SETTING_REFERRAL_PERCENTAGE),
            REFERRAL_MINING_PERCENTAGE,
            allow_zero=True,
        )
        # درصد پاداش نمی‌تواند از 100% بیشتر باشد؛ مقدار نامعتبر به پیش‌فرض برمی‌گردد.
        if percentage > Decimal("1"):
            percentage = REFERRAL_MINING_PERCENTAGE

        web_app_url = normalize_web_app_url(values.get(SETTING_WEB_APP_URL, WEB_APP_URL))
        if web_app_url and not valid_web_app_url(web_app_url):
            web_app_url = ""

        return RuntimeConfig(
            base_mining_rate=_positive_decimal(values.get(SETTING_BASE_RATE), BASE_MINING_RATE),
            mining_cooldown_seconds=_positive_integer(
                values.get(SETTING_MINING_COOLDOWN), MINING_COOLDOWN_SECONDS
            ),
            referral_fixed_reward=_positive_decimal(
                values.get(SETTING_REFERRAL_FIXED_REWARD),
                REFERRAL_FIXED_REWARD,
                allow_zero=True,
            ),
            referral_mining_percentage=percentage,
            speed_packages=packages,
            tron_wallet_address=(values.get(SETTING_TRON_WALLET_ADDRESS) or TRON_WALLET_ADDRESS).strip(),
            web_app_url=web_app_url,
        )
