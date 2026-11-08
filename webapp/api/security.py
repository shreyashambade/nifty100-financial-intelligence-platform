import hmac
import hashlib
import time
import re
import functools
from django.core.cache import cache
from django.http import JsonResponse
from django.utils.encoding import force_bytes
from .models import ChannelPartner

TIMESTAMP_WINDOW_SECONDS = 300  # 5-minute clock skew window
NONCE_CACHE_TIMEOUT = 2 * TIMESTAMP_WINDOW_SECONDS + 10  # 610-second TTL to cover full replay window
NONCE_REGEX = re.compile(r"^[A-Za-z0-9_\-]{16,64}$")
GENERIC_AUTH_ERROR = "Authentication failed."
DUMMY_SECRET = b"0" * 32
DUMMY_CANONICAL = b"DUMMY_CANONICAL_STRING"


def _safe_equal(a, b):
    """
    Safely compare two strings in constant time, encoding both to UTF-8 bytes
    before comparing to prevent WSGI / runtime 500 exceptions when encountering
    non-ASCII or malformed header inputs.
    """
    if a is None or b is None:
        return False
    try:
        a_bytes = a.encode('utf-8', errors='surrogateescape') if isinstance(a, str) else bytes(a)
        b_bytes = b.encode('utf-8', errors='surrogateescape') if isinstance(b, str) else bytes(b)
        return hmac.compare_digest(a_bytes, b_bytes)
    except Exception:
        return False


def build_canonical_string(method, full_path, timestamp, nonce, body_bytes=b""):
    """
    Constructs the strict canonical string format for HMAC-SHA256 signing:
    METHOD\nPATH?QUERY\nTIMESTAMP\nNONCE\nBODY_HASH
    
    Args:
        method (str): HTTP method (e.g., GET, POST).
        full_path (str): Full request path including query string (e.g., /api/v2/partner/companies/?sector=IT).
        timestamp (str|int): Raw timestamp string as sent in headers.
        nonce (str): Cryptographically random unique request identifier.
        body_bytes (bytes|str): Raw request payload bytes.
    """
    method_upper = str(method).strip().upper()
    
    if body_bytes is None:
        raw_body = b""
    elif isinstance(body_bytes, str):
        raw_body = body_bytes.encode('utf-8')
    else:
        raw_body = bytes(body_bytes)

    body_hash = hashlib.sha256(raw_body).hexdigest().lower()
    ts_str = str(timestamp).strip()
    nonce_str = str(nonce).strip()
    return f"{method_upper}\n{full_path}\n{ts_str}\n{nonce_str}\n{body_hash}"


def generate_hmac_signature(secret_key, method, full_path, timestamp, nonce, body_bytes=b""):
    """
    Generates an HMAC-SHA256 hex signature from the canonical request representation.
    """
    canonical_string = build_canonical_string(method, full_path, timestamp, nonce, body_bytes)
    return hmac.new(
        force_bytes(secret_key),
        force_bytes(canonical_string),
        hashlib.sha256
    ).hexdigest().lower()


def validate_hmac_request(request):
    """
    Enterprise-hardened HMAC-SHA256 request validator:
    
    Required Headers:
      - X-API-Key-ID: Partner's public API key identifier (UUID) as per Stream C PRD.
      - X-Timestamp: Epoch timestamp (must be within 5 minutes of server time).
      - X-Nonce: Cryptographically random unique request identifier matching [A-Za-z0-9_-]{16,64}.
      - X-Signature: Hexadecimal HMAC-SHA256 hash of the canonical request.
      
    Canonical Format:
      METHOD\nPATH?QUERY\nTIMESTAMP\nNONCE\nBODY_HASH
      
    Security & Reliability Rules:
      - Nonce TTL: 2 * TIMESTAMP_WINDOW_SECONDS + 10 (610s).
      - Non-ASCII Safe: Wrapped in _safe_equal to prevent WSGI 500 errors on invalid inputs.
      - Raw Timestamp: The raw header string is used in the canonical representation.
      - Database Errors: DB failures propagate cleanly as 500s rather than masquerading as 401s.
      - Anti-Enumeration: All authentication failures return identical generic error messages.
    """
    # 1. Extract required security headers (Strictly supports X-API-Key-ID with legacy X-API-Key fallback)
    api_key = (
        request.headers.get('X-API-Key-ID') or 
        request.META.get('HTTP_X_API_KEY_ID') or 
        request.headers.get('x-api-key-id') or
        request.headers.get('X-API-Key') or 
        request.META.get('HTTP_X_API_KEY') or 
        request.headers.get('x-api-key')
    )
    timestamp_header = (
        request.headers.get('X-Timestamp') or 
        request.META.get('HTTP_X_TIMESTAMP') or 
        request.headers.get('x-timestamp')
    )
    client_signature = (
        request.headers.get('X-Signature') or 
        request.META.get('HTTP_X_SIGNATURE') or 
        request.headers.get('x-signature')
    )
    nonce = (
        request.headers.get('X-Nonce') or 
        request.META.get('HTTP_X_NONCE') or 
        request.headers.get('x-nonce')
    )

    # Missing any header -> Generic 401
    if not api_key or not timestamp_header or not client_signature or not nonce:
        return (False, GENERIC_AUTH_ERROR, None)

    # 2. Validate Nonce format using strict regex [A-Za-z0-9_-]{16,64}
    nonce_str = str(nonce).strip()
    if not NONCE_REGEX.fullmatch(nonce_str):
        return (False, GENERIC_AUTH_ERROR, None)

    # 3. Validate Timestamp format and anti-replay clock skew window (300 seconds)
    # Note: timestamp_raw preserves the exact header string for the canonical payload
    timestamp_raw = str(timestamp_header).strip()
    try:
        req_timestamp = int(timestamp_raw)
    except (ValueError, TypeError):
        return (False, GENERIC_AUTH_ERROR, None)

    current_timestamp = int(time.time())
    if abs(current_timestamp - req_timestamp) > TIMESTAMP_WINDOW_SECONDS:
        return (False, GENERIC_AUTH_ERROR, None)

    # 4. Lookup partner by API key (do NOT catch generic Exception; allow DB outages to raise 500)
    partner = ChannelPartner.objects.filter(api_key=str(api_key).strip()).first()

    if not partner or not partner.is_active:
        # Perform constant-time dummy comparison to prevent key enumeration timing attacks
        _safe_equal(
            hmac.new(DUMMY_SECRET, DUMMY_CANONICAL, hashlib.sha256).hexdigest(),
            str(client_signature).strip().lower()
        )
        return (False, GENERIC_AUTH_ERROR, None)

    # 5. Strict Canonical String construction & HMAC-SHA256 signature verification
    method = request.method
    full_path = request.get_full_path()
    raw_body = request.body if hasattr(request, 'body') and request.body is not None else b""

    expected_signature = generate_hmac_signature(
        secret_key=partner.secret_key,
        method=method,
        full_path=full_path,
        timestamp=timestamp_raw,
        nonce=nonce_str,
        body_bytes=raw_body
    )

    client_sig_clean = str(client_signature).strip().lower()
    if not _safe_equal(expected_signature, client_sig_clean):
        return (False, GENERIC_AUTH_ERROR, None)

    # 6. Nonce Replay Protection using cache.add with extended 610s TTL
    # cache.add succeeds only if the key does not already exist in the cache.
    nonce_key = f"nonce:{partner.api_key}:{nonce_str}"
    if not cache.add(nonce_key, True, timeout=NONCE_CACHE_TIMEOUT):
        return (False, GENERIC_AUTH_ERROR, None)

    return (True, None, partner)


def partner_hmac_required(view_func):
    """
    Decorator for function-based views requiring valid HMAC-SHA256 authentication.
    Returns standard uniform 401 Unauthorized JSON error on authentication failure.
    """
    @functools.wraps(view_func)
    def wrapper(request, *args, **kwargs):
        is_valid, error_msg, partner = validate_hmac_request(request)
        if not is_valid:
            return JsonResponse({"detail": GENERIC_AUTH_ERROR}, status=401)
        request.partner = partner
        request.auth = 'HMAC_VERIFIED'
        return view_func(request, *args, **kwargs)
    return wrapper
