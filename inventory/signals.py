from django.contrib.auth.signals import user_logged_in
from django.contrib.auth.models import User
from django.dispatch import receiver
from django.db.models.signals import post_save

from .models import SignInLog, UserProfile


@receiver(post_save, sender=User)
def create_user_profile(sender, instance, created, **kwargs):
    if created:
        UserProfile.objects.get_or_create(user=instance)


def _extract_client_ip(request):
    forwarded_for = request.META.get("HTTP_X_FORWARDED_FOR", "").strip()
    if forwarded_for:
        return forwarded_for.split(",")[0].strip()
    return request.META.get("REMOTE_ADDR", "").strip() or None


@receiver(user_logged_in)
def record_successful_sign_in(sender, request, user, **kwargs):
    SignInLog.objects.create(
        user=user,
        username_snapshot=user.username,
        ip_address=_extract_client_ip(request),
        user_agent=(request.META.get("HTTP_USER_AGENT", "") or "")[:512],
    )
