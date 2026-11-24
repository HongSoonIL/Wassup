import django.db.models.deletion
from django.conf import settings
from django.db import migrations, models

from accounts.private_photos import private_storage


class Migration(migrations.Migration):
    dependencies = [('accounts', '0003_user_shared_preferences')]

    operations = [
        migrations.CreateModel(
            name='PersonalPost',
            fields=[
                ('id', models.BigAutoField(auto_created=True, primary_key=True, serialize=False, verbose_name='ID')),
                ('caption', models.TextField(blank=True)),
                ('created_at', models.DateTimeField(auto_now_add=True)),
                ('photo', models.ImageField(blank=True, storage=private_storage, upload_to='personal-posts/')),
                ('owner', models.ForeignKey(on_delete=django.db.models.deletion.CASCADE,
                                            related_name='personal_posts', to=settings.AUTH_USER_MODEL)),
            ],
            options={'indexes': [
                models.Index(fields=['owner', '-created_at', '-id'], name='personal_post_owner_date'),
            ]},
        ),
    ]
