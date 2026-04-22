from functools import wraps
from django.shortcuts import redirect
from django.contrib.auth.mixins import LoginRequiredMixin
from django.core.exceptions import PermissionDenied
from .models import UserProfile


def get_user_profile(user):
    if not user.is_authenticated:
        return None
    profile, _ = UserProfile.objects.get_or_create(user=user)
    return profile


def roles_required(*allowed_roles):
    def decorator(view_func):
        @wraps(view_func)
        def wrapper(request, *args, **kwargs):
            if not request.user.is_authenticated:
                return redirect("login")
            profile = get_user_profile(request.user)
            if profile is None or profile.role not in allowed_roles:
                raise PermissionDenied
            return view_func(request, *args, **kwargs)
        return wrapper
    return decorator


def manager_required(view_func):
    """Decorator: allow only logged-in managers."""
    return roles_required(UserProfile.ROLE_MANAGER)(view_func)


def stock_operator_required(view_func):
    """Decorator: allow only store keepers and managers."""
    return roles_required(UserProfile.ROLE_STOREKEEPER, UserProfile.ROLE_MANAGER)(view_func)


class ManagerRequiredMixin(LoginRequiredMixin):
    def dispatch(self, request, *args, **kwargs):
        if not request.user.is_authenticated:
            return self.handle_no_permission()
        profile = get_user_profile(request.user)
        if profile is None or not profile.is_manager:
            raise PermissionDenied
        return super().dispatch(request, *args, **kwargs)
