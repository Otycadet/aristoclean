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
            if request.user.is_superuser:
                return view_func(request, *args, **kwargs)
            profile = get_user_profile(request.user)
            if profile is None or profile.role not in allowed_roles:
                raise PermissionDenied
            return view_func(request, *args, **kwargs)
        return wrapper
    return decorator


def manager_required(view_func):
    """Decorator: allow operational managers and superusers."""
    return roles_required(UserProfile.ROLE_MANAGER)(view_func)


def admin_required(view_func):
    """Decorator: allow admins and superusers."""
    return roles_required(UserProfile.ROLE_ADMIN)(view_func)


def stock_operator_required(view_func):
    """Decorator: allow stock operators, managers, and superusers."""
    return roles_required(UserProfile.ROLE_STOREKEEPER, UserProfile.ROLE_MANAGER)(view_func)


class ManagerRequiredMixin(LoginRequiredMixin):
    def dispatch(self, request, *args, **kwargs):
        if not request.user.is_authenticated:
            return self.handle_no_permission()
        if request.user.is_superuser:
            return super().dispatch(request, *args, **kwargs)
        profile = get_user_profile(request.user)
        if profile is None or not profile.is_manager:
            raise PermissionDenied
        return super().dispatch(request, *args, **kwargs)


def get_role_choices_for_actor(actor_user, target_user=None):
    if not actor_user.is_authenticated:
        return []
    if target_user is not None and target_user.is_superuser:
        return []
    if actor_user.is_superuser:
        return list(UserProfile.ROLE_CHOICES)

    actor_profile = get_user_profile(actor_user)
    if actor_profile is None:
        return []

    if actor_profile.role == UserProfile.ROLE_ADMIN:
        if target_user is not None and target_user == actor_user:
            return []
        return list(UserProfile.ROLE_CHOICES)

    return []


def can_create_users(actor_user):
    if not actor_user.is_authenticated:
        return False
    if actor_user.is_superuser:
        return True
    actor_profile = get_user_profile(actor_user)
    return bool(actor_profile and actor_profile.role == UserProfile.ROLE_ADMIN)


def can_manage_user_account(actor_user, target_user):
    if not actor_user.is_authenticated:
        return False
    if actor_user.is_superuser:
        return True

    actor_profile = get_user_profile(actor_user)
    if actor_profile is None:
        return False

    if actor_profile.role == UserProfile.ROLE_ADMIN:
        return not target_user.is_superuser

    return False


def get_effective_role_label(user):
    if not user.is_authenticated:
        return ""
    if user.is_superuser:
        return "Superadmin"
    profile = get_user_profile(user)
    return profile.get_role_display() if profile else "Unknown"
