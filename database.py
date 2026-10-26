"""لایهٔ دسترسی به SQLite برای ربات ماینینگ TRON.

مقادیر پولی به‌شکل متن در SQLite ذخیره می‌شوند تا خطای float ایجاد نشود و
هنگام خواندن دوباره به Decimal تبدیل می‌شوند.
"""

from __future__ import annotations

import sqlite3
import threading
import time
from contextlib import contextmanager
from dataclasses import dataclass
from decimal import Decimal
from pathlib import Path
from typing import Iterator, Optional


ZERO = Decimal("0")


@dataclass(frozen=True, slots=True)
class User:
    user_id: int
    username: Optional[str]
    first_name: str
    balance: Decimal
    speed_multiplier: Decimal
    mining_cap: Decimal
    total_mined: Decimal
    last_mining_time: int
    referrer_id: Optional[int]
    created_at: int


@dataclass(frozen=True, slots=True)
class Payment:
    id: int
    user_id: int
    package: str
    amount_tron: Decimal
    txid: str
    status: str
    created_at: int
    reviewed_at: Optional[int]
    user_username: Optional[str] = None
    user_first_name: str = ""


@dataclass(frozen=True, slots=True)
class Withdrawal:
    id: int
    user_id: int
    amount: Decimal
    tron_address: str
    status: str
    created_at: int
    reviewed_at: Optional[int]
    user_username: Optional[str] = None
    user_first_name: str = ""


@dataclass(frozen=True, slots=True)
class RegistrationResult:
    user: User
    created: bool
    referrer_id: Optional[int]


@dataclass(frozen=True, slots=True)
class ClaimResult:
    status: str
    amount: Decimal = ZERO
    referral_bonus: Decimal = ZERO
    balance: Decimal = ZERO
    total_mined: Decimal = ZERO
    mining_cap: Decimal = ZERO
    remaining_seconds: int = 0


@dataclass(frozen=True, slots=True)
class PaymentCreationResult:
    status: str
    payment: Optional[Payment] = None


@dataclass(frozen=True, slots=True)
class WithdrawalCreationResult:
    status: str
    withdrawal: Optional[Withdrawal] = None


@dataclass(frozen=True, slots=True)
class PaymentReviewResult:
    status: str
    payment: Optional[Payment] = None


@dataclass(frozen=True, slots=True)
class WithdrawalReviewResult:
    status: str
    withdrawal: Optional[Withdrawal] = None


@dataclass(frozen=True, slots=True)
class BalanceAdjustmentResult:
    status: str
    balance: Decimal = ZERO


def _to_decimal(value: object) -> Decimal:
    return Decimal(str(value))


def _decimal_to_db(value: Decimal) -> str:
    """Decimal را بدون نمایش علمی و بدون خطای float برای SQLite آماده می‌کند."""
    value = _to_decimal(value)
    if not value.is_finite():
        raise ValueError("مقدار عددی باید متناهی باشد.")
    if value == ZERO:
        return "0"
    return format(value.normalize(), "f")


def _user_from_row(row: sqlite3.Row) -> User:
    return User(
        user_id=int(row["user_id"]),
        username=row["username"],
        first_name=row["first_name"] or "",
        balance=_to_decimal(row["balance"]),
        speed_multiplier=_to_decimal(row["speed_multiplier"]),
        mining_cap=_to_decimal(row["mining_cap"]),
        total_mined=_to_decimal(row["total_mined"]),
        last_mining_time=int(row["last_mining_time"]),
        referrer_id=(int(row["referrer_id"]) if row["referrer_id"] is not None else None),
        created_at=int(row["created_at"]),
    )


def _payment_from_row(row: sqlite3.Row) -> Payment:
    return Payment(
        id=int(row["id"]),
        user_id=int(row["user_id"]),
        package=row["package"],
        amount_tron=_to_decimal(row["amount_tron"]),
        txid=row["txid"],
        status=row["status"],
        created_at=int(row["created_at"]),
        reviewed_at=(int(row["reviewed_at"]) if row["reviewed_at"] is not None else None),
        user_username=row["user_username"] if "user_username" in row.keys() else None,
        user_first_name=(row["user_first_name"] if "user_first_name" in row.keys() else "") or "",
    )


def _withdrawal_from_row(row: sqlite3.Row) -> Withdrawal:
    return Withdrawal(
        id=int(row["id"]),
        user_id=int(row["user_id"]),
        amount=_to_decimal(row["amount"]),
        tron_address=row["tron_address"],
        status=row["status"],
        created_at=int(row["created_at"]),
        reviewed_at=(int(row["reviewed_at"]) if row["reviewed_at"] is not None else None),
        user_username=row["user_username"] if "user_username" in row.keys() else None,
        user_first_name=(row["user_first_name"] if "user_first_name" in row.keys() else "") or "",
    )


class Database:
    """مجموعهٔ عملیات اتمی دیتابیس.

    هر عملیات نوشتنی با BEGIN IMMEDIATE اجرا می‌شود تا دو پیام هم‌زمان نتوانند
    یک موجودی یا یک درخواست در انتظار را دوبار تغییر دهند.
    """

    def __init__(self, path: str | Path) -> None:
        self.path = str(path)
        self._lock = threading.RLock()

    def _connect(self) -> sqlite3.Connection:
        connection = sqlite3.connect(self.path, timeout=30)
        connection.row_factory = sqlite3.Row
        connection.execute("PRAGMA foreign_keys = ON")
        connection.execute("PRAGMA busy_timeout = 5000")
        return connection

    @contextmanager
    def _transaction(self) -> Iterator[sqlite3.Connection]:
        with self._lock:
            connection = self._connect()
            try:
                connection.execute("BEGIN IMMEDIATE")
                yield connection
                connection.commit()
            except Exception:
                connection.rollback()
                raise
            finally:
                connection.close()

    def initialize(self) -> None:
        """جدول‌ها و ایندکس‌های لازم را در اولین اجرا می‌سازد."""
        schema = """
        CREATE TABLE IF NOT EXISTS users (
            user_id INTEGER PRIMARY KEY,
            username TEXT,
            first_name TEXT NOT NULL DEFAULT '',
            balance TEXT NOT NULL DEFAULT '0',
            speed_multiplier TEXT NOT NULL DEFAULT '1',
            mining_cap TEXT NOT NULL,
            total_mined TEXT NOT NULL DEFAULT '0',
            last_mining_time INTEGER NOT NULL,
            referrer_id INTEGER REFERENCES users(user_id),
            created_at INTEGER NOT NULL
        );

        CREATE TABLE IF NOT EXISTS payments (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            user_id INTEGER NOT NULL REFERENCES users(user_id),
            package TEXT NOT NULL,
            amount_tron TEXT NOT NULL,
            txid TEXT NOT NULL,
            status TEXT NOT NULL CHECK (status IN ('pending', 'approved', 'rejected')),
            created_at INTEGER NOT NULL,
            reviewed_at INTEGER
        );

        CREATE TABLE IF NOT EXISTS withdrawals (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            user_id INTEGER NOT NULL REFERENCES users(user_id),
            amount TEXT NOT NULL,
            tron_address TEXT NOT NULL,
            status TEXT NOT NULL CHECK (status IN ('pending', 'approved', 'rejected')),
            created_at INTEGER NOT NULL,
            reviewed_at INTEGER
        );

        CREATE TABLE IF NOT EXISTS admin_logs (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            admin_id INTEGER NOT NULL,
            target_user_id INTEGER,
            action TEXT NOT NULL,
            amount TEXT,
            reason TEXT NOT NULL DEFAULT '',
            created_at INTEGER NOT NULL
        );

        CREATE TABLE IF NOT EXISTS settings (
            key TEXT PRIMARY KEY,
            value TEXT NOT NULL
        );

        CREATE INDEX IF NOT EXISTS idx_payments_status_created
            ON payments(status, created_at);
        CREATE INDEX IF NOT EXISTS idx_withdrawals_status_created
            ON withdrawals(status, created_at);
        CREATE INDEX IF NOT EXISTS idx_users_referrer_id
            ON users(referrer_id);
        CREATE UNIQUE INDEX IF NOT EXISTS idx_payments_txid_unique
            ON payments(txid);
        """
        with self._lock:
            connection = self._connect()
            try:
                connection.execute("PRAGMA journal_mode = WAL")
                connection.executescript(schema)
                connection.commit()
            finally:
                connection.close()

    @staticmethod
    def _get_user(connection: sqlite3.Connection, user_id: int) -> Optional[User]:
        row = connection.execute("SELECT * FROM users WHERE user_id = ?", (user_id,)).fetchone()
        return _user_from_row(row) if row else None

    @staticmethod
    def _get_payment(connection: sqlite3.Connection, payment_id: int) -> Optional[Payment]:
        row = connection.execute("SELECT * FROM payments WHERE id = ?", (payment_id,)).fetchone()
        return _payment_from_row(row) if row else None

    @staticmethod
    def _get_withdrawal(connection: sqlite3.Connection, withdrawal_id: int) -> Optional[Withdrawal]:
        row = connection.execute("SELECT * FROM withdrawals WHERE id = ?", (withdrawal_id,)).fetchone()
        return _withdrawal_from_row(row) if row else None

    @staticmethod
    def _add_admin_log(
        connection: sqlite3.Connection,
        admin_id: int,
        target_user_id: Optional[int],
        action: str,
        amount: Optional[Decimal],
        reason: str,
        now: int,
    ) -> None:
        connection.execute(
            """
            INSERT INTO admin_logs (admin_id, target_user_id, action, amount, reason, created_at)
            VALUES (?, ?, ?, ?, ?, ?)
            """,
            (
                admin_id,
                target_user_id,
                action,
                _decimal_to_db(amount) if amount is not None else None,
                reason,
                now,
            ),
        )

    def register_user(
        self,
        user_id: int,
        username: Optional[str],
        first_name: str,
        referrer_candidate: Optional[int],
        fixed_referral_reward: Decimal,
        default_mining_cap: Decimal = Decimal("2000"),
        now: Optional[int] = None,
    ) -> RegistrationResult:
        """کاربر را ثبت و پاداش ثابت دعوت را فقط در اولین ثبت‌نام اعمال می‌کند."""
        if fixed_referral_reward < ZERO:
            raise ValueError("پاداش دعوت نمی‌تواند منفی باشد.")
        if default_mining_cap <= ZERO:
            raise ValueError("سقف پیش‌فرض ماین باید مثبت باشد.")
        current_time = int(time.time()) if now is None else int(now)
        safe_username = (username or "").strip()[:255] or None
        safe_first_name = (first_name or "").strip()[:255]

        with self._transaction() as connection:
            existing = self._get_user(connection, user_id)
            if existing:
                # اطلاعات نمایشی ممکن است پس از ثبت‌نام در تلگرام تغییر کرده باشد.
                connection.execute(
                    "UPDATE users SET username = ?, first_name = ? WHERE user_id = ?",
                    (safe_username, safe_first_name, user_id),
                )
                refreshed = self._get_user(connection, user_id)
                assert refreshed is not None
                return RegistrationResult(refreshed, False, refreshed.referrer_id)

            referrer_id: Optional[int] = None
            if referrer_candidate and referrer_candidate != user_id:
                referrer = self._get_user(connection, referrer_candidate)
                if referrer:
                    referrer_id = referrer_candidate

            connection.execute(
                """
                INSERT INTO users (
                    user_id, username, first_name, balance, speed_multiplier,
                    mining_cap, total_mined, last_mining_time, referrer_id, created_at
                ) VALUES (?, ?, ?, '0', '1', ?, '0', ?, ?, ?)
                """,
                (
                    user_id,
                    safe_username,
                    safe_first_name,
                    _decimal_to_db(default_mining_cap),
                    current_time,
                    referrer_id,
                    current_time,
                ),
            )

            if referrer_id is not None and fixed_referral_reward > ZERO:
                referrer = self._get_user(connection, referrer_id)
                assert referrer is not None
                new_balance = referrer.balance + fixed_referral_reward
                connection.execute(
                    "UPDATE users SET balance = ? WHERE user_id = ?",
                    (_decimal_to_db(new_balance), referrer_id),
                )

            user = self._get_user(connection, user_id)
            assert user is not None
            return RegistrationResult(user, True, referrer_id)

    def get_user(self, user_id: int) -> Optional[User]:
        with self._lock:
            connection = self._connect()
            try:
                return self._get_user(connection, user_id)
            finally:
                connection.close()

    def claim_mining(
        self,
        user_id: int,
        base_rate_per_minute: Decimal,
        cooldown_seconds: int,
        referral_percentage: Decimal,
        now: Optional[int] = None,
    ) -> ClaimResult:
        """ماین دستی و پاداش لحظه‌ای دعوت را در یک تراکنش ثبت می‌کند."""
        if base_rate_per_minute <= ZERO:
            raise ValueError("نرخ پایه باید مثبت باشد.")
        if cooldown_seconds < 1:
            raise ValueError("فاصلهٔ برداشت باید حداقل یک ثانیه باشد.")
        if referral_percentage < ZERO:
            raise ValueError("درصد دعوت نمی‌تواند منفی باشد.")

        current_time = int(time.time()) if now is None else int(now)
        with self._transaction() as connection:
            user = self._get_user(connection, user_id)
            if not user:
                return ClaimResult(status="user_not_found")

            # مبلغ برداشت در زمان ثبت درخواست ثابت می‌شود؛ تا بررسی آن، ماین جدید
            # متوقف است تا تأیید ادمین موجودی تازه را ناخواسته صفر نکند.
            pending_withdrawal = connection.execute(
                "SELECT 1 FROM withdrawals WHERE user_id = ? AND status = 'pending' LIMIT 1",
                (user_id,),
            ).fetchone()
            if pending_withdrawal:
                return ClaimResult(
                    status="withdrawal_pending",
                    balance=user.balance,
                    total_mined=user.total_mined,
                    mining_cap=user.mining_cap,
                )

            if user.total_mined >= user.mining_cap:
                return ClaimResult(
                    status="cap_reached",
                    balance=user.balance,
                    total_mined=user.total_mined,
                    mining_cap=user.mining_cap,
                )

            elapsed_seconds = max(0, current_time - user.last_mining_time)
            if elapsed_seconds < cooldown_seconds:
                return ClaimResult(
                    status="too_soon",
                    balance=user.balance,
                    total_mined=user.total_mined,
                    mining_cap=user.mining_cap,
                    remaining_seconds=cooldown_seconds - elapsed_seconds,
                )

            elapsed_minutes = Decimal(elapsed_seconds) / Decimal("60")
            calculated_amount = elapsed_minutes * base_rate_per_minute * user.speed_multiplier
            remaining_to_cap = user.mining_cap - user.total_mined
            # سقف، آخرین برداشت را نیز محدود می‌کند تا total_mined از آن عبور نکند.
            mined_amount = min(calculated_amount, remaining_to_cap)
            if mined_amount <= ZERO:
                return ClaimResult(
                    status="cap_reached",
                    balance=user.balance,
                    total_mined=user.total_mined,
                    mining_cap=user.mining_cap,
                )

            new_balance = user.balance + mined_amount
            new_total_mined = user.total_mined + mined_amount
            connection.execute(
                """
                UPDATE users
                SET balance = ?, total_mined = ?, last_mining_time = ?
                WHERE user_id = ?
                """,
                (
                    _decimal_to_db(new_balance),
                    _decimal_to_db(new_total_mined),
                    current_time,
                    user_id,
                ),
            )

            referral_bonus = ZERO
            if user.referrer_id is not None and referral_percentage > ZERO:
                referrer = self._get_user(connection, user.referrer_id)
                if referrer:
                    referral_bonus = mined_amount * referral_percentage
                    connection.execute(
                        "UPDATE users SET balance = ? WHERE user_id = ?",
                        (_decimal_to_db(referrer.balance + referral_bonus), referrer.user_id),
                    )

            return ClaimResult(
                status="claimed",
                amount=mined_amount,
                referral_bonus=referral_bonus,
                balance=new_balance,
                total_mined=new_total_mined,
                mining_cap=user.mining_cap,
            )

    def set_user_mining_cap(self, user_id: int, mining_cap: Decimal) -> Optional[User]:
        if mining_cap <= ZERO:
            raise ValueError("سقف ماین باید مثبت باشد.")
        with self._transaction() as connection:
            if not self._get_user(connection, user_id):
                return None
            connection.execute(
                "UPDATE users SET mining_cap = ? WHERE user_id = ?",
                (_decimal_to_db(mining_cap), user_id),
            )
            return self._get_user(connection, user_id)

    def create_payment(
        self,
        user_id: int,
        package: str,
        amount_tron: Decimal,
        txid: str,
        now: Optional[int] = None,
    ) -> PaymentCreationResult:
        if amount_tron <= ZERO:
            raise ValueError("مبلغ پرداخت باید مثبت باشد.")
        if not package or not txid:
            raise ValueError("پکیج و هش تراکنش لازم هستند.")

        current_time = int(time.time()) if now is None else int(now)
        with self._transaction() as connection:
            if not self._get_user(connection, user_id):
                return PaymentCreationResult(status="user_not_found")

            duplicate = connection.execute(
                "SELECT id FROM payments WHERE lower(txid) = lower(?) LIMIT 1",
                (txid,),
            ).fetchone()
            if duplicate:
                return PaymentCreationResult(status="duplicate_txid")

            cursor = connection.execute(
                """
                INSERT INTO payments (user_id, package, amount_tron, txid, status, created_at)
                VALUES (?, ?, ?, ?, 'pending', ?)
                """,
                (user_id, package, _decimal_to_db(amount_tron), txid, current_time),
            )
            payment = self._get_payment(connection, int(cursor.lastrowid))
            assert payment is not None
            return PaymentCreationResult(status="created", payment=payment)

    def get_payment(self, payment_id: int) -> Optional[Payment]:
        with self._lock:
            connection = self._connect()
            try:
                return self._get_payment(connection, payment_id)
            finally:
                connection.close()

    def get_pending_payments_page(self, limit: int, offset: int) -> tuple[list[Payment], int]:
        safe_limit = max(1, min(int(limit), 50))
        safe_offset = max(0, int(offset))
        with self._lock:
            connection = self._connect()
            try:
                total = int(
                    connection.execute(
                        "SELECT COUNT(*) FROM payments WHERE status = 'pending'"
                    ).fetchone()[0]
                )
                rows = connection.execute(
                    """
                    SELECT p.*, u.username AS user_username, u.first_name AS user_first_name
                    FROM payments AS p
                    JOIN users AS u ON u.user_id = p.user_id
                    WHERE p.status = 'pending'
                    ORDER BY p.created_at ASC, p.id ASC
                    LIMIT ? OFFSET ?
                    """,
                    (safe_limit, safe_offset),
                ).fetchall()
                return [_payment_from_row(row) for row in rows], total
            finally:
                connection.close()

    def review_payment(
        self,
        payment_id: int,
        approved: bool,
        admin_id: int,
        speed_multiplier: Optional[Decimal] = None,
        now: Optional[int] = None,
    ) -> PaymentReviewResult:
        if approved and (speed_multiplier is None or speed_multiplier <= ZERO):
            raise ValueError("ضریب سرعت تأییدشده معتبر نیست.")

        current_time = int(time.time()) if now is None else int(now)
        with self._transaction() as connection:
            payment = self._get_payment(connection, payment_id)
            if not payment:
                return PaymentReviewResult(status="not_found")
            if payment.status != "pending":
                return PaymentReviewResult(status="already_reviewed", payment=payment)

            new_status = "approved" if approved else "rejected"
            connection.execute(
                "UPDATE payments SET status = ?, reviewed_at = ? WHERE id = ?",
                (new_status, current_time, payment_id),
            )
            if approved:
                assert speed_multiplier is not None
                connection.execute(
                    "UPDATE users SET speed_multiplier = ? WHERE user_id = ?",
                    (_decimal_to_db(speed_multiplier), payment.user_id),
                )

            self._add_admin_log(
                connection,
                admin_id=admin_id,
                target_user_id=payment.user_id,
                action="payment_approved" if approved else "payment_rejected",
                amount=payment.amount_tron,
                reason=f"payment_id={payment.id}; txid={payment.txid}",
                now=current_time,
            )
            reviewed_payment = self._get_payment(connection, payment_id)
            assert reviewed_payment is not None
            return PaymentReviewResult(status=new_status, payment=reviewed_payment)

    def get_withdrawal(self, withdrawal_id: int) -> Optional[Withdrawal]:
        with self._lock:
            connection = self._connect()
            try:
                return self._get_withdrawal(connection, withdrawal_id)
            finally:
                connection.close()

    def has_pending_withdrawal(self, user_id: int) -> bool:
        with self._lock:
            connection = self._connect()
            try:
                return (
                    connection.execute(
                        "SELECT 1 FROM withdrawals WHERE user_id = ? AND status = 'pending' LIMIT 1",
                        (user_id,),
                    ).fetchone()
                    is not None
                )
            finally:
                connection.close()

    def create_withdrawal(
        self,
        user_id: int,
        tron_address: str,
        now: Optional[int] = None,
    ) -> WithdrawalCreationResult:
        if not tron_address:
            raise ValueError("آدرس TRON لازم است.")

        current_time = int(time.time()) if now is None else int(now)
        with self._transaction() as connection:
            user = self._get_user(connection, user_id)
            if not user:
                return WithdrawalCreationResult(status="user_not_found")
            if user.total_mined < user.mining_cap:
                return WithdrawalCreationResult(status="not_eligible")
            if user.balance <= ZERO:
                return WithdrawalCreationResult(status="empty_balance")
            if connection.execute(
                "SELECT 1 FROM withdrawals WHERE user_id = ? AND status = 'pending' LIMIT 1",
                (user_id,),
            ).fetchone():
                return WithdrawalCreationResult(status="already_pending")

            cursor = connection.execute(
                """
                INSERT INTO withdrawals (user_id, amount, tron_address, status, created_at)
                VALUES (?, ?, ?, 'pending', ?)
                """,
                (user_id, _decimal_to_db(user.balance), tron_address, current_time),
            )
            withdrawal = self._get_withdrawal(connection, int(cursor.lastrowid))
            assert withdrawal is not None
            return WithdrawalCreationResult(status="created", withdrawal=withdrawal)

    def get_pending_withdrawals_page(self, limit: int, offset: int) -> tuple[list[Withdrawal], int]:
        safe_limit = max(1, min(int(limit), 50))
        safe_offset = max(0, int(offset))
        with self._lock:
            connection = self._connect()
            try:
                total = int(
                    connection.execute(
                        "SELECT COUNT(*) FROM withdrawals WHERE status = 'pending'"
                    ).fetchone()[0]
                )
                rows = connection.execute(
                    """
                    SELECT w.*, u.username AS user_username, u.first_name AS user_first_name
                    FROM withdrawals AS w
                    JOIN users AS u ON u.user_id = w.user_id
                    WHERE w.status = 'pending'
                    ORDER BY w.created_at ASC, w.id ASC
                    LIMIT ? OFFSET ?
                    """,
                    (safe_limit, safe_offset),
                ).fetchall()
                return [_withdrawal_from_row(row) for row in rows], total
            finally:
                connection.close()

    def review_withdrawal(
        self,
        withdrawal_id: int,
        approved: bool,
        admin_id: int,
        now: Optional[int] = None,
    ) -> WithdrawalReviewResult:
        current_time = int(time.time()) if now is None else int(now)
        with self._transaction() as connection:
            withdrawal = self._get_withdrawal(connection, withdrawal_id)
            if not withdrawal:
                return WithdrawalReviewResult(status="not_found")
            if withdrawal.status != "pending":
                return WithdrawalReviewResult(status="already_reviewed", withdrawal=withdrawal)

            new_status = "approved" if approved else "rejected"
            connection.execute(
                "UPDATE withdrawals SET status = ?, reviewed_at = ? WHERE id = ?",
                (new_status, current_time, withdrawal_id),
            )
            if approved:
                # با تسویهٔ دستی، چرخهٔ ماین هم از نو آغاز می‌شود تا زمان قدیمی دوباره محاسبه نشود.
                connection.execute(
                    """
                    UPDATE users
                    SET balance = '0', total_mined = '0', last_mining_time = ?
                    WHERE user_id = ?
                    """,
                    (current_time, withdrawal.user_id),
                )

            self._add_admin_log(
                connection,
                admin_id=admin_id,
                target_user_id=withdrawal.user_id,
                action="withdrawal_approved" if approved else "withdrawal_rejected",
                amount=withdrawal.amount,
                reason=f"withdrawal_id={withdrawal.id}; address={withdrawal.tron_address}",
                now=current_time,
            )
            reviewed_withdrawal = self._get_withdrawal(connection, withdrawal_id)
            assert reviewed_withdrawal is not None
            return WithdrawalReviewResult(status=new_status, withdrawal=reviewed_withdrawal)

    def get_users_page(self, limit: int, offset: int) -> tuple[list[User], int]:
        safe_limit = max(1, min(int(limit), 100))
        safe_offset = max(0, int(offset))
        with self._lock:
            connection = self._connect()
            try:
                total = int(connection.execute("SELECT COUNT(*) FROM users").fetchone()[0])
                rows = connection.execute(
                    """
                    SELECT * FROM users
                    ORDER BY created_at DESC, user_id DESC
                    LIMIT ? OFFSET ?
                    """,
                    (safe_limit, safe_offset),
                ).fetchall()
                return [_user_from_row(row) for row in rows], total
            finally:
                connection.close()

    def get_stats(self) -> dict[str, Decimal | int]:
        with self._lock:
            connection = self._connect()
            try:
                rows = connection.execute("SELECT balance, total_mined FROM users").fetchall()
                return {
                    "users_count": len(rows),
                    "total_balance": sum((_to_decimal(row["balance"]) for row in rows), ZERO),
                    "total_mined": sum((_to_decimal(row["total_mined"]) for row in rows), ZERO),
                }
            finally:
                connection.close()

    def adjust_balance(
        self,
        admin_id: int,
        target_user_id: int,
        change: Decimal,
        reason: str,
        now: Optional[int] = None,
    ) -> BalanceAdjustmentResult:
        if change == ZERO:
            raise ValueError("تغییر موجودی نمی‌تواند صفر باشد.")
        if not change.is_finite():
            raise ValueError("مقدار تغییر موجودی معتبر نیست.")

        current_time = int(time.time()) if now is None else int(now)
        with self._transaction() as connection:
            user = self._get_user(connection, target_user_id)
            if not user:
                return BalanceAdjustmentResult(status="user_not_found")
            new_balance = user.balance + change
            if new_balance < ZERO:
                return BalanceAdjustmentResult(status="insufficient_balance", balance=user.balance)

            connection.execute(
                "UPDATE users SET balance = ? WHERE user_id = ?",
                (_decimal_to_db(new_balance), target_user_id),
            )
            self._add_admin_log(
                connection,
                admin_id=admin_id,
                target_user_id=target_user_id,
                action="balance_increase" if change > ZERO else "balance_decrease",
                amount=change,
                reason=reason,
                now=current_time,
            )
            return BalanceAdjustmentResult(status="updated", balance=new_balance)

    def admin_set_mining_cap(
        self,
        admin_id: int,
        target_user_id: int,
        mining_cap: Decimal,
        now: Optional[int] = None,
    ) -> Optional[User]:
        if mining_cap <= ZERO or not mining_cap.is_finite():
            raise ValueError("سقف ماین باید یک مقدار مثبت باشد.")
        current_time = int(time.time()) if now is None else int(now)
        with self._transaction() as connection:
            user = self._get_user(connection, target_user_id)
            if not user:
                return None
            connection.execute(
                "UPDATE users SET mining_cap = ? WHERE user_id = ?",
                (_decimal_to_db(mining_cap), target_user_id),
            )
            self._add_admin_log(
                connection,
                admin_id=admin_id,
                target_user_id=target_user_id,
                action="mining_cap_changed",
                amount=mining_cap,
                reason=f"old_cap={_decimal_to_db(user.mining_cap)}",
                now=current_time,
            )
            return self._get_user(connection, target_user_id)

    def admin_set_speed_multiplier(
        self,
        admin_id: int,
        target_user_id: int,
        speed_multiplier: Decimal,
        now: Optional[int] = None,
    ) -> Optional[User]:
        if speed_multiplier <= ZERO or not speed_multiplier.is_finite():
            raise ValueError("ضریب سرعت باید یک مقدار مثبت باشد.")
        current_time = int(time.time()) if now is None else int(now)
        with self._transaction() as connection:
            user = self._get_user(connection, target_user_id)
            if not user:
                return None
            connection.execute(
                "UPDATE users SET speed_multiplier = ? WHERE user_id = ?",
                (_decimal_to_db(speed_multiplier), target_user_id),
            )
            self._add_admin_log(
                connection,
                admin_id=admin_id,
                target_user_id=target_user_id,
                action="speed_multiplier_changed",
                amount=speed_multiplier,
                reason=f"old_multiplier={_decimal_to_db(user.speed_multiplier)}",
                now=current_time,
            )
            return self._get_user(connection, target_user_id)

    def get_setting(self, key: str) -> Optional[str]:
        with self._lock:
            connection = self._connect()
            try:
                row = connection.execute("SELECT value FROM settings WHERE key = ?", (key,)).fetchone()
                return str(row["value"]) if row else None
            finally:
                connection.close()

    def set_wallet_address(
        self,
        admin_id: int,
        wallet_address: str,
        now: Optional[int] = None,
    ) -> None:
        if not wallet_address:
            raise ValueError("آدرس کیف پول لازم است.")
        current_time = int(time.time()) if now is None else int(now)
        with self._transaction() as connection:
            connection.execute(
                """
                INSERT INTO settings (key, value) VALUES ('tron_wallet_address', ?)
                ON CONFLICT(key) DO UPDATE SET value = excluded.value
                """,
                (wallet_address,),
            )
            self._add_admin_log(
                connection,
                admin_id=admin_id,
                target_user_id=None,
                action="tron_wallet_address_changed",
                amount=None,
                reason=wallet_address,
                now=current_time,
            )
