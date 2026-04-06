from django.contrib import admin
from django.contrib.auth.admin import UserAdmin as BaseUserAdmin
from django.utils.html import format_html
from .models import (
    User, Asset, PriceSnapshot, Portfolio, Holding,
    BalanceAdjustment, WalletAddress, TradingPair, Transaction,
    StakingPool, StakingPosition, AIModel, AIModelTag, Alert,
)


# ─────────────────────────────────────────────
# USER
# ─────────────────────────────────────────────

@admin.register(User)
class UserAdmin(BaseUserAdmin):
    list_display    = ("email", "full_name", "country", "kyc_badge", "date_joined", "is_active")
    list_filter     = ("kyc_status", "country", "is_active", "is_staff")
    search_fields   = ("email", "first_name", "last_name", "id_number")
    ordering        = ("-date_joined",)
    readonly_fields = ("id", "date_joined", "last_login")

    fieldsets = BaseUserAdmin.fieldsets + (
        ("Profile", {"fields": ("country", "date_of_birth", "experience")}),
        ("KYC",     {"fields": ("kyc_status", "id_type", "id_number")}),
    )

    @admin.display(description="Name")
    def full_name(self, obj):
        return obj.get_full_name() or "—"

    @admin.display(description="KYC")
    def kyc_badge(self, obj):
        colors = {
            "verified": "#16a34a", "pending": "#d97706",
            "review":   "#0284c7", "rejected": "#dc2626",
        }
        color = colors.get(obj.kyc_status, "#8a8680")
        return format_html(
            '<span style="background:{};color:#fff;padding:2px 8px;'
            'border-radius:20px;font-size:11px">{}</span>',
            color, obj.get_kyc_status_display(),
        )


# ─────────────────────────────────────────────
# ASSET
# ─────────────────────────────────────────────

@admin.register(Asset)
class AssetAdmin(admin.ModelAdmin):
    list_display  = ("color_swatch", "symbol", "name", "coingecko_id", "is_stakeable", "is_active")
    list_filter   = ("is_active", "is_stakeable")
    search_fields = ("symbol", "name", "coingecko_id")
    ordering      = ("symbol",)

    @admin.display(description="")
    def color_swatch(self, obj):
        if not obj.color_hex:
            return "—"
        return format_html(
            '<span style="display:inline-block;width:14px;height:14px;'
            'border-radius:3px;background:{};vertical-align:middle"></span>',
            obj.color_hex,
        )


# ─────────────────────────────────────────────
# PRICE SNAPSHOT
# ─────────────────────────────────────────────

@admin.register(PriceSnapshot)
class PriceSnapshotAdmin(admin.ModelAdmin):
    list_display    = ("asset", "price_usd", "change_24h_display", "volume_24h", "timestamp")
    list_filter     = ("asset",)
    search_fields   = ("asset__symbol",)
    ordering        = ("-timestamp",)
    readonly_fields = ("timestamp",)

    @admin.display(description="24h Change")
    def change_24h_display(self, obj):
        if obj.change_24h is None:
            return "—"
        color = "#16a34a" if obj.change_24h >= 0 else "#dc2626"
        sign  = "+" if obj.change_24h >= 0 else ""
        return format_html(
            '<span style="color:{};font-weight:600">{}{} %</span>',
            color, sign, obj.change_24h,
        )


# ─────────────────────────────────────────────
# PORTFOLIO + HOLDING (inline)
# ─────────────────────────────────────────────

class HoldingInline(admin.TabularInline):
    model           = Holding
    extra           = 0
    fields          = ("asset", "quantity")
    readonly_fields = ()


@admin.register(Portfolio)
class PortfolioAdmin(admin.ModelAdmin):
    list_display  = ("name", "user", "created_at")
    search_fields = ("user__email", "name")
    inlines       = [HoldingInline]


# ─────────────────────────────────────────────
# BALANCE ADJUSTMENT LOG  ← NEW
# ─────────────────────────────────────────────

@admin.register(BalanceAdjustment)
class BalanceAdjustmentAdmin(admin.ModelAdmin):
    """
    Read-only view of the balance ledger. Admins can create corrections via
    the admin form; all other rows arrive from automated system processes.
    Rows are never deleted or modified — the DB trigger enforces this.
    """

    list_display = (
        "created_at", "user", "asset_with_color",
        "adjustment_type_badge", "delta_display",
        "running_balance", "usd_value_at_time",
        "description", "created_by",
    )
    list_filter  = ("adjustment_type", "asset", "created_at")
    search_fields = ("user__email", "description", "created_by")
    ordering      = ("-created_at",)
    date_hierarchy = "created_at"

    # Most fields are set programmatically — lock them down in the admin.
    readonly_fields = (
        "id", "user", "asset", "transaction", "staking_position",
        "adjustment_type", "delta", "running_balance",
        "usd_value_at_time", "created_by", "created_at",
    )

    # Only admins can create manual adjustments via this special fieldset.
    # For ADDING a new row: unlock description + adjustment_type + delta.
    def get_readonly_fields(self, request, obj=None):
        if obj:
            # Existing row → everything is read-only (immutable ledger).
            return [f.name for f in self.model._meta.fields]
        # New row → allow the writable fields needed for a manual correction.
        return ("id", "created_at", "running_balance")

    fieldsets = (
        ("Who / What", {
            "fields": ("id", "user", "asset"),
        }),
        ("Adjustment", {
            "fields": (
                "adjustment_type", "delta", "running_balance",
                "usd_value_at_time", "description",
            ),
        }),
        ("Source Event", {
            "fields": ("transaction", "staking_position"),
            "classes": ("collapse",),
        }),
        ("Audit", {
            "fields": ("created_by", "created_at"),
            "classes": ("collapse",),
        }),
    )

    # ── Custom column renderers ──────────────────────────────

    @admin.display(description="Asset")
    def asset_with_color(self, obj):
        swatch = ""
        if obj.asset.color_hex:
            swatch = (
                f'<span style="display:inline-block;width:10px;height:10px;'
                f'border-radius:50%;background:{obj.asset.color_hex};'
                f'margin-right:5px;vertical-align:middle"></span>'
            )
        return format_html("{}{}", swatch, obj.asset.symbol)

    @admin.display(description="Type")
    def adjustment_type_badge(self, obj):
        credit_types = {
            "trade_buy", "staking_reward", "deposit",
            "referral_bonus", "airdrop", "swap_in", "system_credit",
        }
        is_credit = obj.adjustment_type in credit_types
        color = "#16a34a" if is_credit else "#dc2626"
        return format_html(
            '<span style="background:{};color:#fff;padding:2px 8px;'
            'border-radius:20px;font-size:11px;white-space:nowrap">{}</span>',
            color, obj.get_adjustment_type_display(),
        )

    @admin.display(description="Delta")
    def delta_display(self, obj):
        is_credit = obj.delta > 0
        color = "#16a34a" if is_credit else "#dc2626"
        sign  = "+" if is_credit else ""
        return format_html(
            '<strong style="color:{};font-family:monospace">{}{}</strong>',
            color, sign, obj.delta,
        )

    # ── Prevent bulk-delete from the list view ──────────────
    def has_delete_permission(self, request, obj=None):
        return False  # Enforce append-only via the admin as well.


# ─────────────────────────────────────────────
# TRADING PAIR
# ─────────────────────────────────────────────

@admin.register(TradingPair)
class TradingPairAdmin(admin.ModelAdmin):
    list_display  = ("__str__", "maker_fee", "taker_fee", "min_order", "is_active")
    list_filter   = ("is_active",)
    search_fields = ("base_asset__symbol", "quote_asset__symbol")


# ─────────────────────────────────────────────
# TRANSACTION
# ─────────────────────────────────────────────

@admin.register(Transaction)
class TransactionAdmin(admin.ModelAdmin):
    list_display    = (
        "id_short", "user", "tx_type", "quantity",
        "price_usd", "total_usd", "status_badge", "created_at",
    )
    list_filter     = ("tx_type", "status", "created_at")
    search_fields   = ("user__email", "id")
    ordering        = ("-created_at",)
    readonly_fields = ("id", "created_at", "completed_at")
    date_hierarchy  = "created_at"

    @admin.display(description="ID")
    def id_short(self, obj):
        return str(obj.id)[:8] + "…"

    @admin.display(description="Status")
    def status_badge(self, obj):
        colors = {
            "completed": "#16a34a", "pending": "#d97706",
            "failed":    "#dc2626", "cancelled": "#8a8680",
        }
        color = colors.get(obj.status, "#8a8680")
        return format_html(
            '<span style="background:{};color:#fff;padding:2px 8px;'
            'border-radius:20px;font-size:11px">{}</span>',
            color, obj.get_status_display(),
        )


# ─────────────────────────────────────────────
# STAKING POOL
# ─────────────────────────────────────────────

@admin.register(StakingPool)
class StakingPoolAdmin(admin.ModelAdmin):
    list_display  = (
        "name", "asset", "lock_type", "lock_days",
        "apy_display", "capacity_pct_display", "is_active",
    )
    list_filter   = ("lock_type", "is_active", "asset")
    search_fields = ("name", "asset__symbol")
    ordering      = ("-apy",)

    @admin.display(description="APY")
    def apy_display(self, obj):
        return format_html('<strong style="color:#16a34a">{} %</strong>', obj.apy)

    @admin.display(description="Capacity")
    def capacity_pct_display(self, obj):
        pct = obj.capacity_pct
        if pct is None:
            return "Unlimited"
        color = "#dc2626" if pct >= 90 else "#d97706" if pct >= 70 else "#16a34a"
        return format_html('<span style="color:{}">{:.1f}%</span>', color, pct)


# ─────────────────────────────────────────────
# STAKING POSITION
# ─────────────────────────────────────────────

@admin.register(StakingPosition)
class StakingPositionAdmin(admin.ModelAdmin):
    list_display    = (
        "user", "pool", "staked_amount",
        "rewards_earned", "status_badge", "started_at", "matures_at",
    )
    list_filter     = ("status", "pool__asset")
    search_fields   = ("user__email",)
    ordering        = ("-started_at",)
    readonly_fields = ("id", "started_at", "withdrawn_at")

    @admin.display(description="Status")
    def status_badge(self, obj):
        colors = {"active": "#16a34a", "matured": "#d97706", "withdrawn": "#8a8680"}
        color  = colors.get(obj.status, "#8a8680")
        return format_html(
            '<span style="background:{};color:#fff;padding:2px 8px;'
            'border-radius:20px;font-size:11px">{}</span>',
            color, obj.get_status_display(),
        )


# ─────────────────────────────────────────────
# AI MODEL
# ─────────────────────────────────────────────

class AIModelTagInline(admin.TabularInline):
    model  = AIModelTag
    extra  = 1
    fields = ("label",)


@admin.register(AIModel)
class AIModelAdmin(admin.ModelAdmin):
    list_display    = (
        "icon_emoji", "name", "version", "category",
        "status_badge", "avg_latency_ms", "uptime_pct", "is_featured",
    )
    list_filter     = ("status", "category", "is_featured")
    search_fields   = ("name", "version")
    ordering        = ("-is_featured", "name")
    readonly_fields = ("id", "created_at", "updated_at")
    inlines         = [AIModelTagInline]

    fieldsets = (
        (None,           {"fields": ("id", "name", "version", "icon_emoji", "is_featured")}),
        ("Classification", {"fields": ("category", "status")}),
        ("Description",  {"fields": ("description",)}),
        ("Performance",  {"fields": ("metrics", "avg_latency_ms", "uptime_pct", "last_deployed")}),
        ("Timestamps",   {"fields": ("created_at", "updated_at"), "classes": ("collapse",)}),
    )

    @admin.display(description="Status")
    def status_badge(self, obj):
        colors = {
            "live":    "#16a34a", "beta":    "#d97706",
            "preview": "#0284c7", "retired": "#8a8680",
        }
        color = colors.get(obj.status, "#8a8680")
        return format_html(
            '<span style="background:{};color:#fff;padding:2px 8px;'
            'border-radius:20px;font-size:11px">{}</span>',
            color, obj.get_status_display(),
        )


# ─────────────────────────────────────────────
# ALERT
# ─────────────────────────────────────────────

@admin.register(Alert)
class AlertAdmin(admin.ModelAdmin):
    list_display    = (
        "title", "user", "alert_type",
        "severity_badge", "is_read", "is_dismissed", "created_at",
    )
    list_filter     = ("severity", "alert_type", "is_read", "is_dismissed")
    search_fields   = ("title", "user__email", "description")
    ordering        = ("-created_at",)
    readonly_fields = ("id", "created_at")
    actions         = ["mark_dismissed"]

    @admin.display(description="Severity")
    def severity_badge(self, obj):
        colors = {"info": "#0284c7", "warning": "#d97706", "critical": "#dc2626"}
        color  = colors.get(obj.severity, "#8a8680")
        return format_html(
            '<span style="background:{};color:#fff;padding:2px 8px;'
            'border-radius:20px;font-size:11px">{}</span>',
            color, obj.get_severity_display(),
        )

    @admin.action(description="Dismiss selected alerts")
    def mark_dismissed(self, request, queryset):
        count = queryset.update(is_dismissed=True)
        self.message_user(request, f"{count} alert(s) dismissed.")

# ─────────────────────────────────────────────
# WALLET ADDRESS
# ─────────────────────────────────────────────

@admin.register(WalletAddress)
class WalletAddressAdmin(admin.ModelAdmin):
    list_display  = ("user", "network", "asset", "address_short", "is_active", "created_at")
    list_filter   = ("network", "is_active", "asset")
    search_fields = ("user__email", "address")
    ordering      = ("-created_at",)
    readonly_fields = ("id", "created_at")

    @admin.display(description="Address")
    def address_short(self, obj):
        return obj.address[:14] + "…" + obj.address[-6:]