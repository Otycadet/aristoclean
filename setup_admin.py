
from django.contrib.auth.models import User
from inventory.models import UserProfile
import os
import django

os.environ.setdefault("DJANGO_SETTINGS_MODULE", "aristoclean.settings")
django.setup()


# The credentials you will use to log in
USERNAME = 'management'
PASSWORD = 'password123'

if not User.objects.filter(username=USERNAME).exists():
    print(f"Creating superuser {USERNAME}...")
    u = User.objects.create_superuser(
        USERNAME, 'admin@aristoclean.com', PASSWORD)

    # Assign the manager role
    p, _ = UserProfile.objects.get_or_create(user=u)
    p.role = 'manager'
    p.save()
    print("Superuser created successfully!")
