import uuid
import secrets
from django.core.management.base import BaseCommand, CommandError
from api.models import ChannelPartner


class Command(BaseCommand):
    help = 'Create or provision a Channel Partner with secure API Key and Secret Key for HMAC-SHA256 authentication.'

    def add_arguments(self, parser):
        parser.add_argument(
            'name', 
            type=str, 
            help='Partner organization or client name (e.g., "Acme Capital")'
        )
        parser.add_argument(
            '--email', 
            type=str, 
            default=None, 
            help='Partner contact email (defaults to <slug>@partner.niftyiq.local)'
        )
        parser.add_argument(
            '--inactive', 
            action='store_true', 
            help='Mark the partner as inactive (prevent API access)'
        )
        parser.add_argument(
            '--regenerate', 
            action='store_true', 
            help='Force regenerate new API Key and Secret Key if partner already exists'
        )

    def handle(self, *args, **options):
        name = options['name'].strip()
        email = options.get('email')
        is_active = not options.get('inactive', False)
        regenerate = options.get('regenerate', False)

        if not email:
            slug = name.lower().replace(' ', '_').replace('-', '_')
            email = f"{slug}@partner.niftyiq.local"

        partner = ChannelPartner.objects.filter(email=email).first()

        if partner and not regenerate:
            partner.name = name
            partner.is_active = is_active
            partner.save()
            action = "Updated existing"
        else:
            api_key = str(uuid.uuid4())
            secret_key = secrets.token_hex(32)

            if partner and regenerate:
                partner.name = name
                partner.api_key = api_key
                partner.secret_key = secret_key
                partner.is_active = is_active
                partner.save()
                action = "Regenerated credentials for"
            else:
                partner = ChannelPartner.objects.create(
                    name=name,
                    email=email,
                    api_key=api_key,
                    secret_key=secret_key,
                    is_active=is_active,
                )
                action = "Created new"

        status_label = "ACTIVE" if partner.is_active else "INACTIVE"

        self.stdout.write("")
        self.stdout.write(self.style.SUCCESS("=" * 70))
        self.stdout.write(self.style.SUCCESS(f"  {action} Channel Partner!"))
        self.stdout.write(self.style.SUCCESS("=" * 70))
        self.stdout.write(f"  Partner Name: {partner.name}")
        self.stdout.write(f"  Email:        {partner.email}")
        self.stdout.write(self.style.WARNING(f"  X-API-Key:    {partner.api_key}"))
        self.stdout.write(self.style.WARNING(f"  Secret Key:   {partner.secret_key}"))
        self.stdout.write(f"  Status:       {status_label}")
        self.stdout.write(self.style.SUCCESS("-" * 70))
        self.stdout.write("  Test this partner via test_partner_client.py:")
        self.stdout.write(f"  python test_partner_client.py --api-key {partner.api_key} --secret-key {partner.secret_key}")
        self.stdout.write(self.style.SUCCESS("=" * 70))
        self.stdout.write("")
