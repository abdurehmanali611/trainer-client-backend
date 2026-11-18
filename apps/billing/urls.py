from django.urls import path

from . import views

urlpatterns = [
    path("billing/", views.MyBillingView.as_view()),
    path("invoices/", views.InvoiceListView.as_view()),
]
