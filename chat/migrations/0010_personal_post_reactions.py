from django.db import migrations, models
import django.db.models.deletion


class Migration(migrations.Migration):
    dependencies = [
        ('accounts', '0004_personalpost'),
        ('chat', '0009_message_user_photo'),
    ]

    operations = [
        migrations.AddField(
            model_name='message', name='source_personal_post',
            field=models.OneToOneField(
                blank=True, null=True, on_delete=django.db.models.deletion.SET_NULL,
                related_name='reaction_message', to='accounts.personalpost',
            ),
        ),
        migrations.CreateModel(
            name='PersonalPostReaction',
            fields=[
                ('id', models.BigAutoField(auto_created=True, primary_key=True, serialize=False, verbose_name='ID')),
                ('post', models.OneToOneField(on_delete=django.db.models.deletion.CASCADE,
                                              related_name='reaction', to='accounts.personalpost')),
                ('status', models.CharField(choices=[('pending', 'Pending'), ('declined', 'Declined'), ('sent', 'Sent')],
                                            default='pending', max_length=8)),
                ('token', models.UUIDField(editable=False, null=True)),
                ('available_at', models.DateTimeField(null=True)),
                ('attempts', models.PositiveSmallIntegerField(default=0)),
                ('photo_description', models.TextField(blank=True)),
            ],
        ),
    ]
