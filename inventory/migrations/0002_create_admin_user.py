from django.db import migrations


def noop_forward(apps, schema_editor):
    """Historical placeholder.

    Fresh installs must create an admin explicitly with `createsuperuser`.
    """
    return None


def noop_reverse(apps, schema_editor):
    return None


class Migration(migrations.Migration):

    dependencies = [
        ('inventory', '0001_initial'),
    ]

    operations = [
        migrations.RunPython(noop_forward, noop_reverse),
    ]
