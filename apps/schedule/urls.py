from django.urls import path

from . import views

urlpatterns = [
    path("schedule/", views.ScheduleView.as_view()),
    path("requests/reschedule/", views.RescheduleRequestCreateView.as_view()),
    path("requests/permission/", views.PermissionRequestCreateView.as_view()),
    path("requests/", views.MyRequestsView.as_view()),
    path("attendance/", views.MyAttendanceView.as_view()),
    path("sessions/<int:pk>/", views.SessionDetailView.as_view()),
    path("sessions/<int:pk>/finish/", views.SessionFinishView.as_view()),
]
