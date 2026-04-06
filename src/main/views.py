"""
views.py — Portfolio Dashboard
Serves data for:
  1. Balance Adjustment Log  (paginated, filterable)
  2. Asset Allocation Chart  (pie chart data from Holdings + Asset.color_hex)
  3. Price Snapshot Sparklines  (24h OHLC points per held asset)
"""

from decimal import Decimal
import json
from datetime import timedelta
import base58

from django import forms
from django.contrib.auth import authenticate, login, logout
from django.contrib.auth.decorators import login_required
from django.contrib.auth.forms import AuthenticationForm, UserCreationForm
from django.core.paginator import Paginator
from django.db.models import Prefetch
from django.http import JsonResponse
from django.shortcuts import get_object_or_404, redirect, render
from django.urls import reverse
from django.utils import timezone
from django.views.decorators.http import require_GET, require_POST
from django.db import transaction
from .models import (
    Asset,
    BalanceAdjustment,
    Holding,
    Portfolio,
    PriceSnapshot,
    User,
    WalletAddress,
)

class SignUpForm(UserCreationForm):
    first_name = forms.CharField(required=False)
    last_name = forms.CharField(required=False)

    class Meta:
        model = User
        fields = (
            "email",
            "username",
            "first_name",
            "last_name",
            "password1",
            "password2",
        )

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        # Add CSS class to all form inputs for consistent styling
        for field in self.fields.values():
            field.widget.attrs.update({"class": "af-input"})
        
        self.fields["password1"].help_text = (
            "Minimum 8 characters, including uppercase, lowercase, number, and special character."
        )
        self.fields["password2"].help_text = "Enter the same password again for verification."

    def clean_password1(self):
        password = self.cleaned_data.get("password1")
        if not password:
            return password

        requirements = []
        if len(password) < 8:
            requirements.append("at least 8 characters")
        if not any(c.islower() for c in password):
            requirements.append("a lowercase letter")
        if not any(c.isupper() for c in password):
            requirements.append("an uppercase letter")
        if not any(c.isdigit() for c in password):
            requirements.append("a number")
        if not any(c in "!@#$%^&*()-_=+[]{}|;:'\",.<>/?`~" for c in password):
            requirements.append("a special character")

        if requirements:
            raise forms.ValidationError(
                "Password must contain %s." % ", ".join(requirements)
            )

        return password


def home(request):
    if request.user.is_authenticated:
        return redirect("app-shell")
    return render(request, "index.html")


def signup_view(request):
    if request.user.is_authenticated:
        return redirect("app-shell")

    if request.method == "POST":
        form = SignUpForm(request.POST)
        if form.is_valid():
            user = form.save()
            login(request, user)
            return JsonResponse({
                "success": True,
                "user": {
                    "first_name": user.first_name,
                    "last_name": user.last_name,
                    "email": user.email,
                }
            })
        else:
            return JsonResponse({"errors": form.errors}, status=400)
    else:
        form = SignUpForm()

    return render(request, "index.html", {"form": form})


def signin_view(request):
    if request.user.is_authenticated:
        return redirect("portfolio-dashboard")

    if request.method == "POST":
        form = AuthenticationForm(request, data=request.POST)
        if form.is_valid():
            user = form.get_user()
            login(request, user)
            return JsonResponse({
                "success": True,
                "user": {
                    "first_name": user.first_name,
                    "last_name": user.last_name,
                    "email": user.email,
                }
            })
        else:
            return JsonResponse({"errors": form.errors}, status=400)
    else:
        form = AuthenticationForm()

    return render(request, "index.html", {"form": form})


def logout_view(request):
    logout(request)
    return redirect("home")


# ─────────────────────────────────────────────────────────
# MAIN DASHBOARD VIEW
# ─────────────────────────────────────────────────────────

@login_required
def portfolio_dashboard(request):
    """
    Renders the portfolio dashboard template.
    The heavy data (chart slices, sparklines, adjustments) is loaded via
    the three JSON API endpoints below so the initial page is fast.
    """
    # Resolve the portfolio to display (first one, or via ?portfolio=<uuid>)
    portfolio_id = request.GET.get("portfolio")
    qs = Portfolio.objects.filter(user=request.user).prefetch_related(
        Prefetch(
            "holdings",
            queryset=Holding.objects.select_related("asset").order_by("-asset__symbol"),
        )
    )

    if portfolio_id:
        portfolio = get_object_or_404(qs, pk=portfolio_id)
    else:
        portfolio = qs.first()

    all_portfolios = Portfolio.objects.filter(user=request.user).only("id", "name")

    return render(request, "portfolio_dashboard.html", {
        "portfolio":      portfolio,
        "all_portfolios": all_portfolios,
    })


# ─────────────────────────────────────────────────────────
# 1.  BALANCE ADJUSTMENT LOG  — JSON endpoint
# ─────────────────────────────────────────────────────────

@login_required
@require_GET
def balance_adjustments_api(request):
    """
    GET /api/portfolio/adjustments/
    Query params:
      asset      — filter by asset symbol (e.g. ?asset=BTC)
      type       — filter by adjustment_type
      direction  — "credit" | "debit"
      page       — page number (default 1)
      page_size  — rows per page (default 20, max 100)
    Returns JSON list of adjustment rows, newest first.
    """
    qs = (
        BalanceAdjustment.objects
        .filter(user=request.user)
        .select_related("asset", "transaction", "staking_position")
        .order_by("-created_at")
    )

    # ── Filters ────────────────────────────────────────────
    asset_sym = request.GET.get("asset", "").strip().upper()
    if asset_sym:
        qs = qs.filter(asset__symbol=asset_sym)

    adj_type = request.GET.get("type", "").strip()
    if adj_type:
        qs = qs.filter(adjustment_type=adj_type)

    direction = request.GET.get("direction", "").strip().lower()
    if direction == "credit":
        qs = qs.filter(delta__gt=0)
    elif direction == "debit":
        qs = qs.filter(delta__lt=0)

    # ── Pagination ──────────────────────────────────────────
    try:
        page_size = min(int(request.GET.get("page_size", 20)), 100)
    except ValueError:
        page_size = 20

    paginator   = Paginator(qs, page_size)
    page_number = request.GET.get("page", 1)
    page_obj    = paginator.get_page(page_number)

    rows = []
    for adj in page_obj:
        rows.append({
            "id":               str(adj.id),
            "asset_symbol":     adj.asset.symbol,
            "asset_name":       adj.asset.name,
            "asset_color":      adj.asset.color_hex or "#8a8680",
            "adjustment_type":  adj.adjustment_type,
            "type_label":       adj.get_adjustment_type_display(),
            "direction":        "credit" if adj.delta > 0 else "debit",
            "delta":            str(adj.delta),
            "running_balance":  str(adj.running_balance),
            "usd_value":        str(adj.usd_value_at_time) if adj.usd_value_at_time else None,
            "description":      adj.description,
            "transaction_id":   str(adj.transaction_id) if adj.transaction_id else None,
            "created_at":       adj.created_at.isoformat(),
            "created_at_human": _human_time(adj.created_at),
        })

    return JsonResponse({
        "results":    rows,
        "page":       page_obj.number,
        "num_pages":  paginator.num_pages,
        "total":      paginator.count,
        "has_next":   page_obj.has_next(),
        "has_prev":   page_obj.has_previous(),
    })


# ─────────────────────────────────────────────────────────
# 2.  ASSET ALLOCATION CHART  — JSON endpoint
# ─────────────────────────────────────────────────────────

@login_required
@require_GET
def allocation_chart_api(request):
    """
    GET /api/portfolio/allocation/?portfolio=<uuid>
    Returns pie-chart slices: one per held asset, sized by current USD value.
    Uses Asset.color_hex so the chart matches the brand palette exactly.
    """
    portfolio_id = request.GET.get("portfolio")
    portfolio    = _resolve_portfolio(request, portfolio_id)
    if portfolio is None:
        return JsonResponse({"slices": [], "total_usd": "0.00"})

    holdings = (
        portfolio.holdings
        .select_related("asset")
        .prefetch_related(
            Prefetch(
                "asset__price_snapshots",
                queryset=PriceSnapshot.objects.order_by("-timestamp")[:1],
                to_attr="_latest_snap",
            )
        )
    )

    slices     = []
    total_usd  = 0.0

    for h in holdings:
        snaps = getattr(h.asset, "_latest_snap", [])
        price = float(snaps[0].price_usd) if snaps else 0.0
        value = float(h.quantity) * price
        total_usd += value
        slices.append({
            "symbol":    h.asset.symbol,
            "name":      h.asset.name,
            "color":     h.asset.color_hex or "#8a8680",
            "quantity":  str(h.quantity),
            "price_usd": str(price),
            "value_usd": round(value, 2),
        })

    # Sort largest slice first; attach percentage
    slices.sort(key=lambda s: s["value_usd"], reverse=True)
    for s in slices:
        s["pct"] = round(s["value_usd"] / total_usd * 100, 2) if total_usd else 0.0

    return JsonResponse({
        "slices":    slices,
        "total_usd": round(total_usd, 2),
    })


# ─────────────────────────────────────────────────────────
# 3.  PRICE SNAPSHOT SPARKLINES  — JSON endpoint
# ─────────────────────────────────────────────────────────

@login_required
@require_GET
def sparklines_api(request):
    """
    GET /api/portfolio/sparklines/?portfolio=<uuid>&hours=24
    Returns up to `hours` hours of PriceSnapshot data for every asset
    the user currently holds. The frontend renders these as mini line charts.

    To avoid N+1 queries, we pull all snapshots in a single query and
    group them in Python.
    """
    portfolio_id = request.GET.get("portfolio")
    portfolio    = _resolve_portfolio(request, portfolio_id)
    if portfolio is None:
        return JsonResponse({"sparklines": []})

    try:
        hours = max(1, min(int(request.GET.get("hours", 24)), 168))  # 1 h – 7 d
    except ValueError:
        hours = 24

    # Collect asset IDs from this portfolio's holdings
    asset_ids = list(
        portfolio.holdings.values_list("asset_id", flat=True)
    )
    if not asset_ids:
        return JsonResponse({"sparklines": []})

    since = timezone.now() - timedelta(hours=hours)

    snapshots = (
        PriceSnapshot.objects
        .filter(asset_id__in=asset_ids, timestamp__gte=since)
        .select_related("asset")
        .order_by("asset_id", "timestamp")
        .values(
            "asset_id", "asset__symbol", "asset__name",
            "asset__color_hex", "price_usd", "timestamp",
        )
    )

    # Group by asset
    groups: dict = {}
    for snap in snapshots:
        aid = str(snap["asset_id"])
        if aid not in groups:
            groups[aid] = {
                "asset_id": aid,
                "symbol":   snap["asset__symbol"],
                "name":     snap["asset__name"],
                "color":    snap["asset__color_hex"] or "#8a8680",
                "points":   [],
            }
        groups[aid]["points"].append({
            "t": snap["timestamp"].isoformat(),
            "p": str(snap["price_usd"]),
        })

    # Attach 24h change % and current price to each sparkline
    sparklines = []
    for g in groups.values():
        pts = g["points"]
        if pts:
            first = float(pts[0]["p"])
            last  = float(pts[-1]["p"])
            g["current_price"] = str(last)
            g["change_pct"]    = round((last - first) / first * 100, 2) if first else 0.0
            g["is_up"]         = g["change_pct"] >= 0
        else:
            g["current_price"] = "0"
            g["change_pct"]    = 0.0
            g["is_up"]         = True
        sparklines.append(g)

    return JsonResponse({"sparklines": sparklines, "hours": hours})


# ─────────────────────────────────────────────────────────
# HELPERS
# ─────────────────────────────────────────────────────────

def _resolve_portfolio(request, portfolio_id=None):
    """Return the requested portfolio if it belongs to the user, else their first one."""
    qs = Portfolio.objects.filter(user=request.user)
    if portfolio_id:
        return qs.filter(pk=portfolio_id).first()
    return qs.first()


def _human_time(dt):
    """Return a short relative time string for display."""
    delta = timezone.now() - dt
    seconds = int(delta.total_seconds())
    if seconds < 60:
        return "just now"
    if seconds < 3600:
        m = seconds // 60
        return f"{m} min ago"
    if seconds < 86400:
        h = seconds // 3600
        return f"{h}h ago"
    d = seconds // 86400
    return f"{d}d ago"




@login_required
@require_POST
def api_wallet_deposit(request):
    try:
        data   = json.loads(request.body)
        amount = Decimal(str(data.get("amount", 0)))
    except Exception:
        return JsonResponse({"error": "Invalid request payload"}, status=400)

    if amount <= 0:
        return JsonResponse({"error": "Invalid amount"}, status=400)

    usd_asset, _ = Asset.objects.get_or_create(
        symbol="USD",
        defaults={
            "coingecko_id": "usd",          # ← was missing, causes IntegrityError
            "name":         "US Dollar",
            "color_hex":    "#16a34a",
            "is_active":    True,
        },
    )

    portfolio, _ = Portfolio.objects.get_or_create(  # ← was crashing if no portfolio
        user=request.user,
        defaults={"name": "Main Portfolio"},
    )

    holding, _ = Holding.objects.get_or_create(
        portfolio=portfolio,
        asset=usd_asset,
        defaults={"quantity": Decimal("0")},
    )

    with transaction.atomic():
        holding.quantity += amount
        holding.save()
        BalanceAdjustment.objects.create(
            user=request.user,
            asset=usd_asset,
            adjustment_type=BalanceAdjustment.AdjustmentType.DEPOSIT,
            delta=amount,
            running_balance=holding.quantity,
            description="Fiat Deposit via Bank Transfer",
            created_by="System",
        )

    return JsonResponse({"status": "success", "new_balance": str(holding.quantity)})

@login_required
@require_POST
def api_wallet_withdraw(request):
    try:
        data = json.loads(request.body)
        amount = Decimal(str(data.get("amount", 0)))
    except Exception:
        return JsonResponse({"error": "Invalid request payload"}, status=400)

    if amount <= 0:
        return JsonResponse({"error": "Invalid amount"}, status=400)

    portfolio, _ = Portfolio.objects.get_or_create(
        user=request.user,
        name="Main Portfolio",
    )

    try:
        usd_asset = Asset.objects.get(symbol="USD")
        holding = Holding.objects.get(portfolio=portfolio, asset=usd_asset)
    except Asset.DoesNotExist:
        return JsonResponse({"error": "USD asset is not configured"}, status=500)
    except Holding.DoesNotExist:
        return JsonResponse({"error": "No balance available"}, status=400)

    if amount > holding.quantity:
        return JsonResponse({"error": "Insufficient funds"}, status=400)

    with transaction.atomic():
        holding.quantity -= amount
        holding.save()

        BalanceAdjustment.objects.create(
            user=request.user,
            asset=usd_asset,
            adjustment_type=BalanceAdjustment.AdjustmentType.WITHDRAWAL,
            delta=-amount,
            running_balance=holding.quantity,
            description="Fiat Withdrawal to Bank",
            created_by="System",
        )

    return JsonResponse({"status": "success", "new_balance": str(holding.quantity)})

    


# ─────────────────────────────────────────────────────────
# WALLET — Page view
# ─────────────────────────────────────────────────────────

@login_required
def wallet_view(request):
    """
    Renders the wallet page. Passes the user's wallet addresses
    (one per network) and their USD fiat balance to the template.
    Address rows are created on first visit if they don't exist yet.
    """
    NETWORK_DEFAULTS = [
        ("BTC", "bitcoin"),
        ("ETH", "ethereum"),
        ("SOL", "solana"),
        ("BNB", "binancecoin"),
    ]

    # Ensure every address exists for this user
    for network_code, coingecko_id in NETWORK_DEFAULTS:
        asset = Asset.objects.filter(coingecko_id=coingecko_id, is_active=True).first()
        if asset:
            WalletAddress.objects.get_or_create(
                user=request.user,
                network=network_code,
                defaults={
                    "asset": asset,
                    "address": _generate_placeholder_address(network_code, request.user),
                },
            )

    addresses = (
        WalletAddress.objects
        .filter(user=request.user, is_active=True)
        .select_related("asset")
        .order_by("network")
    )

    # USD fiat balance (held as "USD" asset in the portfolio)
    usd_balance = _get_usd_balance(request.user)

    # Total staked value
    from .models import StakingPosition
    staked_usd = sum(
        float(pos.staked_amount)
        for pos in StakingPosition.objects.filter(
            user=request.user, status=StakingPosition.PositionStatus.ACTIVE
        ).select_related("pool__asset")
    )

    return render(request, "portfolio/wallet.html", {
        "addresses":   addresses,
        "usd_balance": usd_balance,
        "staked_usd":  round(staked_usd, 2),
    })


# ─────────────────────────────────────────────────────────
# WALLET — Address API  (called by JS when switching networks)
# ─────────────────────────────────────────────────────────

@login_required
@require_GET
def wallet_address_api(request):
    """
    GET /api/wallet/address/?network=BTC
    Returns the user's deposit address for the given network.
    """
    network = request.GET.get("network", "").upper().strip()
    if not network:
        return JsonResponse({"error": "network param required"}, status=400)

    addr = WalletAddress.objects.filter(
        user=request.user, network=network, is_active=True
    ).select_related("asset").first()

    if not addr:
        return JsonResponse({"error": "address not found"}, status=404)

    return JsonResponse({
        "network":      addr.network,
        "network_label": addr.get_network_display(),
        "asset_symbol": addr.asset.symbol,
        "asset_color":  addr.asset.color_hex or "#8a8680",
        "address":      addr.address,
    })


# ─────────────────────────────────────────────────────────
# WALLET — Balance API  (polled by the wallet page)
# ─────────────────────────────────────────────────────────

@login_required
@require_GET
def wallet_balance_api(request):
    """
    GET /api/wallet/balance/
    Returns the user's USD fiat balance and recent adjustment history
    for the wallet history tab.
    """
    usd_balance = _get_usd_balance(request.user)

    recent = (
        BalanceAdjustment.objects
        .filter(user=request.user)
        .select_related("asset")
        .order_by("-created_at")[:20]
    )

    history = [{
        "id":          str(a.id),
        "type_label":  a.get_adjustment_type_display(),
        "direction":   "credit" if a.delta > 0 else "debit",
        "delta":       str(a.delta),
        "asset":       a.asset.symbol,
        "asset_color": a.asset.color_hex or "#8a8680",
        "description": a.description,
        "usd_value":   str(a.usd_value_at_time) if a.usd_value_at_time else None,
        "created_at_human": _human_time(a.created_at),
        "created_at":  a.created_at.isoformat(),
    } for a in recent]

    return JsonResponse({
        "usd_balance": str(usd_balance),
        "history":     history,
    })


# ─────────────────────────────────────────────────────────
# WALLET — Helpers (add alongside existing helpers at bottom)
# ─────────────────────────────────────────────────────────

def _get_usd_balance(user):
    """Return the user's USD fiat holding quantity, defaulting to 0."""
    try:
        usd_asset = Asset.objects.get(symbol="USD")
        portfolio = Portfolio.objects.filter(user=user).first()
        if not portfolio:
            return Decimal("0")
        holding = Holding.objects.get(portfolio=portfolio, asset=usd_asset)
        return holding.quantity
    except (Asset.DoesNotExist, Holding.DoesNotExist):
        return Decimal("0")


def _generate_placeholder_address(network: str, user) -> str:
    """
    Generates a deterministic placeholder address for dev/staging.
    Replace this with your real custodial wallet provider (e.g. Fireblocks,
    BitGo, Copper) in production — they'll assign real addresses via API.
    """
    import hashlib
    seed = f"{network}:{user.id}:{user.email}"
    h    = hashlib.sha256(seed.encode()).hexdigest()

    if network == "BTC":
        return f"bc1q{h[:38]}"
    elif network == "ETH":
        return f"0x{h[:40]}"
    elif network == "SOL":
        import base64
        b = bytes.fromhex(h[:64])
        return base58.b58encode(b).decode()[:44]
    elif network == "BNB":
        return f"0x{h[4:44]}"
    return h[:42]

@login_required
def portfolio_page(request):
    portfolio_id = request.GET.get("portfolio")
    qs = Portfolio.objects.filter(user=request.user).prefetch_related(
        Prefetch("holdings", queryset=Holding.objects.select_related("asset"))
    )
    portfolio      = qs.filter(pk=portfolio_id).first() if portfolio_id else qs.first()
    all_portfolios = Portfolio.objects.filter(user=request.user).only("id", "name")
    return render(request, "portfolio/portfolio.html", {
        "portfolio":      portfolio,
        "all_portfolios": all_portfolios,
    })

@login_required
def app_shell_view(request):
  
    wallet_balance = _get_usd_balance(request.user)
    return render(request, "app_shell.html", {
        "wallet_balance": wallet_balance,})