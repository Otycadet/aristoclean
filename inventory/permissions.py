from functools import wraps
from django.shortcuts import redirect
from django.contrib.auth.mixins import LoginRequiredMixin
from django.core.exceptions import PermissionDenied


def manager_required(view_func):
    """Decorator: allow only logged-in managers."""
    @wraps(view_func)
    def wrapper(request, *args, **kwargs):
        if not request.user.is_authenticated:
            return redirect("login")
        profile = getattr(request.user, "profile", None)
        if profile is None or not profile.is_manager:
            raise PermissionDenied
        return view_func(request, *args, **kwargs)
    return wrapper


class ManagerRequiredMixin(LoginRequiredMixin):
    def dispatch(self, request, *args, **kwargs):
        if not request.user.is_authenticated:
            return self.handle_no_permission()
        profile = getattr(request.user, "profile", None)
        if profile is None or not profile.is_manager:
            raise PermissionDenied
        return super().dispatch(request, *args, **kwargs)
