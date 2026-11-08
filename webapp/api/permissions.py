from rest_framework.permissions import BasePermission


class IsHMACAuthenticated(BasePermission):
    """
    Commercial strict permission: 
    Allows access ONLY if the request passed the HMAC cryptographic handshake.
    """
    def has_permission(self, request, view):
        return request.auth == 'HMAC_VERIFIED'


class IsPartnerHMACAuthenticated(BasePermission):
    """
    Phase 2 Partner API permission:
    Allows access only to requests that successfully validated against an active ChannelPartner.
    """
    def has_permission(self, request, view):
        return (
            getattr(request, 'auth', None) == 'HMAC_VERIFIED' and
            hasattr(request, 'partner') and
            request.partner is not None and
            getattr(request.partner, 'is_active', False)
        )
