# Generated migration for ChannelPartner tier field
from django.db import migrations, models


class Migration(migrations.Migration):

    dependencies = [
        ('api', '0003_webhooksubscription_webhookevent'),
    ]

    operations = [
        migrations.AddField(
            model_name='channelpartner',
            name='tier',
            field=models.CharField(
                choices=[('BASIC', 'BASIC'), ('PRO', 'PRO'), ('ENTERPRISE', 'ENTERPRISE')],
                default='BASIC',
                db_index=True,
                help_text='Partner rate limiting tier: BASIC, PRO, or ENTERPRISE',
                max_length=20,
            ),
        ),
    ]
