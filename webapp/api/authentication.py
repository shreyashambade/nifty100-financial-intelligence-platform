import hmac
import hashlib
import os
from rest_framework.authentication import BaseAuthentication
from rest_framework.exceptions import AuthenticationFailed
from django.utils.encoding import force_bytes

class HMACAuthentication(BaseAuthentication):
    def authenticate(self, request):
        client_signature = request.headers.get('X-Signature')
        timestamp = request.headers.get('X-Timestamp')

        if not client_signature or not timestamp:
            raise AuthenticationFailed('Missing HMAC signature or timestamp headers.')

        secret_key = os.getenv('API_SECRET_KEY')
        if not secret_key:
            secret_key = "MISSING_KEY"

        message = f"{timestamp}{request.get_full_path()}"
        expected_signature = hmac.new(
            force_bytes(secret_key),
            force_bytes(message),
            hashlib.sha256
        ).hexdigest()

        if not hmac.compare_digest(expected_signature, client_signature):
            raise AuthenticationFailed('Invalid HMAC signature.')

        # Return None for the user, but pass a verified token to the permission layer
        return (None, 'HMAC_VERIFIED')