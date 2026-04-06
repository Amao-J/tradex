from django.db import models
from django.contrib.auth.models import AbstractUser
from django.core.validators import MinValueValidator
from decimal import Decimal
import uuid


# ─────────────────────────────────────────────
# USER
# ─────────────────────────────────────────────

class User(AbstractUser):
    class KYCStatus(models.TextChoices):
        PENDING  = "pending",  "Pending"
        REVIEW   = "review",   "Under Review"
        VERIFIED = "verified", "Verified"
        REJECTED = "rejected", "Rejected"

    class ExperienceLevel(models.TextChoices):
        BEGINNER     = "beginner",     "Beginner"
        INTERMEDIATE = "intermediate", "Intermediate"
        ADVANCED     = "advanced",     "Advanced"

    id            = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    email         = models.EmailField(unique=True)
    country       = models.CharField(max_length=100, blank=True)
    kyc_status    = models.CharField(max_length=20, choices=KYCStatus.choices, default=KYCStatus.PENDING)
    experience    = models.CharField(max_length=20, choices=ExperienceLevel.choices, blank=True)
    date_of_birth = models.DateField(null=True, blank=True)
    id_type       = models.CharField(max_length=50, blank=True)
    id_number     = models.CharField(max_length=100, blank=True)
    created_at    = models.DateTimeField(auto_now_add=True)
    updated_at    = models.DateTimeField(auto_now=True)

    USERNAME_FIELD  = "email"
    REQUIRED_FIELDS = ["username"]

    def __str__(self):
        return self.email

    class Meta:
        ordering = ["-created_at"]


# ─────────────────────────────────────────────
# ASSET  (BTC, ETH, SOL …)
# ─────────────────────────────────────────────

class Asset(models.Model):
    id           = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    coingecko_id = models.CharField(max_length=100, unique=True)
    symbol       = models.CharField(max_length=20, unique=True)
    name         = models.CharField(max_length=100)
    icon_emoji   = models.CharField(max_length=10, blank=True)
    color_hex    = models.CharField(max_length=7, blank=True)   # e.g. "#f7931a"
    is_active    = models.BooleanField(default=True)
    is_stakeable = models.BooleanField(default=False)
    created_at   = models.DateTimeField(auto_now_add=True)

    def __str__(self):
        return f"{self.symbol} — {self.name}"

    class Meta:
        ordering = ["symbol"]


# ─────────────────────────────────────────────
# PRICE SNAPSHOT
# ─────────────────────────────────────────────

class PriceSnapshot(models.Model):
    id         = models.BigAutoField(primary_key=True)
    asset      = models.ForeignKey(Asset, on_delete=models.CASCADE, related_name="price_snapshots")
    price_usd  = models.DecimalField(max_digits=24, decimal_places=8)
    market_cap = models.DecimalField(max_digits=30, decimal_places=2, null=True, blank=True)
    volume_24h = models.DecimalField(max_digits=30, decimal_places=2, null=True, blank=True)
    change_24h = models.DecimalField(max_digits=8,  decimal_places=4, null=True, blank=True)
    timestamp  = models.DateTimeField(db_index=True)

    def __str__(self):
        return f"{self.asset.symbol} @ ${self.price_usd} ({self.timestamp:%Y-%m-%d %H:%M})"

    class Meta:
        ordering = ["-timestamp"]
        indexes  = [models.Index(fields=["asset", "-timestamp"])]


# ─────────────────────────────────────────────
# PORTFOLIO + HOLDING
# ─────────────────────────────────────────────

class Portfolio(models.Model):
    id         = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    user       = models.ForeignKey(User, on_delete=models.CASCADE, related_name="portfolios")
    name       = models.CharField(max_length=100, default="Main Portfolio")
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    def __str__(self):
        return f"{self.user.email} — {self.name}"

    @property
    def total_value_usd(self):
        return sum(h.current_value_usd for h in self.holdings.select_related("asset").all())


class Holding(models.Model):
    """Current balance of an asset inside a portfolio."""
    id        = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    portfolio = models.ForeignKey(Portfolio, on_delete=models.CASCADE, related_name="holdings")
    asset     = models.ForeignKey(Asset, on_delete=models.PROTECT, related_name="holdings")
    quantity  = models.DecimalField(
        max_digits=30, decimal_places=18,
        validators=[MinValueValidator(Decimal("0"))]
    )

    class Meta:
        unique_together = ("portfolio", "asset")

    def __str__(self):
        return f"{self.portfolio.user.email}: {self.quantity} {self.asset.symbol}"

    @property
    def current_value_usd(self):
        snap = self.asset.price_snapshots.first()
        if snap:
            return float(self.quantity) * float(snap.price_usd)
        return 0.0


# ─────────────────────────────────────────────
# BALANCE ADJUSTMENT LOG  ← NEW
# ─────────────────────────────────────────────

class BalanceAdjustment(models.Model):
    """
    Append-only ledger of every balance change for a user/asset pair.

    Every credit or debit — trade fill, staking reward, deposit, fee,
    admin correction — writes exactly one row here. Users see this as
    their transparent "why did my balance change?" history.

    The `running_balance` column records the holding's quantity immediately
    AFTER this row was applied, so you can reconstruct the balance at any
    past point without replaying all transactions.

    Design notes
    ────────────
    • Never update or delete rows (enforce via DB trigger; see migration).
    • Keep `delta` signed: positive = credit, negative = debit.
    • Link to the originating Transaction or StakingPosition when available
      so users can drill into the source event.
    • `description` is the human-readable string shown verbatim in the UI.
    """

    class AdjustmentType(models.TextChoices):
        # ── Credits (+) ──────────────────────────────
        TRADE_BUY         = "trade_buy",         "Trade — Buy"
        STAKING_REWARD    = "staking_reward",    "Staking Reward Credit"
        DEPOSIT           = "deposit",           "Deposit"
        REFERRAL_BONUS    = "referral_bonus",    "Referral Bonus"
        AIRDROP           = "airdrop",           "Airdrop"
        SWAP_IN           = "swap_in",           "Swap — Received"
        SYSTEM_CREDIT     = "system_credit",     "System Credit"
        # ── Debits (−) ───────────────────────────────
        TRADE_SELL        = "trade_sell",        "Trade — Sell"
        STAKE_LOCK        = "stake_lock",        "Staking — Locked"
        UNSTAKE_RELEASE   = "unstake_release",   "Unstaking — Released"
        WITHDRAWAL        = "withdrawal",        "Withdrawal"
        FEE_DEDUCTION     = "fee_deduction",     "Fee Deduction"
        SWAP_OUT          = "swap_out",          "Swap — Sent"
        SYSTEM_CORRECTION = "system_correction", "System Correction"
        ADMIN_ADJUSTMENT  = "admin_adjustment",  "Admin Manual Adjustment"

    # Convenience set for filtering credits
    CREDIT_TYPES = frozenset({
        AdjustmentType.TRADE_BUY,    AdjustmentType.STAKING_REWARD,
        AdjustmentType.DEPOSIT,      AdjustmentType.REFERRAL_BONUS,
        AdjustmentType.AIRDROP,      AdjustmentType.SWAP_IN,
        AdjustmentType.SYSTEM_CREDIT,
    })

    id               = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)

    # Who and what
    user             = models.ForeignKey(
        User, on_delete=models.CASCADE, related_name="balance_adjustments"
    )
    asset            = models.ForeignKey(
        Asset, on_delete=models.PROTECT, related_name="balance_adjustments"
    )

    # Optional back-links to the originating event (for drill-down)
    transaction      = models.ForeignKey(
        "Transaction", on_delete=models.SET_NULL,
        null=True, blank=True, related_name="balance_adjustments"
    )
    staking_position = models.ForeignKey(
        "StakingPosition", on_delete=models.SET_NULL,
        null=True, blank=True, related_name="balance_adjustments"
    )

    adjustment_type  = models.CharField(max_length=30, choices=AdjustmentType.choices)

    # Core accounting fields
    delta            = models.DecimalField(
        max_digits=36, decimal_places=18,
        help_text="Signed quantity change. Positive = credit, negative = debit."
    )
    running_balance  = models.DecimalField(
        max_digits=36, decimal_places=18,
        help_text="Holding quantity immediately after this adjustment."
    )

    # User-facing explanation (shown verbatim in the dashboard)
    description      = models.CharField(
        max_length=255,
        help_text=(
            'Plain-English reason shown to the user. '
            'e.g. "Staking Reward Credit — SOL 90-Day Pool (Day 32/90)"'
        )
    )

    # USD equivalent at the moment of writing (informational only)
    usd_value_at_time = models.DecimalField(
        max_digits=24, decimal_places=2, null=True, blank=True,
        help_text="USD value of the delta at the time of the adjustment."
    )

    # Internal audit: which system process / admin wrote this row
    created_by       = models.CharField(
        max_length=100, blank=True,
        help_text="Username of the admin or name of the system process that created this row."
    )
    created_at       = models.DateTimeField(auto_now_add=True, db_index=True)

    class Meta:
        ordering = ["-created_at"]
        indexes  = [
            models.Index(fields=["user", "-created_at"]),
            models.Index(fields=["user", "asset", "-created_at"]),
            models.Index(fields=["adjustment_type"]),
        ]
        verbose_name        = "Balance Adjustment"
        verbose_name_plural = "Balance Adjustments"

    def __str__(self):
        sign = "+" if self.delta >= 0 else ""
        return (
            f"{self.user.email} | {self.asset.symbol} "
            f"{sign}{self.delta} | {self.get_adjustment_type_display()}"
        )

    @property
    def is_credit(self):
        return self.delta > 0

    @property
    def direction_label(self):
        return "Credit" if self.is_credit else "Debit"


# ─────────────────────────────────────────────
# TRADING PAIR
# ─────────────────────────────────────────────

class TradingPair(models.Model):
    id          = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    base_asset  = models.ForeignKey(Asset, on_delete=models.PROTECT, related_name="base_pairs")
    quote_asset = models.ForeignKey(Asset, on_delete=models.PROTECT, related_name="quote_pairs")
    is_active   = models.BooleanField(default=True)
    maker_fee   = models.DecimalField(max_digits=6, decimal_places=4, default=Decimal("0.0000"))
    taker_fee   = models.DecimalField(max_digits=6, decimal_places=4, default=Decimal("0.0000"))
    min_order   = models.DecimalField(max_digits=20, decimal_places=8, default=Decimal("0.00001"))

    def __str__(self):
        return f"{self.base_asset.symbol}/{self.quote_asset.symbol}"

    class Meta:
        unique_together = ("base_asset", "quote_asset")


# ─────────────────────────────────────────────
# TRANSACTION
# ─────────────────────────────────────────────

class Transaction(models.Model):
    class TxType(models.TextChoices):
        BUY     = "buy",     "Buy"
        SELL    = "sell",    "Sell"
        SWAP    = "swap",    "Swap"
        STAKE   = "stake",   "Stake"
        UNSTAKE = "unstake", "Unstake"
        REWARD  = "reward",  "Staking Reward"

    class Status(models.TextChoices):
        PENDING   = "pending",   "Pending"
        COMPLETED = "completed", "Completed"
        FAILED    = "failed",    "Failed"
        CANCELLED = "cancelled", "Cancelled"

    id           = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    user         = models.ForeignKey(User, on_delete=models.CASCADE, related_name="transactions")
    portfolio    = models.ForeignKey(
        Portfolio, on_delete=models.CASCADE,
        related_name="transactions", null=True, blank=True
    )
    pair         = models.ForeignKey(TradingPair, on_delete=models.PROTECT, null=True, blank=True)
    tx_type      = models.CharField(max_length=10, choices=TxType.choices)
    status       = models.CharField(max_length=12, choices=Status.choices, default=Status.PENDING)
    quantity     = models.DecimalField(
        max_digits=30, decimal_places=18,
        validators=[MinValueValidator(Decimal("0"))]
    )
    price_usd    = models.DecimalField(max_digits=24, decimal_places=8)
    total_usd    = models.DecimalField(max_digits=24, decimal_places=2)
    fee_usd      = models.DecimalField(max_digits=14, decimal_places=6, default=Decimal("0"))
    notes        = models.TextField(blank=True)
    created_at   = models.DateTimeField(auto_now_add=True)
    completed_at = models.DateTimeField(null=True, blank=True)

    def __str__(self):
        return f"{self.tx_type.upper()} {self.quantity} @ ${self.price_usd} [{self.status}]"

    class Meta:
        ordering = ["-created_at"]
        indexes  = [
            models.Index(fields=["user", "-created_at"]),
            models.Index(fields=["status"]),
        ]


# ─────────────────────────────────────────────
# STAKING POOL
# ─────────────────────────────────────────────

class StakingPool(models.Model):
    class LockType(models.TextChoices):
        FLEXIBLE = "flexible", "Flexible"
        FIXED    = "fixed",    "Fixed Term"

    id           = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    asset        = models.ForeignKey(Asset, on_delete=models.PROTECT, related_name="staking_pools")
    name         = models.CharField(max_length=100)
    lock_type    = models.CharField(max_length=10, choices=LockType.choices, default=LockType.FLEXIBLE)
    lock_days    = models.PositiveIntegerField(default=0)
    apy          = models.DecimalField(max_digits=6, decimal_places=2)
    min_stake    = models.DecimalField(max_digits=20, decimal_places=8, default=Decimal("10"))
    max_stake    = models.DecimalField(max_digits=20, decimal_places=8, null=True, blank=True)
    capacity     = models.DecimalField(max_digits=30, decimal_places=8, null=True, blank=True)
    total_staked = models.DecimalField(max_digits=30, decimal_places=8, default=Decimal("0"))
    is_active    = models.BooleanField(default=True)
    created_at   = models.DateTimeField(auto_now_add=True)

    def __str__(self):
        return f"{self.asset.symbol} — {self.name} ({self.apy}% APY)"

    @property
    def capacity_pct(self):
        if self.capacity and self.capacity > 0:
            return min(100, float(self.total_staked / self.capacity * 100))
        return None

    class Meta:
        ordering = ["-apy"]


# ─────────────────────────────────────────────
# STAKING POSITION
# ─────────────────────────────────────────────

class StakingPosition(models.Model):
    class PositionStatus(models.TextChoices):
        ACTIVE    = "active",    "Active"
        MATURED   = "matured",   "Matured"
        WITHDRAWN = "withdrawn", "Withdrawn"

    id               = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    user             = models.ForeignKey(User, on_delete=models.CASCADE, related_name="staking_positions")
    pool             = models.ForeignKey(StakingPool, on_delete=models.PROTECT, related_name="positions")
    staked_amount    = models.DecimalField(
        max_digits=30, decimal_places=18,
        validators=[MinValueValidator(Decimal("0"))]
    )
    rewards_earned   = models.DecimalField(max_digits=30, decimal_places=18, default=Decimal("0"))
    status           = models.CharField(
        max_length=12, choices=PositionStatus.choices, default=PositionStatus.ACTIVE
    )
    started_at       = models.DateTimeField(auto_now_add=True)
    matures_at       = models.DateTimeField(null=True, blank=True)
    withdrawn_at     = models.DateTimeField(null=True, blank=True)

    def __str__(self):
        return f"{self.user.email} staked {self.staked_amount} {self.pool.asset.symbol}"

    @property
    def days_elapsed(self):
        from django.utils import timezone
        return (timezone.now() - self.started_at).days

    @property
    def progress_pct(self):
        if self.pool.lock_days and self.matures_at:
            return min(100, self.days_elapsed / self.pool.lock_days * 100)
        return None

    class Meta:
        ordering = ["-started_at"]
        indexes  = [models.Index(fields=["user", "status"])]


# ─────────────────────────────────────────────
# AI MODEL REGISTRY
# ─────────────────────────────────────────────

class AIModel(models.Model):
    class ModelStatus(models.TextChoices):
        LIVE    = "live",    "Live"
        BETA    = "beta",    "Beta"
        PREVIEW = "preview", "Preview"
        RETIRED = "retired", "Retired"

    class ModelCategory(models.TextChoices):
        TRADING   = "trading",   "Trading"
        STAKING   = "staking",   "Staking"
        RISK      = "risk",      "Risk"
        NLP       = "nlp",       "NLP / Sentiment"
        PORTFOLIO = "portfolio", "Portfolio"

    id             = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    name           = models.CharField(max_length=100)
    version        = models.CharField(max_length=20)
    category       = models.CharField(max_length=20, choices=ModelCategory.choices)
    status         = models.CharField(max_length=10, choices=ModelStatus.choices, default=ModelStatus.LIVE)
    description    = models.TextField(blank=True)
    icon_emoji     = models.CharField(max_length=10, blank=True)
    is_featured    = models.BooleanField(default=False)
    metrics        = models.JSONField(default=dict, blank=True)
    avg_latency_ms = models.FloatField(null=True, blank=True)
    uptime_pct     = models.DecimalField(max_digits=6, decimal_places=3, null=True, blank=True)
    last_deployed  = models.DateTimeField(null=True, blank=True)
    created_at     = models.DateTimeField(auto_now_add=True)
    updated_at     = models.DateTimeField(auto_now=True)

    def __str__(self):
        return f"{self.name} v{self.version} [{self.status}]"

    class Meta:
        ordering = ["-is_featured", "name"]


class AIModelTag(models.Model):
    model = models.ForeignKey(AIModel, on_delete=models.CASCADE, related_name="tags")
    label = models.CharField(max_length=50)

    def __str__(self):
        return self.label


# ─────────────────────────────────────────────
# ALERT
# ─────────────────────────────────────────────

class Alert(models.Model):
    class Severity(models.TextChoices):
        INFO     = "info",     "Info"
        WARNING  = "warning",  "Warning"
        CRITICAL = "critical", "Critical"

    class AlertType(models.TextChoices):
        PRICE   = "price",   "Price Alert"
        RISK    = "risk",    "Risk / Fraud"
        SYSTEM  = "system",  "System"
        STAKING = "staking", "Staking"
        MODEL   = "model",   "AI Model"

    id           = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    user         = models.ForeignKey(
        User, on_delete=models.CASCADE, related_name="alerts", null=True, blank=True
    )
    alert_type   = models.CharField(max_length=20, choices=AlertType.choices)
    severity     = models.CharField(max_length=10, choices=Severity.choices, default=Severity.INFO)
    title        = models.CharField(max_length=200)
    description  = models.TextField(blank=True)
    is_read      = models.BooleanField(default=False)
    is_dismissed = models.BooleanField(default=False)
    created_at   = models.DateTimeField(auto_now_add=True)

    def __str__(self):
        return f"[{self.severity.upper()}] {self.title}"

    class Meta:
        ordering = ["-created_at"]
        indexes  = [models.Index(fields=["user", "is_dismissed", "-created_at"])]

# ─────────────────────────────────────────────
# WALLET ADDRESS
# ─────────────────────────────────────────────

class WalletAddress(models.Model):
    """
    A per-user, per-network deposit address.
    Generated once and reused — never reassigned to a different user.
    """

    class Network(models.TextChoices):
        BITCOIN  = "BTC", "Bitcoin Network"
        ETHEREUM = "ETH", "Ethereum (ERC-20)"
        SOLANA   = "SOL", "Solana Network"
        BNB      = "BNB", "BNB Smart Chain"

    id         = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    user       = models.ForeignKey(
        User, on_delete=models.CASCADE, related_name="wallet_addresses"
    )
    asset      = models.ForeignKey(
        Asset, on_delete=models.PROTECT, related_name="wallet_addresses"
    )
    network    = models.CharField(max_length=10, choices=Network.choices)
    address    = models.CharField(max_length=200, unique=True)
    is_active  = models.BooleanField(default=True)
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        unique_together = ("user", "network")
        indexes = [models.Index(fields=["user", "network"])]

    def __str__(self):
        return f"{self.user.email} | {self.network} | {self.address[:16]}…"