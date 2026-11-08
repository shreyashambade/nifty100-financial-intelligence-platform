# Generated migration for WebhookEvent status_code and response_body fields
from django.db import migrations, models


class Migration(migrations.Migration):

    dependencies = [
        ('api', '0004_channelpartner_tier'),
    ]

    operations = [
        migrations.AddField(
            model_name='webhookevent',
            name='status_code',
            field=models.IntegerField(
                blank=True,
                help_text='HTTP response status code from webhook receiver',
                null=True,
            ),
        ),
        migrations.AddField(
            model_name='webhookevent',
            name='response_body',
            field=models.TextField(
                blank=True,
                help_text='HTTP response body snippet from webhook receiver',
                null=True,
            ),
        ),
    ]
