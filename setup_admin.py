#!/usr/bin/env python
"""
Placeholder stub file for Railway compatibility.
Admin user creation is now handled by Django migrations (0002_create_admin_user.py).
This file exists only to prevent deployment errors from cached configs.
"""

if __name__ == '__main__':
    print("Admin user creation is now handled by Django migrations.")
    print("No action needed here.")
from inventory.models import UserProfile
from django.contrib.auth.models import User
from django.core.management.base import BaseCommand
import os
import django

os.environ.setdefault('DJANGO_SETTINGS_MODULE', 'aristoclean.settings')
django.setup()


class Command(BaseCommand):
    help = 'Creates the initial manager admin account'

    def handle(self, *args, **options):
        USERNAME = 'management'
        PASSWORD = 'password123'

        if not User.objects.filter(username=USERNAME).exists():
            self.stdout.write(f"Creating superuser {USERNAME}...")
            u = User.objects.create_superuser(
                USERNAME, 'admin@aristoclean.com', PASSWORD)

            # Assign the manager role
            p, _ = UserProfile.objects.get_or_create(user=u)
            p.role = 'manager'
            p.save()
            self.stdout.write(self.style.SUCCESS(
                "Superuser created successfully!"))
        else:
            self.stdout.write("Superuser already exists.")


if __name__ == '__main__':
    command = Command()
    command.handle()
