import time
import logging

logger = logging.getLogger(__name__)


class PartnerAPILoggingMiddleware:
    """
    Middleware to monitor and asynchronously log all Channel Partner API requests
    to the APIUsageLog table via Celery task log_api_request_task.delay(...).

    Measures total end-to-end response time (latency in ms), request and response sizes,
    HTTP status codes, and attributes usage to the corresponding ChannelPartner.
    """
    def __init__(self, get_response):
        self.get_response = get_response

    def __call__(self, request):
        path = request.path
        is_partner_route = (
            path.startswith('/api/partner/v1/') or
            path.startswith('/api/partner/') or
            path.startswith('/api/v2/partner/') or 
            'HTTP_X_API_KEY_ID' in request.META or
            'X-API-Key-ID' in request.headers or
            'HTTP_X_API_KEY' in request.META or
            'X-API-Key' in request.headers
        )

        if not is_partner_route:
            return self.get_response(request)

        # 1. Start latency timer and estimate request payload size
        start_time = time.time()

        request_size = 0
        content_length = request.META.get('CONTENT_LENGTH')
        if content_length and content_length.isdigit():
            request_size = int(content_length)
        elif hasattr(request, 'body') and request.body:
            request_size = len(request.body)

        # 2. Execute inner middleware / view processing
        response = self.get_response(request)

        # 3. Calculate latency in milliseconds
        response_ms = max(1, int((time.time() - start_time) * 1000))

        # 4. Resolve client IP address
        x_forwarded_for = request.META.get('HTTP_X_FORWARDED_FOR')
        if x_forwarded_for:
            ip_address = x_forwarded_for.split(',')[0].strip()
        else:
            ip_address = request.META.get('REMOTE_ADDR')

        # 5. Resolve partner identity (attached during DRF HMAC authentication)
        partner = getattr(request, 'partner', None) or getattr(request, 'user', None)
        partner_id = getattr(partner, 'pk', None)

        if not partner_id:
            # Fallback: check if public X-API-Key-ID / X-API-Key was transmitted in request headers
            api_key = (
                request.headers.get('X-API-Key-ID') or 
                request.META.get('HTTP_X_API_KEY_ID') or 
                request.headers.get('x-api-key-id') or
                request.headers.get('X-API-Key') or 
                request.META.get('HTTP_X_API_KEY') or 
                request.headers.get('x-api-key')
            )
            if api_key:
                try:
                    from .models import ChannelPartner
                    cp = ChannelPartner.objects.filter(api_key=str(api_key).strip()).first()
                    if cp:
                        partner_id = cp.pk
                except Exception:
                    pass

        # 6. Calculate response payload size
        response_size = 0
        if hasattr(response, 'content') and response.content is not None:
            response_size = len(response.content)
        elif 'Content-Length' in response:
            try:
                response_size = int(response['Content-Length'])
            except (ValueError, TypeError):
                response_size = 0

        # 7. Asynchronously trigger the Celery logging task
        try:
            from .tasks import log_api_request_task
            log_api_request_task.delay(
                endpoint=request.get_full_path(),
                method=request.method,
                status_code=response.status_code,
                response_ms=response_ms,
                partner_id=partner_id,
                ip_address=ip_address,
                request_size=request_size,
                response_size=response_size,
            )
        except Exception as exc:
            logger.warning(f"Could not dispatch asynchronous API usage log task: {exc}")

        return response

