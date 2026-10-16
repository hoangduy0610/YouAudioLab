from django.db import migrations, models


class Migration(migrations.Migration):

    dependencies = [
        ("processing", "0006_system_asr_local_engine"),
    ]

    operations = [
        migrations.AddField(
            model_name="useraipreferences",
            name="llm_base_url",
            field=models.CharField(blank=True, max_length=512, null=True),
        ),
        migrations.AddField(
            model_name="useraipreferences",
            name="llm_model",
            field=models.CharField(blank=True, max_length=128, null=True),
        ),
        migrations.AddField(
            model_name="useraipreferences",
            name="llm_api_key_encrypted",
            field=models.TextField(blank=True, null=True),
        ),
    ]
