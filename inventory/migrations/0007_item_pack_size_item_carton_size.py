from django.db import migrations, models


class Migration(migrations.Migration):

    dependencies = [
        ("inventory", "0006_signinlog"),
    ]

    operations = [
        migrations.AddField(
            model_name="item",
            name="carton_size",
            field=models.DecimalField(blank=True, decimal_places=2, max_digits=12, null=True),
        ),
        migrations.AddField(
            model_name="item",
            name="pack_size",
            field=models.DecimalField(blank=True, decimal_places=2, max_digits=12, null=True),
        ),
    ]
