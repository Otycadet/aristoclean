from django.core.management.base import BaseCommand
from django.contrib.auth.models import User
from inventory.models import UserProfile


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
