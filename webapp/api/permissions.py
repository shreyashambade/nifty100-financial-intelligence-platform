from rest_framework.permissions import BasePermission

class IsHMACAuthenticated(BasePermission):
    """
    Commercial strict permission: 
    Allows access ONLY if the request passed the HMAC cryptographic handshake.
    """
    def has_permission(self, request, view):
        return request.auth == 'HMAC_VERIFIED'