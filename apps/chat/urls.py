from django.urls import path

from . import views

urlpatterns = [
    path("chat/", views.ChatMessagesView.as_view()),
    path("chat/read/", views.ChatReadView.as_view()),
]
