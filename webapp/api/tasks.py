import hmac
import hashlib
import json
import logging
import requests
from django.utils import timezone
from celery import shared_task
from .models import APIUsageLog, ChannelPartner, WebhookEvent, WebhookSubscription

logger = logging.getLogger(__name__)


@shared_task(name='api.tasks.log_api_request_task')
def log_api_request_task(
    endpoint=None,
    method="GET",
    status_code=200,
    response_ms=0,
    partner_id=None,
    ip_address=None,
    request_size=0,
    response_size=0,
    *args,
    **kwargs
):
    """
    Celery task to asynchronously record an API request log into the APIUsageLog table.
    Accepts both keyword and positional arguments flexibly.
    """
    if endpoint is not None and not isinstance(endpoint, str):
        actual_partner_id = endpoint
        actual_endpoint = method
        actual_method = status_code if isinstance(status_code, str) else "GET"
        actual_status_code = response_ms if isinstance(response_ms, int) else 200
        actual_response_ms = partner_id if isinstance(partner_id, int) else 0
        actual_ip_address = ip_address
        actual_request_size = request_size
        actual_response_size = response_size
    else:
        actual_partner_id = partner_id or kwargs.get('partner_id')
        actual_endpoint = endpoint or kwargs.get('endpoint', "")
        actual_method = method or kwargs.get('method', "GET")
        actual_status_code = status_code if status_code is not None else kwargs.get('status_code', 200)
        actual_response_ms = response_ms if response_ms is not None else kwargs.get('response_ms', 0)
        actual_ip_address = ip_address or kwargs.get('ip_address')
        actual_request_size = request_size if request_size is not None else kwargs.get('request_size', 0)
        actual_response_size = response_size if response_size is not None else kwargs.get('response_size', 0)

    try:
        partner = None
        if actual_partner_id:
            partner = ChannelPartner.objects.filter(pk=actual_partner_id).first()

        log_entry = APIUsageLog.objects.create(
            partner=partner,
            endpoint=str(actual_endpoint)[:512] if actual_endpoint else "",
            method=str(actual_method)[:16].upper() if actual_method else "GET",
            status_code=int(actual_status_code) if actual_status_code is not None else 200,
            response_ms=int(actual_response_ms) if actual_response_ms is not None else 0,
            ip_address=actual_ip_address,
            request_size=int(actual_request_size) if actual_request_size else 0,
            response_size=int(actual_response_size) if actual_response_size else 0,
        )
        return log_entry.pk
    except Exception as exc:
        logger.error(f"Failed to record API usage log asynchronously: {exc}", exc_info=True)
        return None


# PRD exponential backoff intervals for up to 5 retries: 1 min, 2 min, 4 min, 8 min, 16 min
WEBHOOK_BACKOFF_SCHEDULE = [60, 120, 240, 480, 960]


@shared_task(bind=True, max_retries=5, name='api.tasks.deliver_webhook_event')
def deliver_webhook_event(self, event_id):
    """
    Celery task to deliver an HTTP POST webhook notification to a partner's target_url.
    Constructs a JSON payload containing the event type (score_updated or anomaly_flagged),
    the affected company symbol, and the relevant data payload.
    Signs the JSON payload with HMAC-SHA256 using the partner's secret_key in X-Bluestock-Signature.
    Retries failed attempts using the exact PRD exponential backoff schedule:
    up to 5 retries at intervals of 1 min, 2 min, 4 min, 8 min, and 16 min.
    Updates the WebhookEvent database record with HTTP status code, response body,
    and delivery status.
    """
    event = WebhookEvent.objects.select_related('subscription__partner').filter(pk=event_id).first()
    if not event:
        logger.error(f"WebhookEvent with ID {event_id} not found.")
        return False

    subscription = event.subscription
    partner = subscription.partner
    target_url = subscription.target_url

    # Retrieve partner secret key (supports secret_key and key_secret aliases)
    secret_key = getattr(partner, 'secret_key', None) or getattr(partner, 'key_secret', None) or ""

    # Construct / normalize structured JSON payload:
    # event type (score_updated or anomaly_flagged), affected company symbol, and relevant data
    raw_payload = event.payload
    if isinstance(raw_payload, str):
        try:
            payload_dict = json.loads(raw_payload)
        except Exception:
            payload_dict = {"data": raw_payload}
    elif isinstance(raw_payload, dict):
        payload_dict = dict(raw_payload)
    else:
        payload_dict = {}

    # Event type: score_updated or anomaly_flagged
    event_type = payload_dict.get('event') or payload_dict.get('event_type') or subscription.event_type
    payload_dict['event'] = event_type
    payload_dict['event_type'] = event_type

    # Affected company symbol
    if 'symbol' not in payload_dict:
        company_val = payload_dict.get('company')
        if isinstance(company_val, dict) and 'symbol' in company_val:
            payload_dict['symbol'] = company_val['symbol']
        elif isinstance(company_val, str) and company_val:
            payload_dict['symbol'] = company_val
        else:
            payload_dict['symbol'] = getattr(subscription, 'symbol', '') or 'N/A'

    # Relevant data payload
    if 'data' not in payload_dict:
        payload_dict['data'] = {
            k: v for k, v in payload_dict.items()
            if k not in ('event', 'event_type', 'symbol', 'timestamp', 'company')
        }

    # Canonical JSON string representation for stable HMAC signing
    payload_str = json.dumps(payload_dict, sort_keys=True, separators=(',', ':'))
    payload_bytes = payload_str.encode('utf-8')
    secret_bytes = secret_key.encode('utf-8')

    # Generate HMAC-SHA256 signature
    signature = hmac.new(secret_bytes, payload_bytes, hashlib.sha256).hexdigest()

    headers = {
        'Content-Type': 'application/json',
        'X-Bluestock-Signature': signature,
        'User-Agent': 'NiftyIQ-Webhook-Delivery/2.0',
    }

    # Current retry attempt index (0 on first attempt)
    retries = getattr(self.request, 'retries', 0) if hasattr(self, 'request') else getattr(event, 'retry_count', 0)

    try:
        # Strict timeout: 5 seconds
        response = requests.post(
            target_url,
            data=payload_bytes,
            headers=headers,
            timeout=5
        )

        resp_status_code = response.status_code
        resp_body = response.text[:2000] if response.text else ""

        # Update event record with HTTP status code and response body
        if hasattr(event, 'status_code'):
            event.status_code = resp_status_code
        if hasattr(event, 'response_body'):
            event.response_body = resp_body

        if resp_status_code >= 400:
            raise requests.HTTPError(
                f"HTTP {resp_status_code}: {resp_body[:200]}",
                response=response
            )

        # Successful delivery
        event.status = WebhookEvent.STATUS_SUCCESS
        event.retry_count = retries
        event.last_error = None
        event.delivered_at = timezone.now()

        update_fields = ['status', 'retry_count', 'last_error', 'delivered_at']
        if hasattr(event, 'status_code'):
            update_fields.append('status_code')
        if hasattr(event, 'response_body'):
            update_fields.append('response_body')

        try:
            event.save(update_fields=update_fields)
        except Exception:
            event.save(update_fields=['status', 'retry_count', 'last_error', 'delivered_at'])

        logger.info(f"Webhook #{event_id} successfully delivered to {target_url} (HTTP {resp_status_code}).")
        return True

    except Exception as exc:
        event.retry_count = retries + 1
        event.last_error = str(exc)

        # Extract status code and body from exception if available
        if hasattr(exc, 'response') and exc.response is not None:
            if hasattr(event, 'status_code'):
                event.status_code = exc.response.status_code
            if hasattr(event, 'response_body'):
                event.response_body = exc.response.text[:2000] if exc.response.text else ""

        # Exact PRD exponential backoff intervals: 1 min, 2 min, 4 min, 8 min, 16 min
        countdown = (
            WEBHOOK_BACKOFF_SCHEDULE[retries]
            if retries < len(WEBHOOK_BACKOFF_SCHEDULE)
            else 60 * (2 ** retries)
        )

        update_fields = ['status', 'retry_count', 'last_error']
        if hasattr(event, 'status_code'):
            update_fields.append('status_code')
        if hasattr(event, 'response_body'):
            update_fields.append('response_body')

        if retries >= getattr(self, 'max_retries', 5):
            event.status = WebhookEvent.STATUS_FAILED
            try:
                event.save(update_fields=update_fields)
            except Exception:
                event.save(update_fields=['status', 'retry_count', 'last_error'])
            logger.error(f"Webhook #{event_id} permanently failed after {getattr(self, 'max_retries', 5)} retries: {exc}")
            return False
        else:
            event.status = WebhookEvent.STATUS_PENDING
            try:
                event.save(update_fields=update_fields)
            except Exception:
                event.save(update_fields=['status', 'retry_count', 'last_error'])
            logger.warning(
                f"Webhook #{event_id} delivery failed (attempt {retries + 1}/{getattr(self, 'max_retries', 5)}). "
                f"Retrying in {countdown}s: {exc}"
            )
            if hasattr(self, 'retry'):
                raise self.retry(exc=exc, countdown=countdown)
            return False


# Backward compatibility alias
deliver_webhook_task = deliver_webhook_event


def dispatch_webhook_event(subscription, payload):
    """
    Helper function to create a WebhookEvent and enqueue deliver_webhook_event Celery task.
    """
    event = WebhookEvent.objects.create(
        subscription=subscription,
        payload=payload,
        status=WebhookEvent.STATUS_PENDING,
        retry_count=0
    )
    try:
        deliver_webhook_event.delay(event.pk)
    except Exception as exc:
        logger.warning(f"Could not queue deliver_webhook_event via Celery: {exc}")
    return event


def trigger_webhook_events(event_type, symbol, data_payload=None):
    """
    Dispatches webhooks to all active subscriptions matching event_type (score_updated or anomaly_flagged).
    """
    subscriptions = WebhookSubscription.objects.filter(
        event_type=event_type,
        is_active=True
    ).select_related('partner')

    payload = {
        "event": event_type,
        "event_type": event_type,
        "symbol": symbol,
        "timestamp": timezone.now().isoformat(),
        "data": data_payload or {}
    }

    events = []
    for sub in subscriptions:
        ev = dispatch_webhook_event(sub, payload)
        events.append(ev)
    return events
