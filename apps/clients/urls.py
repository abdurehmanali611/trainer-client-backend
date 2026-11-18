from django.urls import path

from . import views

urlpatterns = [
    path("me/", views.MeView.as_view()),
    path("today/", views.TodayView.as_view()),
    path("achievements/", views.AchievementsView.as_view()),
    path("notifications/", views.NotificationListView.as_view()),
    path("devices/", views.DeviceRegisterView.as_view()),
]
