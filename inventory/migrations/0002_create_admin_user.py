# Generated migration for creating initial admin user

from django.db import migrations


def create_admin_user(apps, schema_editor):
    """Create initial admin/manager user if it doesn't exist."""
    USERNAME = 'management'
    PASSWORD = 'password123'
    EMAIL = 'admin@aristoclean.com'

    User = apps.get_model('auth', 'User')

    # Only create if user doesn't exist
    if not User.objects.filter(username=USERNAME).exists():
        user = User.objects.create_superuser(USERNAME, EMAIL, PASSWORD)

        # Assign manager role
        UserProfile = apps.get_model('inventory', 'UserProfile')
        profile, _ = UserProfile.objects.get_or_create(user=user)
        profile.role = 'manager'
        profile.save()


def reverse_admin_user(apps, schema_editor):
    """Remove the admin user if it was created by this migration."""
    User = apps.get_model('auth', 'User')
    User.objects.filter(username='management').delete()


class Migration(migrations.Migration):

    dependencies = [
        ('inventory', '0001_initial'),
    ]

    operations = [
        migrations.RunPython(create_admin_user, reverse_admin_user),
    ]
