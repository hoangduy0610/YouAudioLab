from django.db import migrations, models


class Migration(migrations.Migration):

    dependencies = [
        ("processing", "0005_ai_provider_w8"),
    ]

    operations = [
        migrations.AddField(
            model_name="systemasrsettings",
            name="local_engine_enabled",
            field=models.BooleanField(blank=True, null=True),
        ),
    ]
