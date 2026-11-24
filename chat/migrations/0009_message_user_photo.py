from django.db import migrations, models

from accounts.private_photos import private_storage


class Migration(migrations.Migration):
    dependencies = [('chat', '0008_reply_preference_revision')]

    operations = [
        migrations.AddField(
            model_name='message', name='user_photo',
            field=models.ImageField(blank=True, db_default='', editable=False, storage=private_storage, upload_to='chat/'),
        ),
        migrations.AddField(
            model_name='message', name='client_message_id',
            field=models.UUIDField(editable=False, null=True),
        ),
        migrations.AddConstraint(
            model_name='message',
            constraint=models.UniqueConstraint(fields=('room', 'client_message_id'), name='chat_room_client_message'),
        ),
    ]
