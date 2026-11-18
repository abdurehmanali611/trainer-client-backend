from django.urls import path

from . import views

urlpatterns = [
    path("client-login/", views.ClientLoginView.as_view()),
    path("access-status/", views.ClientAccessStatusView.as_view()),
    path("lock-payment-submit/", views.ClientLockPaymentSubmitView.as_view()),
    path("change-credential/", views.ClientChangeCredentialView.as_view()),
    path("reset-request/", views.ClientResetRequestView.as_view()),
    path("reset-confirm/", views.ClientResetConfirmView.as_view()),
    path("refresh/", views.TokenRefreshView.as_view()),
]
