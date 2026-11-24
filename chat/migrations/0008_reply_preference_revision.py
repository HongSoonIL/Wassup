from django.db import migrations, models


class Migration(migrations.Migration):
    dependencies = [
        ("accounts", "0003_user_shared_preferences"),
        ("chat", "0007_reply_photo_attachments"),
    ]

    operations = [
        migrations.AddField(
            model_name="replyplan",
            name="preference_revision",
            field=models.PositiveBigIntegerField(default=0, editable=False),
        ),
        migrations.AddField(
            model_name="replyplan",
            name="preference_retry_until",
            field=models.DateTimeField(editable=False, null=True),
        ),
    ]
