# Generated migration for ChannelPartner model
from django.db import migrations, models
import uuid


class Migration(migrations.Migration):

    initial = True

    dependencies = [
    ]

    operations = [
        migrations.CreateModel(
            name='ChannelPartner',
            fields=[
                ('id', models.BigAutoField(auto_created=True, primary_key=True, serialize=False, verbose_name='ID')),
                ('name', models.CharField(max_length=255)),
                ('email', models.EmailField(max_length=254, unique=True)),
                ('api_key', models.CharField(db_index=True, default=uuid.uuid4, max_length=64, unique=True)),
                ('secret_key', models.CharField(max_length=128)),
                ('is_active', models.BooleanField(default=True)),
                ('created_at', models.DateTimeField(auto_now_add=True)),
            ],
            options={
                'verbose_name': 'Channel Partner',
                'verbose_name_plural': 'Channel Partners',
                'db_table': 'channel_partner',
            },
        ),
    ]

