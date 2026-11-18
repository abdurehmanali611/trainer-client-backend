from django.urls import include, path

# Every feature area serves its own URLs under /api/ — the client app keeps
# calling `${API_URL}/api/...` exactly as before (no proxy, no rewrite).
urlpatterns = [
    path("api/auth/", include("apps.auth.urls")),
    path("api/client/", include("apps.clients.urls")),
    path("api/client/", include("apps.plans.urls")),
    path("api/client/", include("apps.schedule.urls")),
    path("api/client/", include("apps.billing.urls")),
    path("api/client/", include("apps.chat.urls")),
    path("api/client/", include("apps.journal.urls")),
]
