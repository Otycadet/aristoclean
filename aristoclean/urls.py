from django.contrib import admin
from django.urls import path, include

urlpatterns = [
    path("admin/", admin.site.urls),
    path("accounts/", include("django.contrib.auth.urls")),
    path("", include("inventory.urls")),
]

handler403 = "aristoclean.error_views.permission_denied_view"
handler404 = "aristoclean.error_views.page_not_found_view"
handler500 = "aristoclean.error_views.server_error_view"
