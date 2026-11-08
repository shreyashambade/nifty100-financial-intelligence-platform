# Generated migration for APIUsageLog model
from django.db import migrations, models
import django.db.models.deletion


class Migration(migrations.Migration):

    dependencies = [
        ('api', '0001_initial'),
    ]

    operations = [
        migrations.CreateModel(
            name='APIUsageLog',
            fields=[
                ('id', models.BigAutoField(auto_created=True, primary_key=True, serialize=False, verbose_name='ID')),
                ('endpoint', models.CharField(max_length=512)),
                ('method', models.CharField(max_length=16)),
                ('status_code', models.IntegerField()),
                ('response_ms', models.IntegerField()),
                ('ip_address', models.GenericIPAddressField(blank=True, null=True)),
                ('request_size', models.IntegerField(default=0)),
                ('response_size', models.IntegerField(default=0)),
                ('created_at', models.DateTimeField(auto_now_add=True)),
                ('partner', models.ForeignKey(blank=True, null=True, on_delete=django.db.models.deletion.SET_NULL, related_name='usage_logs', to='api.channelpartner')),
            ],
            options={
                'verbose_name': 'API Usage Log',
                'verbose_name_plural': 'API Usage Logs',
                'db_table': 'api_usage_log',
                'ordering': ['-created_at'],
            },
        ),
    ]

