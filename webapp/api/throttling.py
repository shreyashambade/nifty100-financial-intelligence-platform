"""
Tier-Based Redis Rate Limiting & Throttling for NiftyIQ Channel Partner API.
Implements Stream C PRD multi-window quotas:
  - BASIC:      10 / minute,   100 / hour,     500 / day
  - PRO:        60 / minute, 1,000 / hour,  10,000 / day
  - ENTERPRISE: 300 / minute, 10,000 / hour,  no daily cap (unlimited)
"""

import time
import logging
from django.core.cache import cache
from rest_framework.throttling import BaseThrottle
from rest_framework.exceptions import Throttled
from rest_framework.response import Response
from rest_framework.views import exception_handler as drf_exception_handler
from rest_framework import status

logger = logging.getLogger(__name__)

# PRD Multi-window quotas by Tier
TIER_QUOTAS = {
    'BASIC': {
        'minute': 10,
        'hour': 100,
        'day': 500,
    },
    'PRO': {
        'minute': 60,
        'hour': 1000,
        'day': 10000,
    },
    'ENTERPRISE': {
        'minute': 300,
        'hour': 10000,
        'day': None,  # No daily cap
    },
}


class PartnerRateLimitExceeded(Throttled):
    """
    Custom Throttled exception carrying partner tier and remaining quota metadata.
    """
    default_detail = "Rate limit exceeded"
    extra_detail_singular = "Expected available in {wait} second."
    extra_detail_plural = "Expected available in {wait} seconds."

    def __init__(self, wait=None, detail=None, code=None, quota_info=None):
        super().__init__(wait=wait, detail=detail, code=code)
        self.quota_info = quota_info or {}


class PartnerTierRateThrottle(BaseThrottle):
    """
    Custom DRF Throttle class enforcing multi-window tier quotas stored in Redis / cache.
    Evaluates:
      1. Per-minute window (60s)
      2. Per-hour window (3600s)
      3. Per-day window (86400s)
    """

    def __init__(self):
        self._wait = 0
        self._quota_info = {}

    def get_ident(self, request):
        """
        Resolves a unique consumer identifier. Prioritizes authenticated ChannelPartner
        (set as request.user or request.partner by PartnerHMACAuthentication),
        with client IP address fallback.
        """
        partner = getattr(request, 'user', None) or getattr(request, 'partner', None)
        if partner and hasattr(partner, 'pk') and partner.pk:
            return f"partner:{partner.pk}"
        
        # IP address fallback
        x_forwarded_for = request.META.get('HTTP_X_FORWARDED_FOR')
        if x_forwarded_for:
            ip = x_forwarded_for.split(',')[0].strip()
        else:
            ip = request.META.get('REMOTE_ADDR', '127.0.0.1')
        return f"ip:{ip}"

    def get_tier(self, request):
        """
        Determines the tier for the requesting entity. Defaults to 'BASIC'.
        """
        partner = getattr(request, 'user', None) or getattr(request, 'partner', None)
        tier = getattr(partner, 'tier', None)
        if tier and str(tier).upper() in TIER_QUOTAS:
            return str(tier).upper()
        return 'BASIC'

    def allow_request(self, request, view):
        """
        Inspects multi-window Redis counters for the partner's tier.
        Returns True if all windows have remaining quota, False otherwise.
        """
        ident = self.get_ident(request)
        tier = self.get_tier(request)
        quotas = TIER_QUOTAS.get(tier, TIER_QUOTAS['BASIC'])

        current_time = time.time()

        # Window definitions (duration in seconds, quota limit, bucket key, remaining TTL)
        windows = {
            'minute': {
                'limit': quotas['minute'],
                'bucket': int(current_time // 60),
                'ttl': max(1, int(60 - (current_time % 60))),
            },
            'hour': {
                'limit': quotas['hour'],
                'bucket': int(current_time // 3600),
                'ttl': max(1, int(3600 - (current_time % 3600))),
            },
            'day': {
                'limit': quotas['day'],
                'bucket': int(current_time // 86400),
                'ttl': max(1, int(86400 - (current_time % 86400))),
            },
        }

        counts = {}
        exceeded_window = None
        max_wait = 0

        # 1. Check all window limits
        for win_name, win in windows.items():
            limit = win['limit']
            if limit is None:
                counts[win_name] = 0
                continue

            cache_key = f"throttle:{ident}:{win_name}:{win['bucket']}"
            raw_count = cache.get(cache_key)
            try:
                current_count = int(raw_count) if raw_count is not None else 0
            except (ValueError, TypeError):
                current_count = 0

            counts[win_name] = current_count

            if current_count >= limit:
                if exceeded_window is None or win['ttl'] > max_wait:
                    exceeded_window = win_name
                    max_wait = win['ttl']

        # 2. If any window quota is breached, reject request
        if exceeded_window is not None:
            self._wait = max_wait
            remaining_quota = {
                'tier': tier,
                'exceeded_window': exceeded_window,
                'minute': max(0, quotas['minute'] - counts.get('minute', 0)),
                'hour': max(0, quotas['hour'] - counts.get('hour', 0)),
                'day': max(0, quotas['day'] - counts.get('day', 0)) if quotas['day'] is not None else "UNLIMITED",
            }
            self._quota_info = remaining_quota
            request._quota_info = remaining_quota
            request._throttle_wait = max_wait
            logger.warning(
                f"Rate limit exceeded for {ident} (Tier: {tier}, Window: {exceeded_window}, Wait: {max_wait}s)."
            )
            return False

        # 3. All windows within quota -> Atomically increment counters
        for win_name, win in windows.items():
            if win['limit'] is None:
                continue

            cache_key = f"throttle:{ident}:{win_name}:{win['bucket']}"
            ttl = win['ttl']
            try:
                # Atomically set if not exists, or increment
                if cache.add(cache_key, 1, timeout=ttl):
                    counts[win_name] = 1
                else:
                    new_val = cache.incr(cache_key)
                    counts[win_name] = new_val
            except Exception:
                # Resilient fallback for test or mock caches
                val = cache.get(cache_key)
                new_val = int(val or 0) + 1
                cache.set(cache_key, new_val, timeout=ttl)
                counts[win_name] = new_val

        # 4. Attach remaining quota snapshot to request
        remaining_quota = {
            'tier': tier,
            'minute': max(0, quotas['minute'] - counts.get('minute', 0)),
            'hour': max(0, quotas['hour'] - counts.get('hour', 0)),
            'day': max(0, quotas['day'] - counts.get('day', 0)) if quotas['day'] is not None else "UNLIMITED",
        }
        self._quota_info = remaining_quota
        request._quota_info = remaining_quota
        return True

    def wait(self):
        """
        Returns duration in seconds until the breached rate limit resets.
        """
        return self._wait


# Backward compatibility alias
PartnerRateThrottle = PartnerTierRateThrottle


class PartnerThrottleMixin:
    """
    Mixin for DRF ViewSets to ensure throttled rejections raise PartnerRateLimitExceeded.
    """
    def throttled(self, request, wait):
        raise PartnerRateLimitExceeded(
            wait=wait,
            quota_info=getattr(request, '_quota_info', None)
        )


def partner_api_exception_handler(exc, context):
    """
    Custom DRF Exception Handler.
    Intercepts Throttled exceptions to return HTTP 429 with:
      1. {"error": "Rate limit exceeded", ...}
      2. Standard 'Retry-After' header
      3. Multi-window remaining quota breakdown
    """
    response = drf_exception_handler(exc, context)

    if isinstance(exc, Throttled):
        wait = getattr(exc, 'wait', None)
        if wait is None and 'request' in context:
            wait = getattr(context['request'], '_throttle_wait', 1)
        wait = int(wait) if wait else 1

        quota_info = getattr(exc, 'quota_info', None)
        if not quota_info and 'request' in context:
            quota_info = getattr(context['request'], '_quota_info', None)

        error_data = {
            "error": "Rate limit exceeded",
            "detail": f"Request was throttled. Expected available in {wait} second{'s' if wait != 1 else ''}.",
            "retry_after": wait,
        }
        if quota_info:
            error_data["remaining_quota"] = quota_info

        if response is not None:
            response.data = error_data
            response.status_code = status.HTTP_429_TOO_MANY_REQUESTS
            response['Retry-After'] = str(wait)
        else:
            response = Response(
                error_data,
                status=status.HTTP_429_TOO_MANY_REQUESTS,
                headers={'Retry-After': str(wait)}
            )

    return response
