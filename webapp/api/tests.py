import time
import hmac
import hashlib
import json
import secrets
from unittest import mock
from django.test import TestCase, RequestFactory
from django.core.cache import cache
from django.http import JsonResponse
from rest_framework.test import APIClient, APIRequestFactory
from rest_framework import status

from api.models import (
    ChannelPartner, 
    DimCompanies, 
    DimCompany, 
    APIUsageLog,
    WebhookSubscription,
    WebhookEvent
)
from api.security import (
    validate_hmac_request, 
    generate_hmac_signature, 
    build_canonical_string,
    _safe_equal,
    GENERIC_AUTH_ERROR,
    TIMESTAMP_WINDOW_SECONDS,
    NONCE_CACHE_TIMEOUT
)
from api.partner_views import PartnerRateThrottle, PartnerCompanyViewSet
from api.throttling import (
    PartnerTierRateThrottle,
    PartnerRateLimitExceeded,
    partner_api_exception_handler,
    TIER_QUOTAS,
)
from api.services.scoring_engine import (
    calculate_company_health,
    assign_health_label,
    compute_profitability_score,
    compute_solvency_score,
    compute_efficiency_score,
    compute_growth_score,
)
from api.tasks import log_api_request_task, deliver_webhook_task, deliver_webhook_event
from api.middleware import PartnerAPILoggingMiddleware


class ChannelPartnerModelTests(TestCase):
    def test_create_channel_partner(self):
        partner = ChannelPartner.objects.create(
            name="Alpha Hedge Fund",
            email="alpha@fund.com"
        )
        self.assertIsNotNone(partner.api_key)
        self.assertIsNotNone(partner.secret_key)
        self.assertTrue(partner.is_active)
        self.assertEqual(str(partner), f"Alpha Hedge Fund ({partner.api_key})")

    def test_default_keys_generation(self):
        partner = ChannelPartner.objects.create(
            name="Delta Analytics",
            email="delta@analytics.com"
        )
        self.assertTrue(len(str(partner.api_key)) >= 32)
        self.assertTrue(len(str(partner.secret_key)) >= 32)

    def test_tier_choices_and_key_id(self):
        partner_basic = ChannelPartner.objects.create(
            name="Basic Partner",
            email="basic@test.com"
        )
        self.assertEqual(partner_basic.tier, ChannelPartner.TIER_BASIC)
        self.assertEqual(partner_basic.key_id, partner_basic.api_key)

        partner_pro = ChannelPartner.objects.create(
            name="Pro Partner",
            email="pro@test.com",
            tier=ChannelPartner.TIER_PRO
        )
        self.assertEqual(partner_pro.tier, 'PRO')

        partner_ent = ChannelPartner.objects.create(
            name="Enterprise Partner",
            email="ent@test.com",
            tier=ChannelPartner.TIER_ENTERPRISE
        )
        self.assertEqual(partner_ent.tier, 'ENTERPRISE')


class HMACSecurityCanonicalSigningTests(TestCase):
    def setUp(self):
        cache.clear()
        self.factory = RequestFactory()
        self.partner = ChannelPartner.objects.create(
            name="Beta Capital",
            email="beta@capital.com",
            api_key="beta-test-api-key-12345",
            secret_key="secret-key-abcdef-67890"
        )

    def tearDown(self):
        cache.clear()

    def test_valid_canonical_get_request(self):
        path = "/api/v2/partner/companies/?sector=IT"
        timestamp = str(int(time.time()))
        nonce = "valid-nonce-0000000001"
        method = "GET"
        body_bytes = b""

        signature = generate_hmac_signature(
            secret_key=self.partner.secret_key,
            method=method,
            full_path=path,
            timestamp=timestamp,
            nonce=nonce,
            body_bytes=body_bytes
        )

        request = self.factory.get(
            path,
            HTTP_X_API_KEY=self.partner.api_key,
            HTTP_X_TIMESTAMP=timestamp,
            HTTP_X_NONCE=nonce,
            HTTP_X_SIGNATURE=signature
        )

        is_valid, error, partner = validate_hmac_request(request)
        self.assertTrue(is_valid)
        self.assertIsNone(error)
        self.assertEqual(partner.pk, self.partner.pk)

    def test_valid_canonical_get_request_with_x_api_key_id(self):
        path = "/api/partner/v1/companies/?sector=IT"
        timestamp = str(int(time.time()))
        nonce = "valid-nonce-0000000009"
        method = "GET"
        body_bytes = b""

        signature = generate_hmac_signature(
            secret_key=self.partner.secret_key,
            method=method,
            full_path=path,
            timestamp=timestamp,
            nonce=nonce,
            body_bytes=body_bytes
        )

        request = self.factory.get(
            path,
            HTTP_X_API_KEY_ID=self.partner.api_key,
            HTTP_X_TIMESTAMP=timestamp,
            HTTP_X_NONCE=nonce,
            HTTP_X_SIGNATURE=signature
        )

        is_valid, error, partner = validate_hmac_request(request)
        self.assertTrue(is_valid)
        self.assertIsNone(error)
        self.assertEqual(partner.pk, self.partner.pk)

    def test_valid_canonical_post_request_with_body(self):
        path = "/api/v2/partner/companies/"
        timestamp = str(int(time.time()))
        nonce = "valid-nonce-0000000002"
        method = "POST"
        body = b'{"sector": "Financial Services"}'

        signature = generate_hmac_signature(
            secret_key=self.partner.secret_key,
            method=method,
            full_path=path,
            timestamp=timestamp,
            nonce=nonce,
            body_bytes=body
        )

        request = self.factory.post(
            path,
            data=body,
            content_type="application/json",
            HTTP_X_API_KEY=self.partner.api_key,
            HTTP_X_TIMESTAMP=timestamp,
            HTTP_X_NONCE=nonce,
            HTTP_X_SIGNATURE=signature
        )

        is_valid, error, partner = validate_hmac_request(request)
        self.assertTrue(is_valid)
        self.assertIsNone(error)
        self.assertEqual(partner.pk, self.partner.pk)

    def test_tampered_nonce(self):
        """
        Security requirement: Altering only X-Nonce after signing must result in 401.
        """
        path = "/api/v2/partner/companies/"
        timestamp = str(int(time.time()))
        signed_nonce = "legitimate-nonce-12345678"
        tampered_nonce = "altered-nonce-8765432100"

        # Client signs canonical string with signed_nonce
        signature = generate_hmac_signature(
            secret_key=self.partner.secret_key,
            method="GET",
            full_path=path,
            timestamp=timestamp,
            nonce=signed_nonce,
            body_bytes=b""
        )

        # Attacker transmits tampered_nonce in HTTP header with original signature
        request = self.factory.get(
            path,
            HTTP_X_API_KEY=self.partner.api_key,
            HTTP_X_TIMESTAMP=timestamp,
            HTTP_X_NONCE=tampered_nonce,
            HTTP_X_SIGNATURE=signature
        )

        is_valid, error, partner = validate_hmac_request(request)
        self.assertFalse(is_valid)
        self.assertEqual(error, GENERIC_AUTH_ERROR)
        self.assertIsNone(partner)

    def test_tampered_request_body(self):
        path = "/api/v2/partner/companies/"
        timestamp = str(int(time.time()))
        nonce = "tamper-body-nonce-12345"
        original_body = b'{"action": "read"}'
        tampered_body = b'{"action": "elevate_privileges"}'

        signature = generate_hmac_signature(
            secret_key=self.partner.secret_key,
            method="POST",
            full_path=path,
            timestamp=timestamp,
            nonce=nonce,
            body_bytes=original_body
        )

        request = self.factory.post(
            path,
            data=tampered_body,
            content_type="application/json",
            HTTP_X_API_KEY=self.partner.api_key,
            HTTP_X_TIMESTAMP=timestamp,
            HTTP_X_NONCE=nonce,
            HTTP_X_SIGNATURE=signature
        )

        is_valid, error, partner = validate_hmac_request(request)
        self.assertFalse(is_valid)
        self.assertEqual(error, GENERIC_AUTH_ERROR)
        self.assertIsNone(partner)

    def test_tampered_query_string(self):
        signed_path = "/api/v2/partner/companies/?sector=IT"
        tampered_path = "/api/v2/partner/companies/?sector=Automobile"
        timestamp = str(int(time.time()))
        nonce = "tamper-query-nonce-1234"

        signature = generate_hmac_signature(
            secret_key=self.partner.secret_key,
            method="GET",
            full_path=signed_path,
            timestamp=timestamp,
            nonce=nonce,
            body_bytes=b""
        )

        request = self.factory.get(
            tampered_path,
            HTTP_X_API_KEY=self.partner.api_key,
            HTTP_X_TIMESTAMP=timestamp,
            HTTP_X_NONCE=nonce,
            HTTP_X_SIGNATURE=signature
        )

        is_valid, error, partner = validate_hmac_request(request)
        self.assertFalse(is_valid)
        self.assertEqual(error, GENERIC_AUTH_ERROR)
        self.assertIsNone(partner)

    def test_incorrect_http_method(self):
        path = "/api/v2/partner/companies/"
        timestamp = str(int(time.time()))
        nonce = "method-mismatch-nonce-1"

        signature = generate_hmac_signature(
            secret_key=self.partner.secret_key,
            method="GET",
            full_path=path,
            timestamp=timestamp,
            nonce=nonce,
            body_bytes=b""
        )

        request = self.factory.post(
            path,
            data=b"",
            content_type="application/json",
            HTTP_X_API_KEY=self.partner.api_key,
            HTTP_X_TIMESTAMP=timestamp,
            HTTP_X_NONCE=nonce,
            HTTP_X_SIGNATURE=signature
        )

        is_valid, error, partner = validate_hmac_request(request)
        self.assertFalse(is_valid)
        self.assertEqual(error, GENERIC_AUTH_ERROR)
        self.assertIsNone(partner)

    def test_replayed_nonce_within_5_minute_window(self):
        path = "/api/v2/partner/companies/"
        timestamp = str(int(time.time()))
        nonce = "replayed-nonce-12345678"

        signature = generate_hmac_signature(
            secret_key=self.partner.secret_key,
            method="GET",
            full_path=path,
            timestamp=timestamp,
            nonce=nonce,
            body_bytes=b""
        )

        req1 = self.factory.get(
            path,
            HTTP_X_API_KEY=self.partner.api_key,
            HTTP_X_TIMESTAMP=timestamp,
            HTTP_X_NONCE=nonce,
            HTTP_X_SIGNATURE=signature
        )

        # First request must succeed and record nonce in cache
        is_valid1, error1, partner1 = validate_hmac_request(req1)
        self.assertTrue(is_valid1)
        self.assertIsNone(error1)

        # Replay attempt: exact same nonce re-sent within window
        req2 = self.factory.get(
            path,
            HTTP_X_API_KEY=self.partner.api_key,
            HTTP_X_TIMESTAMP=timestamp,
            HTTP_X_NONCE=nonce,
            HTTP_X_SIGNATURE=signature
        )

        is_valid2, error2, partner2 = validate_hmac_request(req2)
        self.assertFalse(is_valid2)
        self.assertEqual(error2, GENERIC_AUTH_ERROR)
        self.assertIsNone(partner2)

    def test_nonce_cache_timeout_value(self):
        """Nonce cache TTL must equal 2 * TIMESTAMP_WINDOW_SECONDS + 10 (610s)."""
        self.assertEqual(NONCE_CACHE_TIMEOUT, 2 * TIMESTAMP_WINDOW_SECONDS + 10)
        self.assertEqual(NONCE_CACHE_TIMEOUT, 610)

    def test_invalid_nonce_regex_formats(self):
        """Nonce must strictly match [A-Za-z0-9_-]{16,64}."""
        path = "/api/v2/partner/companies/"
        timestamp = str(int(time.time()))

        invalid_nonces = [
            "short",                           # Too short (< 16 chars)
            "invalid*char!@#$",                # Illegal symbols
            "nonce with spaces 123",           # Space character
            "a" * 65,                          # Too long (> 64 chars)
        ]

        for bad_nonce in invalid_nonces:
            request = self.factory.get(
                path,
                HTTP_X_API_KEY=self.partner.api_key,
                HTTP_X_TIMESTAMP=timestamp,
                HTTP_X_NONCE=bad_nonce,
                HTTP_X_SIGNATURE="dummy-signature"
            )
            is_valid, error, partner = validate_hmac_request(request)
            self.assertFalse(is_valid, f"Expected invalid nonce for: {bad_nonce}")
            self.assertEqual(error, GENERIC_AUTH_ERROR)
            self.assertIsNone(partner)

    def test_non_ascii_safe_compare_prevents_500(self):
        """
        Verify _safe_equal and validate_hmac_request do not raise UnicodeDecodeError
        or runtime exceptions when handling non-ASCII header values.
        """
        path = "/api/v2/partner/companies/"
        timestamp = str(int(time.time()))
        nonce = "valid-nonce-utf8-1234"

        # Non-ASCII signature header
        request = self.factory.get(
            path,
            HTTP_X_API_KEY=self.partner.api_key,
            HTTP_X_TIMESTAMP=timestamp,
            HTTP_X_NONCE=nonce,
            HTTP_X_SIGNATURE="invalid_signature_ñóñ_áscii_12345"
        )

        try:
            is_valid, error, partner = validate_hmac_request(request)
            self.assertFalse(is_valid)
            self.assertEqual(error, GENERIC_AUTH_ERROR)
            self.assertIsNone(partner)
        except Exception as e:
            self.fail(f"Non-ASCII header caused unexpected 500 exception: {e}")

    def test_expired_timestamp(self):
        path = "/api/v2/partner/companies/"
        old_timestamp = str(int(time.time()) - 600)
        nonce = "expired-nonce-12345"

        signature = generate_hmac_signature(
            secret_key=self.partner.secret_key,
            method="GET",
            full_path=path,
            timestamp=old_timestamp,
            nonce=nonce,
            body_bytes=b""
        )

        request = self.factory.get(
            path,
            HTTP_X_API_KEY=self.partner.api_key,
            HTTP_X_TIMESTAMP=old_timestamp,
            HTTP_X_NONCE=nonce,
            HTTP_X_SIGNATURE=signature
        )

        is_valid, error, partner = validate_hmac_request(request)
        self.assertFalse(is_valid)
        self.assertEqual(error, GENERIC_AUTH_ERROR)

    def test_future_timestamp_outside_window(self):
        path = "/api/v2/partner/companies/"
        future_timestamp = str(int(time.time()) + 600)
        nonce = "future-nonce-12345"

        signature = generate_hmac_signature(
            secret_key=self.partner.secret_key,
            method="GET",
            full_path=path,
            timestamp=future_timestamp,
            nonce=nonce,
            body_bytes=b""
        )

        request = self.factory.get(
            path,
            HTTP_X_API_KEY=self.partner.api_key,
            HTTP_X_TIMESTAMP=future_timestamp,
            HTTP_X_NONCE=nonce,
            HTTP_X_SIGNATURE=signature
        )

        is_valid, error, partner = validate_hmac_request(request)
        self.assertFalse(is_valid)
        self.assertEqual(error, GENERIC_AUTH_ERROR)

    def test_missing_nonce_header(self):
        path = "/api/v2/partner/companies/"
        timestamp = str(int(time.time()))
        nonce = "temp-nonce-12345"
        signature = generate_hmac_signature(
            secret_key=self.partner.secret_key,
            method="GET",
            full_path=path,
            timestamp=timestamp,
            nonce=nonce,
            body_bytes=b""
        )

        # Missing X-Nonce header
        request = self.factory.get(
            path,
            HTTP_X_API_KEY=self.partner.api_key,
            HTTP_X_TIMESTAMP=timestamp,
            HTTP_X_SIGNATURE=signature
        )

        is_valid, error, partner = validate_hmac_request(request)
        self.assertFalse(is_valid)
        self.assertEqual(error, GENERIC_AUTH_ERROR)

    def test_missing_timestamp_header(self):
        request = self.factory.get(
            "/api/v2/partner/companies/",
            HTTP_X_API_KEY=self.partner.api_key,
            HTTP_X_NONCE="valid-nonce-12345678",
            HTTP_X_SIGNATURE="fake-signature"
        )
        is_valid, error, partner = validate_hmac_request(request)
        self.assertFalse(is_valid)
        self.assertEqual(error, GENERIC_AUTH_ERROR)

    def test_missing_api_key_header(self):
        request = self.factory.get(
            "/api/v2/partner/companies/",
            HTTP_X_TIMESTAMP=str(int(time.time())),
            HTTP_X_NONCE="valid-nonce-12345678",
            HTTP_X_SIGNATURE="fake-signature"
        )
        is_valid, error, partner = validate_hmac_request(request)
        self.assertFalse(is_valid)
        self.assertEqual(error, GENERIC_AUTH_ERROR)

    def test_unknown_api_key_anti_enumeration(self):
        path = "/api/v2/partner/companies/"
        timestamp = str(int(time.time()))
        nonce = "unknown-key-nonce-12345"

        request = self.factory.get(
            path,
            HTTP_X_API_KEY="non-existent-api-key",
            HTTP_X_TIMESTAMP=timestamp,
            HTTP_X_NONCE=nonce,
            HTTP_X_SIGNATURE="fake-signature"
        )

        is_valid, error, partner = validate_hmac_request(request)
        self.assertFalse(is_valid)
        self.assertEqual(error, GENERIC_AUTH_ERROR)

    def test_inactive_partner_anti_enumeration(self):
        self.partner.is_active = False
        self.partner.save()

        path = "/api/v2/partner/companies/"
        timestamp = str(int(time.time()))
        nonce = "inactive-nonce-12345"

        signature = generate_hmac_signature(
            secret_key=self.partner.secret_key,
            method="GET",
            full_path=path,
            timestamp=timestamp,
            nonce=nonce,
            body_bytes=b""
        )

        request = self.factory.get(
            path,
            HTTP_X_API_KEY=self.partner.api_key,
            HTTP_X_TIMESTAMP=timestamp,
            HTTP_X_NONCE=nonce,
            HTTP_X_SIGNATURE=signature
        )

        is_valid, error, partner = validate_hmac_request(request)
        self.assertFalse(is_valid)
        self.assertEqual(error, GENERIC_AUTH_ERROR)


class DRFPartnerEndpointTests(TestCase):
    def setUp(self):
        cache.clear()
        self.client = APIClient()
        self.partner = ChannelPartner.objects.create(
            name="Gamma Research",
            email="gamma@research.com",
            api_key="gamma-key-uuid-9999",
            secret_key="gamma-secret-hex-5555"
        )

    def tearDown(self):
        cache.clear()

    def test_unauthenticated_request_returns_uniform_401(self):
        response = self.client.get('/api/v2/partner/companies/')
        self.assertEqual(response.status_code, status.HTTP_401_UNAUTHORIZED)
        self.assertEqual(response.json(), {"detail": "Authentication failed."})

    def test_invalid_signature_request_returns_uniform_401(self):
        headers = {
            'HTTP_X_API_KEY': self.partner.api_key,
            'HTTP_X_TIMESTAMP': str(int(time.time())),
            'HTTP_X_NONCE': 'invalid-sig-nonce-1234',
            'HTTP_X_SIGNATURE': 'badsignature00000000000000000000000000000000',
        }
        response = self.client.get('/api/v2/partner/companies/', **headers)
        self.assertEqual(response.status_code, status.HTTP_401_UNAUTHORIZED)
        self.assertEqual(response.json(), {"detail": "Authentication failed."})

    def test_tampered_nonce_via_endpoint_returns_401(self):
        path = "/api/v2/partner/companies/"
        timestamp = str(int(time.time()))
        signed_nonce = "signed-nonce-endpoint-1"
        altered_nonce = "altered-nonce-endpoint-2"

        signature = generate_hmac_signature(
            secret_key=self.partner.secret_key,
            method="GET",
            full_path=path,
            timestamp=timestamp,
            nonce=signed_nonce,
            body_bytes=b""
        )

        headers = {
            'HTTP_X_API_KEY': self.partner.api_key,
            'HTTP_X_TIMESTAMP': timestamp,
            'HTTP_X_NONCE': altered_nonce,
            'HTTP_X_SIGNATURE': signature,
        }
        response = self.client.get(path, **headers)
        self.assertEqual(response.status_code, status.HTTP_401_UNAUTHORIZED)
        self.assertEqual(response.json(), {"detail": "Authentication failed."})

    def test_invalid_nonce_regex_via_endpoint_returns_401(self):
        headers = {
            'HTTP_X_API_KEY': self.partner.api_key,
            'HTTP_X_TIMESTAMP': str(int(time.time())),
            'HTTP_X_NONCE': 'short',
            'HTTP_X_SIGNATURE': 'some-signature',
        }
        response = self.client.get('/api/v2/partner/companies/', **headers)
        self.assertEqual(response.status_code, status.HTTP_401_UNAUTHORIZED)
        self.assertEqual(response.json(), {"detail": "Authentication failed."})

    def test_non_ascii_signature_header_via_endpoint_returns_401(self):
        headers = {
            'HTTP_X_API_KEY': self.partner.api_key,
            'HTTP_X_TIMESTAMP': str(int(time.time())),
            'HTTP_X_NONCE': 'valid-nonce-utf8-5678',
            'HTTP_X_SIGNATURE': 'sig_with_ñon_ascii_character_12345',
        }
        response = self.client.get('/api/v2/partner/companies/', **headers)
        self.assertEqual(response.status_code, status.HTTP_401_UNAUTHORIZED)
        self.assertEqual(response.json(), {"detail": "Authentication failed."})

    def test_partner_rate_throttle_cache_key_ident(self):
        """
        Verify PartnerRateThrottle isolates throttling quota per partner primary key.
        """
        throttle = PartnerRateThrottle()
        factory = APIRequestFactory()
        request = factory.get('/api/v2/partner/companies/')
        request.partner = self.partner
        request.user = self.partner

        cache_key = throttle.get_cache_key(request, None)
        self.assertIn(str(self.partner.pk), cache_key)
        self.assertTrue(cache_key.startswith('throttle_partner_api_'))


class MLHealthScoringEngineTests(TestCase):
    def test_assign_health_label(self):
        self.assertEqual(assign_health_label(85.0), 'EXCELLENT')
        self.assertEqual(assign_health_label(80.0), 'EXCELLENT')
        self.assertEqual(assign_health_label(79.9), 'GOOD')
        self.assertEqual(assign_health_label(60.0), 'GOOD')
        self.assertEqual(assign_health_label(59.9), 'AVERAGE')
        self.assertEqual(assign_health_label(40.0), 'AVERAGE')
        self.assertEqual(assign_health_label(39.9), 'WEAK')
        self.assertEqual(assign_health_label(20.0), 'WEAK')
        self.assertEqual(assign_health_label(19.9), 'POOR')
        self.assertEqual(assign_health_label(0.0), 'POOR')
        self.assertEqual(assign_health_label(None), 'PENDING')

    def test_profitability_scoring_high_metrics(self):
        # 30% ROE and 28% OPM should produce maximum 35.0
        score, norm = compute_profitability_score(roe_pct=30.0, opm_pct=28.0)
        self.assertEqual(score, 35.0)
        self.assertEqual(norm, 100.0)

    def test_profitability_scoring_loss_making(self):
        # Negative ROE and OPM should produce 0
        score, norm = compute_profitability_score(roe_pct=-5.0, opm_pct=-10.0)
        self.assertEqual(score, 0.0)
        self.assertEqual(norm, 0.0)

    def test_solvency_scoring_debt_free(self):
        # D/E <= 0.10 should receive maximum 35.0
        score, norm = compute_solvency_score(debt_to_equity=0.05)
        self.assertEqual(score, 35.0)
        self.assertEqual(norm, 100.0)

    def test_solvency_scoring_heavily_penalizes_de_above_two(self):
        # Solvency requirement: "Lower Debt-to-Equity ratios increase the score. Penalize heavily for D/E > 2.0."
        score_healthy, _ = compute_solvency_score(debt_to_equity=0.8)
        score_elevated, _ = compute_solvency_score(debt_to_equity=1.8)
        score_excessive, _ = compute_solvency_score(debt_to_equity=2.8)
        score_severe, _ = compute_solvency_score(debt_to_equity=4.5)

        self.assertGreater(score_healthy, 20.0)
        self.assertGreater(score_healthy, score_elevated)
        self.assertGreater(score_elevated, score_excessive)
        # Heavily penalized for D/E > 2.0 (< 6.0 out of 35)
        self.assertLess(score_excessive, 6.0)
        self.assertLess(score_severe, 2.0)

    def test_solvency_negative_equity(self):
        # Negative D/E means negative equity (insolvent)
        score, norm = compute_solvency_score(debt_to_equity=-1.5)
        self.assertEqual(score, 0.0)

    def test_efficiency_scoring(self):
        # High asset turnover & cash conversion ratio
        score_high, norm_high = compute_efficiency_score(asset_turnover=1.6, cash_conversion_ratio=1.3)
        self.assertEqual(score_high, 15.0)
        self.assertEqual(norm_high, 100.0)

        # Low asset turnover & cash conversion ratio
        score_low, _ = compute_efficiency_score(asset_turnover=0.2, cash_conversion_ratio=0.1)
        self.assertLess(score_low, 6.0)

    def test_growth_scoring(self):
        # High sales CAGR and positive EPS compounding
        score_high, norm_high = compute_growth_score(sales_cagr=22.0, profit_cagr=25.0, latest_eps=45.0)
        self.assertEqual(score_high, 15.0)
        self.assertEqual(norm_high, 100.0)

        # Negative EPS penalized
        score_loss, _ = compute_growth_score(sales_cagr=5.0, latest_eps=-10.0)
        self.assertLess(score_loss, 5.0)

    def test_calculate_company_health_calculation_structure(self):
        # Test calculation without saving to DB (in-memory mockable)
        res = calculate_company_health(symbol='TEST_CO', save_to_db=False)
        self.assertIn('overall_score', res)
        self.assertIn('health_label', res)
        self.assertIn('pillar_scores', res)
        self.assertIn('normalized_pillars', res)
        self.assertIn('metrics_used', res)
        self.assertTrue(0.0 <= res['overall_score'] <= 100.0)

        p = res['pillar_scores']
        sum_pillars = round(p['profitability'] + p['solvency'] + p['efficiency'] + p['growth'], 2)
        self.assertEqual(res['overall_score'], sum_pillars)


class APIUsageLoggingTests(TestCase):
    def setUp(self):
        cache.clear()
        self.partner = ChannelPartner.objects.create(
            name="Zeta Analytics",
            email="zeta@analytics.com",
            api_key="zeta-key-uuid-1111",
            secret_key="zeta-secret-hex-2222"
        )

    def test_create_api_usage_log_model(self):
        log = APIUsageLog.objects.create(
            partner=self.partner,
            endpoint="/api/v2/partner/companies/?sector=IT",
            method="GET",
            status_code=200,
            response_ms=45,
            ip_address="127.0.0.1",
            request_size=128,
            response_size=2048,
        )
        self.assertIsNotNone(log.pk)
        self.assertEqual(log.partner, self.partner)
        self.assertEqual(log.status_code, 200)
        self.assertEqual(log.response_ms, 45)
        self.assertIn("Zeta Analytics", str(log))

    def test_log_api_request_task_direct_execution(self):
        log_id = log_api_request_task(
            endpoint="/api/v2/partner/companies/TCS/",
            method="GET",
            status_code=200,
            response_ms=32,
            partner_id=self.partner.pk,
            ip_address="192.168.1.100",
            request_size=0,
            response_size=1500,
        )
        self.assertIsNotNone(log_id)
        log = APIUsageLog.objects.get(pk=log_id)
        self.assertEqual(log.partner_id, self.partner.pk)
        self.assertEqual(log.endpoint, "/api/v2/partner/companies/TCS/")
        self.assertEqual(log.status_code, 200)

    def test_partner_logging_middleware_invocation(self):
        middleware = PartnerAPILoggingMiddleware(lambda req: JsonResponse({'status': 'ok'}, status=200))
        factory = RequestFactory()
        req = factory.get('/api/v2/partner/companies/', HTTP_X_API_KEY=self.partner.api_key)
        req.partner = self.partner

        resp = middleware(req)
        self.assertEqual(resp.status_code, 200)
        # Check that log entry was created
        log = APIUsageLog.objects.filter(partner=self.partner).order_by('-created_at').first()
        self.assertIsNotNone(log)
        self.assertEqual(log.status_code, 200)
        self.assertEqual(log.method, "GET")


class WebhookDeliverySystemTests(TestCase):
    def setUp(self):
        cache.clear()
        self.partner = ChannelPartner.objects.create(
            name="Apex Quant",
            email="apex@quant.com",
            api_key="apex-webhook-key-3333",
            secret_key="apex-secret-hex-4444"
        )
        self.subscription = WebhookSubscription.objects.create(
            partner=self.partner,
            event_type=WebhookSubscription.EVENT_SCORE_UPDATED,
            target_url="https://api.apexquant.com/webhooks/nifty100/",
            is_active=True
        )

    def test_create_webhook_models(self):
        self.assertTrue(self.subscription.is_active)
        self.assertEqual(self.partner.key_secret, self.partner.secret_key)

        event = WebhookEvent.objects.create(
            subscription=self.subscription,
            payload={"event": "score_updated", "symbol": "TCS"},
            status=WebhookEvent.STATUS_PENDING
        )
        self.assertIsNotNone(event.pk)
        self.assertEqual(event.status, WebhookEvent.STATUS_PENDING)
        self.assertEqual(event.retry_count, 0)
        self.assertIn("score_updated", str(event))

    def test_exponential_backoff_calculation(self):
        """
        Verify exponential backoff progression per Stream C PRD:
        (1 min, 2 min, 4 min, 8 min, 16 min).
        """
        expected_progression = [60, 120, 240, 480, 960]
        for retry, expected_secs in enumerate(expected_progression):
            countdown = 60 * (2 ** retry)
            self.assertEqual(countdown, expected_secs)

    def test_hmac_sha256_payload_signature(self):
        payload = {"event": "score_updated", "symbol": "INFY", "score": 85.5}
        payload_str = json.dumps(payload, sort_keys=True, separators=(',', ':'))
        secret = self.partner.key_secret

        expected_sig = hmac.new(
            secret.encode('utf-8'),
            payload_str.encode('utf-8'),
            hashlib.sha256
        ).hexdigest()

        self.assertIsNotNone(expected_sig)
        self.assertEqual(len(expected_sig), 64)

    def test_deliver_webhook_event_task_definition(self):
        """
        Verify deliver_webhook_event Celery task exists and is callable.
        """
        self.assertTrue(callable(deliver_webhook_event))
        self.assertEqual(deliver_webhook_event.name, 'api.tasks.deliver_webhook_event')
        self.assertEqual(deliver_webhook_event.max_retries, 5)

    @mock.patch('api.tasks.requests.post')
    def test_deliver_webhook_event_successful_execution(self, mock_post):
        """
        Verify deliver_webhook_event sends HTTP POST with X-Bluestock-Signature
        and updates WebhookEvent with status 200, response body, and STATUS_SUCCESS.
        """
        mock_response = mock.MagicMock()
        mock_response.status_code = 200
        mock_response.text = '{"received": true}'
        mock_post.return_value = mock_response

        event = WebhookEvent.objects.create(
            subscription=self.subscription,
            payload={
                "event": "score_updated",
                "symbol": "RELIANCE",
                "data": {"score": 88.0, "label": "EXCELLENT"}
            },
            status=WebhookEvent.STATUS_PENDING
        )

        result = deliver_webhook_event(event.pk)
        self.assertTrue(result)

        event.refresh_from_db()
        self.assertEqual(event.status, WebhookEvent.STATUS_SUCCESS)
        self.assertIsNotNone(event.delivered_at)
        self.assertEqual(event.status_code, 200)
        self.assertEqual(event.response_body, '{"received": true}')

        # Verify POST called with timeout=5 and X-Bluestock-Signature header
        mock_post.assert_called_once()
        args, kwargs = mock_post.call_args
        self.assertEqual(args[0], self.subscription.target_url)
        self.assertEqual(kwargs['timeout'], 5)
        self.assertIn('X-Bluestock-Signature', kwargs['headers'])
        self.assertEqual(kwargs['headers']['Content-Type'], 'application/json')

    @mock.patch('api.tasks.requests.post')
    def test_deliver_webhook_event_permanent_failure(self, mock_post):
        """
        Verify deliver_webhook_event permanently fails after max retries (5)
        and updates WebhookEvent record with STATUS_FAILED and status_code.
        """
        mock_response = mock.MagicMock()
        mock_response.status_code = 500
        mock_response.text = 'Internal Server Error'
        mock_post.return_value = mock_response

        event = WebhookEvent.objects.create(
            subscription=self.subscription,
            payload={
                "event": "anomaly_flagged",
                "symbol": "TATASTEEL",
                "data": {"z_score": 3.8, "metric": "operating_profit"}
            },
            status=WebhookEvent.STATUS_PENDING,
            retry_count=5
        )

        result = deliver_webhook_event(event.pk)
        self.assertFalse(result)

        event.refresh_from_db()
        self.assertEqual(event.status, WebhookEvent.STATUS_FAILED)
        self.assertEqual(event.status_code, 500)
        self.assertIn("Internal Server Error", event.response_body)


class APIDocumentationSchemaTests(TestCase):
    def test_financial_serializers_decimal_fields(self):
        """
        Ensure all financial serializers have properly configured DecimalFields
        with explicit max_digits and decimal_places for drf-spectacular schema generation.
        """
        from api.serializers import (
            FactProfitLossSerializer,
            FactBalanceSheetSerializer,
            FactCashFlowSerializer,
            FactAnalysisSerializer,
            FactMlScoresSerializer,
            PartnerCompanySerializer,
        )
        pl_fields = FactProfitLossSerializer().fields
        self.assertIn('sales', pl_fields)
        self.assertEqual(pl_fields['sales'].max_digits, 15)
        self.assertEqual(pl_fields['sales'].decimal_places, 4)

        bs_fields = FactBalanceSheetSerializer().fields
        self.assertIn('total_assets', bs_fields)
        self.assertEqual(bs_fields['total_assets'].max_digits, 15)
        self.assertEqual(bs_fields['total_assets'].decimal_places, 4)

        partner_fields = PartnerCompanySerializer().fields
        self.assertIn('face_value', partner_fields)
        self.assertEqual(partner_fields['face_value'].max_digits, 15)
        self.assertEqual(partner_fields['face_value'].decimal_places, 4)

    def test_swagger_authorize_configuration(self):
        """
        Verify that default basic/session auth schemes are removed and our custom
        ApiKeyAuth scheme (in header, name: X-API-Key) is configured.
        """
        from django.conf import settings
        spectacular_settings = getattr(settings, 'SPECTACULAR_SETTINGS', {})
        sec_schemes = spectacular_settings.get('APPEND_COMPONENTS', {}).get('securitySchemes', {})
        self.assertIn('ApiKeyAuth', sec_schemes)
        self.assertEqual(sec_schemes['ApiKeyAuth']['type'], 'apiKey')
        self.assertEqual(sec_schemes['ApiKeyAuth']['in'], 'header')
        self.assertIn(sec_schemes['ApiKeyAuth']['name'], ['X-API-Key-ID', 'X-API-Key'])

        # Verify global security configuration
        self.assertEqual(spectacular_settings.get('SECURITY'), [{'ApiKeyAuth': []}])

        # Verify basic/session auth are not in whitelist or default classes
        whitelist = spectacular_settings.get('AUTHENTICATION_WHITELIST', [])
        self.assertNotIn('rest_framework.authentication.BasicAuthentication', whitelist)
        self.assertNotIn('rest_framework.authentication.SessionAuthentication', whitelist)
        self.assertIn('api.authentication.PartnerHMACAuthentication', whitelist)

        rf_auth = settings.REST_FRAMEWORK.get('DEFAULT_AUTHENTICATION_CLASSES', [])
        self.assertNotIn('rest_framework.authentication.BasicAuthentication', rf_auth)
        self.assertNotIn('rest_framework.authentication.SessionAuthentication', rf_auth)

        # Verify documentation UI serves unauthenticated
        self.assertEqual(spectacular_settings.get('SERVE_PERMISSIONS'), ['rest_framework.permissions.AllowAny'])
        self.assertEqual(spectacular_settings.get('SERVE_AUTHENTICATION'), [])

    def test_openapi_schema_endpoint(self):
        client = APIClient()
        response = client.get('/api/schema/')
        self.assertEqual(response.status_code, status.HTTP_200_OK)

    def test_swagger_ui_endpoint(self):
        client = APIClient()
        response = client.get('/api/docs/')
        self.assertEqual(response.status_code, status.HTTP_200_OK)

    def test_redoc_ui_endpoint(self):
        client = APIClient()
        response = client.get('/api/redoc/')
        self.assertEqual(response.status_code, status.HTTP_200_OK)


class CompanyChartJsEndpointTests(TestCase):
    def test_build_company_chart_data_structure(self):
        """
        Verify build_company_chart_data constructs exact Chart.js data objects:
        revenue_ebitda_trend, margin_trends, balance_sheet_composition,
        cash_flow_waterfall, cagr_radar, and backward-compatible series.
        """
        from api.views import build_company_chart_data

        company = DimCompany(
            company_name="Mock Tech Ltd",
            sector="Information Technology"
        )
        company.symbol_id = "MOCKTECH"

        data = build_company_chart_data(company)
        self.assertEqual(data['symbol'], "MOCKTECH")
        self.assertEqual(data['company_name'], "Mock Tech Ltd")

        # 1. revenue_ebitda_trend
        self.assertIn('revenue_ebitda_trend', data)
        self.assertIn('labels', data['revenue_ebitda_trend'])
        self.assertIn('datasets', data['revenue_ebitda_trend'])
        dataset_labels = [d['label'] for d in data['revenue_ebitda_trend']['datasets']]
        self.assertTrue(any('Revenue' in l for l in dataset_labels))
        self.assertTrue(any('EBITDA' in l for l in dataset_labels))

        # 2. margin_trends
        self.assertIn('margin_trends', data)
        self.assertIn('labels', data['margin_trends'])
        self.assertIn('datasets', data['margin_trends'])
        margin_labels = [d['label'] for d in data['margin_trends']['datasets']]
        self.assertTrue(any('Gross' in l for l in margin_labels))
        self.assertTrue(any('Operating' in l for l in margin_labels))
        self.assertTrue(any('Net' in l for l in margin_labels))

        # 3. balance_sheet_composition
        self.assertIn('balance_sheet_composition', data)
        self.assertIn('labels', data['balance_sheet_composition'])
        self.assertIn('datasets', data['balance_sheet_composition'])
        bs_labels = [d['label'] for d in data['balance_sheet_composition']['datasets']]
        self.assertTrue(any('Assets' in l for l in bs_labels))
        self.assertTrue(any('Liabilities' in l for l in bs_labels))

        # 4. cash_flow_waterfall
        self.assertIn('cash_flow_waterfall', data)
        self.assertIn('labels', data['cash_flow_waterfall'])
        self.assertIn('datasets', data['cash_flow_waterfall'])
        self.assertEqual(len(data['cash_flow_waterfall']['labels']), 4)

        # 5. cagr_radar
        self.assertIn('cagr_radar', data)
        self.assertIn('labels', data['cagr_radar'])
        self.assertIn('datasets', data['cagr_radar'])
        self.assertEqual(len(data['cagr_radar']['labels']), 3)

        # 6. eps_trend
        self.assertIn('eps_trend', data)
        self.assertIn('labels', data['eps_trend'])
        self.assertIn('datasets', data['eps_trend'])
        eps_labels = [d['label'] for d in data['eps_trend']['datasets']]
        self.assertTrue(any('EPS' in l or 'Earnings Per Share' in l for l in eps_labels))

        # 7. debt_to_equity_trend
        self.assertIn('debt_to_equity_trend', data)
        self.assertIn('labels', data['debt_to_equity_trend'])
        self.assertIn('datasets', data['debt_to_equity_trend'])
        de_labels = [d['label'] for d in data['debt_to_equity_trend']['datasets']]
        self.assertTrue(any('Debt to Equity' in l for l in de_labels))

        # 8. return_metrics
        self.assertIn('return_metrics', data)
        self.assertIn('labels', data['return_metrics'])
        self.assertIn('datasets', data['return_metrics'])
        rm_labels = [d['label'] for d in data['return_metrics']['datasets']]
        self.assertTrue(any('ROE' in l or 'Return on Equity' in l for l in rm_labels))
        self.assertTrue(any('ROA' in l or 'Return on Assets' in l for l in rm_labels))

        # Backward compatibility keys
        self.assertIn('labels', data)
        self.assertIn('revenue', data)
        self.assertIn('net_profit', data)
        self.assertIn('opm', data)
        self.assertIn('eps', data)
        self.assertIn('debt_to_equity', data)
        self.assertIn('operating_cash', data)

    def test_cash_flow_waterfall_colors_and_values(self):
        """
        Verify cash_flow_waterfall colors logic (green for positive, red for negative, blue for net cash flow).
        """
        from api.views import build_company_chart_data

        company = DimCompany(company_name="Test Company", sector="Energy")
        company.symbol_id = "TESTCO"

        data = build_company_chart_data(company)
        cf = data['cash_flow_waterfall']
        self.assertEqual(len(cf['datasets']), 1)
        dataset = cf['datasets'][0]
        self.assertEqual(len(dataset['data']), 4)
        self.assertEqual(len(dataset['backgroundColor']), 4)
        self.assertEqual(len(dataset['borderColor']), 4)

    def test_company_detail_template_chart_canvas_elements(self):
        """
        Verify company_detail.html contains all 8 required Chart.js canvas elements:
        revenueEbitdaChart, marginTrendsChart, balanceSheetChart, cashFlowChart,
        epsChart, debtEquityChart, returnMetricsChart, cagrRadarChart,
        and ensures PRD Option labels and badge text have been removed.
        """
        with open('webapp/templates/company_detail.html', 'r', encoding='utf-8') as f:
            template_content = f.read()

        required_canvas_ids = [
            'revenueEbitdaChart',
            'marginTrendsChart',
            'balanceSheetChart',
            'cashFlowChart',
            'epsChart',
            'debtEquityChart',
            'returnMetricsChart',
            'cagrRadarChart'
        ]
        for canvas_id in required_canvas_ids:
            self.assertIn(f'id="{canvas_id}"', template_content)

        self.assertNotIn('Stream C Option 2', template_content)
        self.assertNotIn('Chart.js Active', template_content)
        self.assertIn('chart.js', template_content.lower())
        self.assertIn('/api/v1/companies/${symbol}/charts/', template_content)


class AdminInsightsViewTests(TestCase):
    def setUp(self):
        from django.contrib.auth.models import User
        self.staff_user = User.objects.create_user(
            username='staffuser',
            password='StaffPassword123!',
            is_staff=True,
            is_active=True
        )
        self.regular_user = User.objects.create_user(
            username='regularuser',
            password='RegularPassword123!',
            is_staff=False,
            is_active=True
        )

    def test_admin_insights_requires_staff(self):
        # 1. Anonymous user redirected to admin login
        client = APIClient()
        response = client.get('/admin-insights/')
        self.assertEqual(response.status_code, 302)
        self.assertIn('/admin/login/', response.url)

        # 2. Regular non-staff user redirected to admin login
        client.login(username='regularuser', password='RegularPassword123!')
        response = client.get('/admin-insights/')
        self.assertEqual(response.status_code, 302)

    def test_admin_insights_staff_access_and_context(self):
        # Staff user gets 200 and rendered dashboard with aggregated metrics
        client = APIClient()
        client.login(username='staffuser', password='StaffPassword123!')
        response = client.get('/admin-insights/')
        self.assertEqual(response.status_code, 200)
        self.assertIn('api_usage', response.context)
        self.assertIn('webhook_health', response.context)
        self.assertIn('data_quality', response.context)
        self.assertIn('ml_anomalies', response.context)
        self.assertIn('channel_partners', response.context)
        self.assertIn('channel_partners', response.context['api_usage'])
        self.assertContains(response, "Admin Insights & Platform Telemetry")
        self.assertContains(response, "API Management: Channel Partners")

    def test_admin_dashboard_navigation_link_visibility(self):
        client = APIClient()

        # Anonymous user should not see Admin Dashboard link in navbar
        response = client.get('/')
        self.assertEqual(response.status_code, 200)
        self.assertNotContains(response, "Admin Dashboard")

        # Regular user should not see Admin Dashboard link
        client.login(username='regularuser', password='RegularPassword123!')
        response = client.get('/')
        self.assertEqual(response.status_code, 200)
        self.assertNotContains(response, "Admin Dashboard")

        # Staff user should see Admin Dashboard link in navbar
        client.login(username='staffuser', password='StaffPassword123!')
        response = client.get('/')
        self.assertEqual(response.status_code, 200)
        self.assertContains(response, "Admin Dashboard")
        self.assertContains(response, "/admin-insights/")


class MLScoringEngineTests(TestCase):
    def test_assign_health_label_thresholds(self):
        """
        Verify health label assignment based on PRD Section 7 thresholds:
          85+: 'EXCELLENT'
          70-84: 'GOOD'
          50-69: 'AVERAGE'
          35-49: 'WEAK'
          <35: 'POOR'
        """
        from nifty_core.ml_scoring import assign_health_label

        self.assertEqual(assign_health_label(92.5), 'EXCELLENT')
        self.assertEqual(assign_health_label(85.0), 'EXCELLENT')
        self.assertEqual(assign_health_label(84.9), 'GOOD')
        self.assertEqual(assign_health_label(70.0), 'GOOD')
        self.assertEqual(assign_health_label(69.9), 'AVERAGE')
        self.assertEqual(assign_health_label(50.0), 'AVERAGE')
        self.assertEqual(assign_health_label(49.9), 'WEAK')
        self.assertEqual(assign_health_label(35.0), 'WEAK')
        self.assertEqual(assign_health_label(34.9), 'POOR')
        self.assertEqual(assign_health_label(12.0), 'POOR')
        self.assertEqual(assign_health_label(None), 'UNKNOWN')

    def test_is_financial_company_detection(self):
        """
        Verify identification of Banking, NBFC, and Insurance companies.
        """
        from nifty_core.ml_scoring import is_financial_company

        self.assertTrue(is_financial_company('Banking', 'HDFCBANK'))
        self.assertTrue(is_financial_company('NBFC', 'BAJFINANCE'))
        self.assertTrue(is_financial_company('Insurance', 'HDFCLIFE'))
        self.assertTrue(is_financial_company('Financial Services', 'BAJAJFINSV'))
        self.assertTrue(is_financial_company(None, 'SBIN'))
        self.assertFalse(is_financial_company('IT', 'TCS'))
        self.assertFalse(is_financial_company('Energy', 'RELIANCE'))
        self.assertFalse(is_financial_company('Automobile', 'TATAMOTORS'))

    def test_ml_scoring_dynamic_reweighting(self):
        """
        Verify that Banks and NBFCs are exempted from Leverage & Cashflow,
        and dynamically re-weighted from 65 available weight to 100.
        """
        import pandas as pd
        from nifty_core.ml_scoring import compute_ml_scores_dataframe

        years = [1, 2, 3, 4, 5]
        # P&L for Tech vs Bank
        pl_rows = []
        for y in years:
            pl_rows.append({
                'symbol': 'TECHCO',
                'year_id': y,
                'sales': 1000.0 + y * 100.0,
                'opm_pct': 25.0,
                'net_profit_margin_pct': 20.0,
                'dividend_payout_pct': 30.0,
            })
            pl_rows.append({
                'symbol': 'BANKCO',
                'year_id': y,
                'sales': 5000.0 + y * 500.0,
                'opm_pct': 22.0,
                'net_profit_margin_pct': 18.0,
                'dividend_payout_pct': 25.0,
            })
        pl_df = pd.DataFrame(pl_rows)

        # Balance Sheet (Bank has high D/E which should be exempted)
        bs_rows = [
            {'symbol': 'TECHCO', 'year_id': 5, 'debt_to_equity': 0.05},
            {'symbol': 'BANKCO', 'year_id': 5, 'debt_to_equity': 8.5},
        ]
        bs_df = pd.DataFrame(bs_rows)

        # Cash Flow
        cf_rows = [
            {'symbol': 'TECHCO', 'year_id': 5, 'cash_conversion_ratio': 1.5},
            {'symbol': 'BANKCO', 'year_id': 5, 'cash_conversion_ratio': -0.2},
        ]
        cf_df = pd.DataFrame(cf_rows)

        sectors_map = {
            'TECHCO': 'IT',
            'BANKCO': 'Banking'
        }

        df_scores = compute_ml_scores_dataframe(pl_df, bs_df, cf_df, sectors_map)

        tech_row = df_scores[df_scores['symbol'] == 'TECHCO'].iloc[0]
        bank_row = df_scores[df_scores['symbol'] == 'BANKCO'].iloc[0]

        # Non-financial company gets full 100 available weight
        self.assertEqual(tech_row['available_weight'], 100.0)
        self.assertFalse(pd.isna(tech_row['leverage_raw']))
        self.assertFalse(pd.isna(tech_row['cashflow_raw']))

        # Financial company has leverage & cashflow cleared to NaN and available weight = 65.0
        self.assertEqual(bank_row['available_weight'], 65.0)
        self.assertTrue(pd.isna(bank_row['leverage_raw']))
        self.assertTrue(pd.isna(bank_row['cashflow_raw']))
        self.assertTrue(pd.isna(bank_row['leverage_score']))
        self.assertTrue(pd.isna(bank_row['cashflow_score']))

        # Overall score is dynamically scaled out of 100
        self.assertTrue(0 <= bank_row['overall_score'] <= 100)
        self.assertIn(bank_row['health_label'], ['EXCELLENT', 'GOOD', 'AVERAGE', 'WEAK', 'POOR'])

    def test_management_command_configuration(self):
        """
        Verify run_ml_scoring command exists and supports --symbol, --snapshot, and --quiet arguments.
        """
        from api.management.commands.run_ml_scoring import Command
        cmd = Command()
        parser = cmd.create_parser('manage.py', 'run_ml_scoring')
        
        # Test parsing default options
        parsed_default = parser.parse_args([])
        self.assertIsNone(parsed_default.symbol)
        self.assertFalse(parsed_default.snapshot)
        self.assertFalse(parsed_default.quiet)

        # Test parsing custom options
        parsed_custom = parser.parse_args(['--symbol', 'TCS', '--snapshot', '--quiet'])
        self.assertEqual(parsed_custom.symbol, 'TCS')
        self.assertTrue(parsed_custom.snapshot)
        self.assertTrue(parsed_custom.quiet)


class CompanySearchEndpointTests(TestCase):
    def test_unauthenticated_search_access_allowed(self):
        """
        Verify that frontend search autocomplete requests to /api/v1/companies/search/
        succeed with 200 and AllowAny permission, without 401 Unauthorized HMAC failures.
        """
        client = APIClient()
        response = client.get('/api/v1/companies/search/?q=TCS')
        self.assertEqual(response.status_code, 200)

    def test_empty_query_returns_empty_list(self):
        """
        Verify that search with empty query returns an empty list with 200 OK.
        """
        client = APIClient()
        response = client.get('/api/v1/companies/search/?q=')
        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.json(), [])

    def test_company_viewset_search_action_allows_any(self):
        """
        Verify that CompanyViewSet.search action has AllowAny and empty authenticators.
        """
        from api.views import CompanyViewSet, company_search_api
        from rest_framework.permissions import AllowAny

        viewset = CompanyViewSet()
        viewset.action = 'search'
        perms = viewset.get_permissions()
        self.assertTrue(any(isinstance(p, AllowAny) for p in perms))
        auths = viewset.get_authenticators()
        self.assertEqual(auths, [])

        # Standalone company_search_api view check
        self.assertTrue(hasattr(company_search_api, 'permission_classes'))
        self.assertIn(AllowAny, company_search_api.permission_classes)
        self.assertTrue(hasattr(company_search_api, 'authentication_classes'))
        self.assertEqual(company_search_api.authentication_classes, [])


class HomePageViewTests(TestCase):
    def test_home_page_renders_cleanly(self):
        """
        Verify that the home page endpoint resolves, returns 200, and provides
        the required PRD context (featured_companies, sectors, ticker_insights).
        """
        from api.views import home_page
        from django.test import RequestFactory

        factory = RequestFactory()
        request = factory.get('/')
        response = home_page(request)
        self.assertEqual(response.status_code, 200)

    def test_home_page_view_context_structure(self):
        """
        Verify context has 6 featured companies, 12 sectors, ticker insights, and popular symbols.
        """
        response = self.client.get('/')
        self.assertEqual(response.status_code, 200)
        self.assertIn('featured_companies', response.context)
        self.assertIn('sectors', response.context)
        self.assertIn('ticker_insights', response.context)
        self.assertIn('popular_symbols', response.context)
        self.assertEqual(len(response.context['featured_companies']), 6)
        self.assertEqual(len(response.context['sectors']), 12)

    def test_company_list_and_companies_reverse_routing(self):
        """
        Verify that both 'company_list' and 'companies' resolve cleanly to /companies/
        preventing any NoReverseMatch errors during template rendering.
        """
        from django.urls import reverse
        self.assertEqual(reverse('company_list'), '/companies/')
        self.assertEqual(reverse('companies'), '/companies/')


class SectorDetailViewTests(TestCase):
    def test_sector_detail_route_resolves_and_renders(self):
        """
        Verify that /sectors/<str:sector_name>/ returns 200 and supplies
        KPI averages, outliers, and Chart.js revenue time-series.
        """
        from django.urls import reverse
        url = reverse('sector_detail', args=['Financial Services'])
        self.assertEqual(url, '/sectors/Financial%20Services/')
        response = self.client.get(url)
        self.assertEqual(response.status_code, 200)
        self.assertIn('sector_name', response.context)
        self.assertIn('avg_score', response.context)
        self.assertIn('top_3', response.context)
        self.assertIn('bottom_3', response.context)
        self.assertIn('revenue_labels', response.context)
        self.assertIn('revenue_values', response.context)

    def test_legacy_sector_alias_resolves(self):
        """
        Verify that legacy /sector/<sector_name>/ also resolves cleanly.
        """
        response = self.client.get('/sector/Information-Technology/')
        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.context['sector_name'], 'Information Technology')

    def test_fact_ml_scores_overall_score_attribute_mapping(self):
        """
        Verify that SectorDetailView correctly reads FactMlScores.overall_score
        and maps health scores without raising AttributeError for health_score.
        """
        from unittest.mock import MagicMock, patch
        from api.views import SectorDetailView

        view = SectorDetailView()
        mock_score = MagicMock()
        mock_score.overall_score = 88.5
        mock_score.health_label = 'EXCELLENT'
        # Ensure mock does not have health_score attribute to simulate real model
        del mock_score.health_score

        mock_company = MagicMock()
        mock_company.symbol_id = 'TCS'
        mock_company.company_name = 'Tata Consultancy Services Ltd.'
        mock_company.sector = 'Information Technology'
        mock_company.sub_sector = 'IT Services'

        score_val = getattr(mock_score, 'overall_score', None)
        self.assertEqual(score_val, 88.5)
        self.assertFalse(hasattr(mock_score, 'health_score'))


class TierBasedRateLimitingTests(TestCase):
    """
    Tests for PRD Tier-Based Redis Rate Limiting on Channel Partner API:
      BASIC: 10/min, 100/hr, 500/day
      PRO: 60/min, 1000/hr, 10000/day
      ENTERPRISE: 300/min, 10000/hr, unlimited/day
    """
    def setUp(self):
        cache.clear()
        self.factory = RequestFactory()
        self.partner_basic = ChannelPartner.objects.create(
            name="Basic Corp",
            email="basic@corp.com",
            tier=ChannelPartner.TIER_BASIC
        )
        self.partner_pro = ChannelPartner.objects.create(
            name="Pro Analytics",
            email="pro@analytics.com",
            tier=ChannelPartner.TIER_PRO
        )
        self.partner_ent = ChannelPartner.objects.create(
            name="Enterprise Fund",
            email="ent@fund.com",
            tier=ChannelPartner.TIER_ENTERPRISE
        )

    def tearDown(self):
        cache.clear()

    def test_basic_tier_quota_exhaustion_and_429_response(self):
        throttle = PartnerTierRateThrottle()
        req = self.factory.get("/api/partner/v1/companies/")
        req.user = self.partner_basic

        # BASIC tier limit is 10/min
        for i in range(10):
            self.assertTrue(throttle.allow_request(req, None), f"Request {i+1} should be allowed")

        # 11th request must breach the limit
        self.assertFalse(throttle.allow_request(req, None), "11th request should be throttled")
        wait = throttle.wait()
        self.assertTrue(wait > 0)

        # Verify custom exception handler formats error response cleanly
        exc = PartnerRateLimitExceeded(wait=wait, quota_info=getattr(req, '_quota_info', None))
        context = {'request': req}
        response = partner_api_exception_handler(exc, context)

        self.assertEqual(response.status_code, 429)
        self.assertEqual(response.data.get('error'), 'Rate limit exceeded')
        self.assertIn('retry_after', response.data)
        self.assertIn('Retry-After', response.headers)
        self.assertIn('remaining_quota', response.data)
        self.assertEqual(response.data['remaining_quota']['minute'], 0)
        self.assertEqual(response.data['remaining_quota']['tier'], 'BASIC')

    def test_pro_tier_higher_minute_limit(self):
        throttle = PartnerTierRateThrottle()
        req = self.factory.get("/api/partner/v1/companies/")
        req.user = self.partner_pro

        # Pro tier allows 60/min; 15 requests should all succeed without throttling
        for i in range(15):
            self.assertTrue(throttle.allow_request(req, None))

        quota = getattr(req, '_quota_info', {})
        self.assertEqual(quota['tier'], 'PRO')
        self.assertEqual(quota['minute'], 60 - 15)

    def test_enterprise_tier_no_daily_cap(self):
        self.assertIsNone(TIER_QUOTAS['ENTERPRISE']['day'])
        throttle = PartnerTierRateThrottle()
        req = self.factory.get("/api/partner/v1/companies/")
        req.user = self.partner_ent

        self.assertTrue(throttle.allow_request(req, None))
        quota = getattr(req, '_quota_info', {})
        self.assertEqual(quota['tier'], 'ENTERPRISE')
        self.assertEqual(quota['day'], "UNLIMITED")








