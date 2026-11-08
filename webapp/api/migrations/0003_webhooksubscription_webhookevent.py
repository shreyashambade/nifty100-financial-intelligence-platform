# Generated migration for WebhookSubscription and WebhookEvent models
from django.db import migrations, models
import django.db.models.deletion


class Migration(migrations.Migration):

    dependencies = [
        ('api', '0002_apiusagelog'),
    ]

    operations = [
        migrations.CreateModel(
            name='WebhookSubscription',
            fields=[
                ('id', models.BigAutoField(auto_created=True, primary_key=True, serialize=False, verbose_name='ID')),
                ('event_type', models.CharField(choices=[('score_updated', 'score_updated'), ('anomaly_flagged', 'anomaly_flagged')], default='score_updated', max_length=64)),
                ('target_url', models.URLField(max_length=512)),
                ('is_active', models.BooleanField(default=True)),
                ('created_at', models.DateTimeField(auto_now_add=True)),
                ('partner', models.ForeignKey(on_delete=django.db.models.deletion.CASCADE, related_name='webhook_subscriptions', to='api.channelpartner')),
            ],
            options={
                'verbose_name': 'Webhook Subscription',
                'verbose_name_plural': 'Webhook Subscriptions',
                'db_table': 'webhook_subscription',
                'ordering': ['-created_at'],
            },
        ),
        migrations.CreateModel(
            name='WebhookEvent',
            fields=[
                ('id', models.BigAutoField(auto_created=True, primary_key=True, serialize=False, verbose_name='ID')),
                ('payload', models.JSONField()),
                ('status', models.CharField(choices=[('PENDING', 'PENDING'), ('SUCCESS', 'SUCCESS'), ('FAILED', 'FAILED')], default='PENDING', max_length=20)),
                ('retry_count', models.IntegerField(default=0)),
                ('last_error', models.TextField(blank=True, null=True)),
                ('created_at', models.DateTimeField(auto_now_add=True)),
                ('delivered_at', models.DateTimeField(blank=True, null=True)),
                ('subscription', models.ForeignKey(on_delete=django.db.models.deletion.CASCADE, related_name='delivery_events', to='api.webhooksubscription')),
            ],
            options={
                'verbose_name': 'Webhook Event',
                'verbose_name_plural': 'Webhook Events',
                'db_table': 'webhook_event',
                'ordering': ['-created_at'],
            },
        ),
    ]

