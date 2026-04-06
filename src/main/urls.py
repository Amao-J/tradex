from django.urls import path

from main import views

urlpatterns = [
    path('', views.home, name="home"),
    path("signin/", views.signin_view, name="signin"),
    path("signup/", views.signup_view, name="signup"),
    path("logout/", views.logout_view, name="logout"),
    path("portfolio/", views.portfolio_dashboard, name="portfolio-dashboard"),
    path("portfolio-page/", views.portfolio_page, name="portfolio"),
    path("api/portfolio/adjustments/", views.balance_adjustments_api, name="api-adjustments"),
    path("api/portfolio/allocation/", views.allocation_chart_api, name="api-allocation"),
    path("api/portfolio/sparklines/", views.sparklines_api, name="api-sparklines"),
  
path("wallet/",                views.wallet_view,         name="wallet"),
path("api/wallet/address/",    views.wallet_address_api,  name="api-wallet-address"),
path("api/wallet/balance/",    views.wallet_balance_api,  name="api-wallet-balance"),
path("app/",     views.app_shell_view, name="app-shell"),

path("api/wallet/deposit/",    views.api_wallet_deposit,  name="api-wallet-deposit"),
path("api/wallet/withdraw/",   views.api_wallet_withdraw, name="api-wallet-withdraw"),
]
