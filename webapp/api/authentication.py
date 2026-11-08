import hmac
import hashlib
import os
from rest_framework.authentication import BaseAuthentication
from rest_framework.exceptions import AuthenticationFailed
from django.utils.encoding import force_bytes
from .security import validate_hmac_request, GENERIC_AUTH_ERROR


class PartnerHMACAuthentication(BaseAuthentication):
    """
    Consolidated single-point DRF Authentication for Phase 2 Channel Partner API.
    Enforces strict canonical HMAC-SHA256 signature verification and atomic nonce replay prevention.
    Returns uniform generic AuthenticationFailed on any failure to prevent key enumeration.
    """
    def authenticate(self, request):
        is_valid, error_msg, partner = validate_hmac_request(request)
        if not is_valid:
            raise AuthenticationFailed(GENERIC_AUTH_ERROR)

        request.partner = partner
        return (partner, 'HMAC_VERIFIED')

    def authenticate_header(self, request):
        return 'HMAC-SHA256 realm="NiftyIQ Channel Partner API"'


class HMACAuthentication(BaseAuthentication):
    """
    Legacy / v1 HMAC Authentication using shared environment API_SECRET_KEY.
    Maintained for backward compatibility.
    """
    def authenticate(self, request):
        client_signature = request.headers.get('X-Signature') or request.META.get('HTTP_X_SIGNATURE')
        timestamp = request.headers.get('X-Timestamp') or request.META.get('HTTP_X_TIMESTAMP')

        if not client_signature or not timestamp:
            raise AuthenticationFailed(GENERIC_AUTH_ERROR)

        secret_key = os.getenv('API_SECRET_KEY', 'bluestock_commercial_secret_998877')

        path = request.path
        full_path = request.get_full_path()
        candidates = [
            f"{timestamp}{full_path}",
            f"{timestamp}{path}",
            f"{timestamp}:{path}",
        ]

        secret_bytes = force_bytes(secret_key)
        client_sig_clean = client_signature.strip().lower()

        matched = False
        for msg in candidates:
            expected = hmac.new(secret_bytes, force_bytes(msg), hashlib.sha256).hexdigest().lower()
            if hmac.compare_digest(expected, client_sig_clean):
                matched = True
                break

        if not matched:
            raise AuthenticationFailed(GENERIC_AUTH_ERROR)

        return (None, 'HMAC_VERIFIED')


try:
    from drf_spectacular.extensions import OpenApiAuthenticationExtension

    class PartnerHMACAuthenticationScheme(OpenApiAuthenticationExtension):
        target_class = 'api.authentication.PartnerHMACAuthentication'
        name = 'ApiKeyAuth'
        match_subclasses = True

        def get_security_definition(self, auto_schema):
            return {
                'type': 'apiKey',
                'in': 'header',
                'name': 'X-API-Key',
                'description': (
                    'Channel Partner HMAC-SHA256 Authentication.\n'
                    'Provide your partner public API key in the X-API-Key header.\n'
                    'Requests also require X-Timestamp, X-Nonce, and X-Signature headers.'
                )
            }

    class LegacyHMACAuthenticationScheme(OpenApiAuthenticationExtension):
        target_class = 'api.authentication.HMACAuthentication'
        name = 'ApiKeyAuth'
        match_subclasses = True

        def get_security_definition(self, auto_schema):
            return {
                'type': 'apiKey',
                'in': 'header',
                'name': 'X-API-Key',
                'description': (
                    'Legacy HMAC Authentication.\n'
                    'Provide your partner API key in the X-API-Key header.'
                )
            }
except ImportError:
    pass

