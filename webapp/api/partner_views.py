import logging
import uuid
import secrets
from rest_framework import viewsets, status
from rest_framework.response import Response
from rest_framework.decorators import action
from rest_framework.throttling import SimpleRateThrottle
from django.shortcuts import get_object_or_404
from django.db.models import Q, Subquery, OuterRef
from django.db import connection

from drf_spectacular.utils import (
    extend_schema,
    extend_schema_view,
    OpenApiParameter,
    OpenApiResponse,
    OpenApiExample,
)
from drf_spectacular.types import OpenApiTypes

from .models import (
    DimCompany,
    FactMlScores,
    FactAnalysis,
    FactBalanceSheet,
    FactProfitLoss,
    FactCashFlow,
    ChannelPartner,
    WebhookSubscription,
)
from .serializers import (
    PartnerCompanySerializer,
    WebhookSubscriptionSerializer,
    ChannelPartnerKeySerializer,
)
from .authentication import PartnerHMACAuthentication
from .permissions import IsPartnerHMACAuthenticated
from .throttling import (
    PartnerTierRateThrottle,
    PartnerRateThrottle,
    PartnerThrottleMixin,
    PartnerRateLimitExceeded,
)

logger = logging.getLogger(__name__)

# Standard OpenAPI parameters for HMAC-SHA256 authenticated endpoints
HMAC_AUTH_HEADER_PARAMETERS = [
    OpenApiParameter(
        name='X-API-Key-ID',
        type=OpenApiTypes.STR,
        location=OpenApiParameter.HEADER,
        required=True,
        description='Commercial Partner Public API Key ID (UUID) as per Stream C PRD'
    ),
    OpenApiParameter(
        name='X-Timestamp',
        type=OpenApiTypes.STR,
        location=OpenApiParameter.HEADER,
        required=True,
        description='Unix epoch timestamp in seconds (valid within 300s window of server time)'
    ),
    OpenApiParameter(
        name='X-Nonce',
        type=OpenApiTypes.STR,
        location=OpenApiParameter.HEADER,
        required=True,
        description='Cryptographically random unique identifier matching [A-Za-z0-9_-]{16,64}'
    ),
    OpenApiParameter(
        name='X-Signature',
        type=OpenApiTypes.STR,
        location=OpenApiParameter.HEADER,
        required=True,
        description='Hexadecimal HMAC-SHA256 signature of canonical request string (METHOD\\nPATH?QUERY\\nTIMESTAMP\\nNONCE\\nBODY_HASH)'
    ),
]


@extend_schema_view(
    list=extend_schema(
        summary="List Nifty 100 Companies with Fundamentals & Health Scores",
        description=(
            "Returns a paginated list of all Nifty 100 constituents with standardized valuation ratios, "
            "P&L, balance sheet, cash flows, and latest ML health scores.\n\n"
            "**Authentication**: Requires valid HMAC-SHA256 request headers signed with partner `secret_key`."
        ),
        parameters=[
            *HMAC_AUTH_HEADER_PARAMETERS,
            OpenApiParameter('sector', type=OpenApiTypes.STR, description='Filter companies by industry sector (e.g. Information Technology)'),
            OpenApiParameter('search', type=OpenApiTypes.STR, description='Search across ticker symbol and company name'),
            OpenApiParameter('health_label', type=OpenApiTypes.STR, description='Filter by ML rating (EXCELLENT, GOOD, AVERAGE, WEAK, POOR)'),
            OpenApiParameter('page', type=OpenApiTypes.INT, description='Page number for pagination'),
        ],
        responses={
            200: PartnerCompanySerializer(many=True),
            401: OpenApiResponse(description="Authentication failed due to invalid, missing, or expired HMAC credentials."),
            429: OpenApiResponse(description="Rate limit exceeded for partner quota."),
        },
        tags=["Channel Partner: Companies"]
    ),
    retrieve=extend_schema(
        summary="Retrieve Full Company Data Dump",
        description=(
            "Returns a complete fundamental and intelligence data dump for one company by symbol: "
            "all historical ratios, balance sheet composition, P&L statements, cash flows, and ML health scores."
        ),
        parameters=[
            *HMAC_AUTH_HEADER_PARAMETERS,
            OpenApiParameter('symbol', type=OpenApiTypes.STR, location=OpenApiParameter.PATH, description='Company ticker symbol (e.g. RELIANCE, TCS, INFY)'),
        ],
        responses={
            200: PartnerCompanySerializer,
            401: OpenApiResponse(description="Authentication failed."),
            404: OpenApiResponse(description="Company ticker symbol not found in Nifty 100 universe."),
            429: OpenApiResponse(description="Rate limit exceeded."),
        },
        tags=["Channel Partner: Companies"]
    )
)
class PartnerCompanyViewSet(PartnerThrottleMixin, viewsets.ReadOnlyModelViewSet):
    """
    Phase 2 Commercial Partner API:
    Enterprise REST API providing Nifty 100 fundamental intelligence,
    ratios, and machine-learning health scores to authorized channel partners.
    
    Protected exclusively by canonical HMAC-SHA256 signature verification and PartnerTierRateThrottle.
    """
    authentication_classes = [PartnerHMACAuthentication]
    permission_classes = [IsPartnerHMACAuthenticated]
    throttle_classes = [PartnerTierRateThrottle]
    serializer_class = PartnerCompanySerializer
    lookup_field = 'symbol__symbol'
    lookup_url_kwarg = 'symbol'

    def get_queryset(self):
        queryset = DimCompany.objects.all().order_by('symbol_id')
        
        # Optional sector filter
        sector = self.request.query_params.get('sector')
        if sector:
            queryset = queryset.filter(sector__iexact=sector.strip())

        # Optional search filter
        search = self.request.query_params.get('search') or self.request.query_params.get('q')
        if search:
            q = search.strip()
            queryset = queryset.filter(
                Q(symbol__symbol__icontains=q) | Q(company_name__icontains=q)
            )

        # Health label filter: evaluate ONLY the latest FactMlScores row per company
        health_label = self.request.query_params.get('health_label')
        if health_label:
            latest_ml_label = Subquery(
                FactMlScores.objects.filter(symbol_id=OuterRef('symbol_id'))
                .order_by('-computed_at')
                .values('health_label')[:1]
            )
            queryset = queryset.annotate(latest_health_label=latest_ml_label).filter(
                latest_health_label__iexact=health_label.strip()
            )

        return queryset

    def get_serializer_context(self):
        context = super().get_serializer_context()

        # Identify symbols for the current request / current page to prevent N+1 queries
        symbol_kwarg = self.kwargs.get('symbol')
        if symbol_kwarg:
            symbols = [symbol_kwarg.strip().upper()]
        else:
            queryset = self.filter_queryset(self.get_queryset())
            symbols = list(queryset.values_list('symbol_id', flat=True)[:100])

        if not symbols:
            context.update({
                'ml_scores': {},
                'analysis_map': {},
                'bs_map': {},
                'pl_map': {},
                'cf_map': {},
            })
            return context

        # Use PostgreSQL's distinct("symbol_id") chained with order-by to grab only latest rows
        if connection.features.can_distinct_on_fields:
            ml_qs = FactMlScores.objects.filter(symbol_id__in=symbols).order_by('symbol_id', '-computed_at').distinct('symbol_id')
            ml_scores = {s.symbol_id: s for s in ml_qs}

            analysis_qs = FactAnalysis.objects.filter(symbol_id__in=symbols).order_by('symbol_id', '-period_label').distinct('symbol_id')
            analysis_map = {a.symbol_id: a for a in analysis_qs}

            bs_qs = FactBalanceSheet.objects.filter(symbol_id__in=symbols).order_by('symbol_id', '-year_id').distinct('symbol_id')
            bs_map = {b.symbol_id: b for b in bs_qs}

            pl_qs = FactProfitLoss.objects.filter(symbol_id__in=symbols).order_by('symbol_id', '-year_id').distinct('symbol_id')
            pl_map = {p.symbol_id: p for p in pl_qs}

            cf_qs = FactCashFlow.objects.filter(symbol_id__in=symbols).order_by('symbol_id', '-year_id').distinct('symbol_id')
            cf_map = {c.symbol_id: c for c in cf_qs}
        else:
            # Fallback for database backends lacking distinct(*fields)
            ml_scores = {}
            for s in FactMlScores.objects.filter(symbol_id__in=symbols).order_by('-computed_at'):
                if s.symbol_id not in ml_scores:
                    ml_scores[s.symbol_id] = s

            analysis_map = {}
            for a in FactAnalysis.objects.filter(symbol_id__in=symbols).order_by('-period_label'):
                if a.symbol_id not in analysis_map:
                    analysis_map[a.symbol_id] = a

            bs_map = {}
            for b in FactBalanceSheet.objects.filter(symbol_id__in=symbols).order_by('-year_id'):
                if b.symbol_id not in bs_map:
                    bs_map[b.symbol_id] = b

            pl_map = {}
            for p in FactProfitLoss.objects.filter(symbol_id__in=symbols).order_by('-year_id'):
                if p.symbol_id not in pl_map:
                    pl_map[p.symbol_id] = p

            cf_map = {}
            for c in FactCashFlow.objects.filter(symbol_id__in=symbols).order_by('-year_id'):
                if c.symbol_id not in cf_map:
                    cf_map[c.symbol_id] = c

        context.update({
            'ml_scores': ml_scores,
            'analysis_map': analysis_map,
            'bs_map': bs_map,
            'pl_map': pl_map,
            'cf_map': cf_map,
        })
        return context

    def get_object(self):
        symbol = self.kwargs.get('symbol', '').strip().upper()
        obj = get_object_or_404(DimCompany, symbol__symbol=symbol)
        self.check_object_permissions(self.request, obj)
        return obj

    @extend_schema(
        summary="Bulk Financials Dump",
        description="Accepts a comma-separated ?symbols=TCS,INFY,WIPRO parameter and returns full financial data for up to ten companies in one call.",
        parameters=[
            *HMAC_AUTH_HEADER_PARAMETERS,
            OpenApiParameter('symbols', type=OpenApiTypes.STR, required=True, description='Comma-separated ticker symbols (e.g. TCS,INFY,WIPRO). Maximum 10 symbols.'),
        ],
        responses={
            200: PartnerCompanySerializer(many=True),
            400: OpenApiResponse(description="Invalid request (e.g. missing symbols or exceeds limit of 10)."),
            401: OpenApiResponse(description="Authentication failed."),
        },
        tags=["Channel Partner: Companies"]
    )
    @action(detail=False, methods=['get'], url_path='bulk-financials')
    def bulk_financials(self, request):
        symbols_param = request.query_params.get('symbols', '').strip()
        if not symbols_param:
            return Response({'error': 'Parameter "symbols" is required (e.g. ?symbols=TCS,INFY).'}, status=status.HTTP_400_BAD_REQUEST)

        symbols = [s.strip().upper() for s in symbols_param.split(',') if s.strip()]
        if len(symbols) > 10:
            return Response({'error': 'Maximum 10 symbols allowed per bulk request.'}, status=status.HTTP_400_BAD_REQUEST)

        companies = DimCompany.objects.filter(symbol__symbol__in=symbols).order_by('symbol_id')
        serializer = self.get_serializer(companies, many=True)
        return Response(serializer.data)

    @extend_schema(
        summary="Partner Financial Screener",
        description="Executes a multi-filter fundamental screening query on the Nifty 100 universe and returns structured JSON results.",
        parameters=[
            *HMAC_AUTH_HEADER_PARAMETERS,
            OpenApiParameter('sector', type=OpenApiTypes.STR, description='Filter by industry sector'),
            OpenApiParameter('health_label', type=OpenApiTypes.STR, description='Filter by health label (EXCELLENT, GOOD, AVERAGE, WEAK, POOR)'),
            OpenApiParameter('min_roe', type=OpenApiTypes.FLOAT, description='Minimum Return on Equity % (e.g. 15.0)'),
            OpenApiParameter('max_de', type=OpenApiTypes.FLOAT, description='Maximum Debt-to-Equity ratio (e.g. 1.0)'),
            OpenApiParameter('min_score', type=OpenApiTypes.FLOAT, description='Minimum ML health score (e.g. 70.0)'),
        ],
        responses={
            200: PartnerCompanySerializer(many=True),
            401: OpenApiResponse(description="Authentication failed."),
        },
        tags=["Channel Partner: Screener"]
    )
    @action(detail=False, methods=['get'], url_path='screener')
    def screener(self, request):
        queryset = self.get_queryset()

        min_roe = request.query_params.get('min_roe')
        max_de = request.query_params.get('max_de')
        min_score = request.query_params.get('min_score')

        if min_roe:
            try:
                val = float(min_roe)
                symbols_with_roe = FactAnalysis.objects.filter(roe_pct__gte=val).values_list('symbol_id', flat=True)
                queryset = queryset.filter(symbol_id__in=symbols_with_roe)
            except ValueError:
                pass

        if max_de:
            try:
                val = float(max_de)
                symbols_with_de = FactBalanceSheet.objects.filter(debt_to_equity__lte=val).values_list('symbol_id', flat=True)
                queryset = queryset.filter(symbol_id__in=symbols_with_de)
            except ValueError:
                pass

        if min_score:
            try:
                val = float(min_score)
                symbols_with_score = FactMlScores.objects.filter(overall_score__gte=val).values_list('symbol_id', flat=True)
                queryset = queryset.filter(symbol_id__in=symbols_with_score)
            except ValueError:
                pass

        serializer = self.get_serializer(queryset[:100], many=True)
        return Response(serializer.data)

    @extend_schema(
        summary="Constituent Health Scores Leaderboard",
        description="Returns the latest ML health scores and qualitative labels for all 100 companies or a specified subset, sorted descending by health score.",
        parameters=[
            *HMAC_AUTH_HEADER_PARAMETERS,
            OpenApiParameter('symbols', type=OpenApiTypes.STR, required=False, description='Optional comma-separated subset of ticker symbols'),
        ],
        responses={
            200: OpenApiResponse(description="Ranked array of companies with health scores and health labels."),
            401: OpenApiResponse(description="Authentication failed."),
        },
        tags=["Channel Partner: Health Scores"]
    )
    @action(detail=False, methods=['get'], url_path='scores')
    def scores(self, request):
        return self.health_scores(request)

    @extend_schema(
        summary="Ranked Health Scores Overview",
        description="Returns a ranked overview of all Nifty 100 constituents ordered by automated ML health score.",
        parameters=HMAC_AUTH_HEADER_PARAMETERS,
        responses={
            200: OpenApiResponse(description="Ranked overview of constituents with health scores and health labels."),
            401: OpenApiResponse(description="Authentication failed."),
        },
        tags=["Channel Partner: Health Scores"]
    )
    @action(detail=False, methods=['get'], url_path='health-scores')
    def health_scores(self, request):
        symbols_param = request.query_params.get('symbols')
        companies = DimCompany.objects.all().order_by('symbol_id')
        if symbols_param:
            requested = [s.strip().upper() for s in symbols_param.split(',') if s.strip()]
            companies = companies.filter(symbol__symbol__in=requested)

        symbols = list(companies.values_list('symbol_id', flat=True))

        if connection.features.can_distinct_on_fields:
            ml_qs = FactMlScores.objects.filter(symbol_id__in=symbols).order_by('symbol_id', '-computed_at').distinct('symbol_id')
            ml_scores = {s.symbol_id: s for s in ml_qs}
        else:
            ml_scores = {}
            for score in FactMlScores.objects.filter(symbol_id__in=symbols).order_by('-computed_at'):
                if score.symbol_id not in ml_scores:
                    ml_scores[score.symbol_id] = score

        results = []
        for c in companies:
            score_obj = ml_scores.get(c.symbol_id)
            results.append({
                'symbol': c.symbol_id,
                'company_name': c.company_name,
                'sector': c.sector,
                'health_score': float(score_obj.overall_score) if score_obj and score_obj.overall_score is not None else None,
                'health_label': score_obj.health_label if score_obj else 'PENDING',
            })

        # Sort descending by health_score (nulls last)
        results.sort(key=lambda x: (x['health_score'] is not None, x['health_score'] or 0), reverse=True)

        return Response({
            'count': len(results),
            'results': results
        })

    @extend_schema(
        summary="Universe Executive Overview",
        description="Returns executive universe summary metrics, total universe count, sector count, average health score, and rating distribution.",
        parameters=HMAC_AUTH_HEADER_PARAMETERS,
        responses={
            200: OpenApiResponse(description="Universe summary metrics and health score distribution."),
            401: OpenApiResponse(description="Authentication failed."),
        },
        tags=["Channel Partner: Intelligence"]
    )
    @action(detail=False, methods=['get'], url_path='overview')
    def overview(self, request):
        total_companies = DimCompany.objects.count()
        sectors = list(DimCompany.objects.values_list('sector', flat=True).distinct())

        if connection.features.can_distinct_on_fields:
            ml_qs = FactMlScores.objects.order_by('symbol_id', '-computed_at').distinct('symbol_id')
            ml_scores = {s.symbol_id: s for s in ml_qs}
        else:
            ml_scores = {}
            for score in FactMlScores.objects.order_by('-computed_at'):
                if score.symbol_id not in ml_scores:
                    ml_scores[score.symbol_id] = score

        scores_list = [float(s.overall_score) for s in ml_scores.values() if s.overall_score is not None]
        avg_health_score = round(sum(scores_list) / len(scores_list), 2) if scores_list else 0.0

        label_counts = {}
        for s in ml_scores.values():
            lbl = s.health_label or 'PENDING'
            label_counts[lbl] = label_counts.get(lbl, 0) + 1

        partner_name = getattr(getattr(request, 'partner', None), 'name', 'Partner')

        return Response({
            'message': f"Welcome to NiftyIQ Channel Partner Intelligence API, {partner_name}.",
            'total_universe_tracked': total_companies,
            'total_sectors': len(sectors),
            'average_universe_health_score': avg_health_score,
            'health_label_distribution': label_counts,
        })


@extend_schema_view(
    list=extend_schema(
        summary="List Webhook Subscriptions",
        description="Returns all active webhook event subscriptions registered for the authenticated partner.",
        parameters=HMAC_AUTH_HEADER_PARAMETERS,
        responses={
            200: WebhookSubscriptionSerializer(many=True),
            401: OpenApiResponse(description="Authentication failed."),
        },
        tags=["Channel Partner: Webhooks"]
    ),
    create=extend_schema(
        summary="Create Webhook Subscription",
        description="Registers a new webhook URL to receive real-time notifications when events occur (e.g. score_updated, anomaly_flagged).",
        parameters=HMAC_AUTH_HEADER_PARAMETERS,
        request=WebhookSubscriptionSerializer,
        responses={
            201: WebhookSubscriptionSerializer,
            400: OpenApiResponse(description="Validation error."),
            401: OpenApiResponse(description="Authentication failed."),
        },
        tags=["Channel Partner: Webhooks"]
    ),
    destroy=extend_schema(
        summary="Delete Webhook Subscription",
        description="Deactivates and deletes a webhook subscription by its ID.",
        parameters=[
            *HMAC_AUTH_HEADER_PARAMETERS,
            OpenApiParameter('id', type=OpenApiTypes.INT, location=OpenApiParameter.PATH, description='Webhook subscription ID'),
        ],
        responses={
            204: OpenApiResponse(description="Webhook subscription successfully deleted."),
            401: OpenApiResponse(description="Authentication failed."),
            404: OpenApiResponse(description="Webhook subscription not found."),
        },
        tags=["Channel Partner: Webhooks"]
    )
)
class PartnerWebhookViewSet(PartnerThrottleMixin, viewsets.ModelViewSet):
    """
    Channel Partner Webhook Subscription Management.
    Enables partners to register, inspect, and delete webhook subscriptions.
    """
    authentication_classes = [PartnerHMACAuthentication]
    permission_classes = [IsPartnerHMACAuthenticated]
    throttle_classes = [PartnerTierRateThrottle]
    serializer_class = WebhookSubscriptionSerializer
    http_method_names = ['get', 'post', 'delete']

    def get_queryset(self):
        partner = getattr(self.request, 'partner', None)
        if not partner:
            return WebhookSubscription.objects.none()
        return WebhookSubscription.objects.filter(partner=partner, is_active=True).order_by('-created_at')

    def perform_create(self, serializer):
        partner = getattr(self.request, 'partner', None)
        serializer.save(partner=partner)

    def perform_destroy(self, instance):
        instance.is_active = False
        instance.save(update_fields=['is_active'])


@extend_schema_view(
    list=extend_schema(
        summary="Get Partner API Key Metadata",
        description="Returns API key metadata, tier info, and active status for the authenticated partner.",
        parameters=HMAC_AUTH_HEADER_PARAMETERS,
        responses={
            200: ChannelPartnerKeySerializer,
            401: OpenApiResponse(description="Authentication failed."),
        },
        tags=["Channel Partner: Keys"]
    ),
    create=extend_schema(
        summary="Regenerate Partner API Key & Secret",
        description="Regenerates a new public API key and private secret key. The private secret_key is shown exactly once in the response.",
        parameters=HMAC_AUTH_HEADER_PARAMETERS,
        responses={
            201: OpenApiResponse(description="New credentials generated with api_key and secret_key."),
            401: OpenApiResponse(description="Authentication failed."),
        },
        tags=["Channel Partner: Keys"]
    )
)
class PartnerKeyViewSet(PartnerThrottleMixin, viewsets.ViewSet):
    """
    Channel Partner API Key Credentials Management.
    Allows partner self-service key inspection and credential rotation.
    """
    authentication_classes = [PartnerHMACAuthentication]
    permission_classes = [IsPartnerHMACAuthenticated]
    throttle_classes = [PartnerTierRateThrottle]

    def list(self, request):
        partner = getattr(request, 'partner', None)
        if not partner:
            return Response({'error': 'Partner not authenticated'}, status=status.HTTP_401_UNAUTHORIZED)
        serializer = ChannelPartnerKeySerializer(partner)
        return Response(serializer.data)

    def create(self, request):
        partner = getattr(request, 'partner', None)
        if not partner:
            return Response({'error': 'Partner not authenticated'}, status=status.HTTP_401_UNAUTHORIZED)

        new_api_key = str(uuid.uuid4())
        new_secret_key = secrets.token_hex(32)

        partner.api_key = new_api_key
        partner.secret_key = new_secret_key
        partner.save(update_fields=['api_key', 'secret_key'])

        return Response({
            'message': 'API Key credentials regenerated successfully. Store this secret key securely; it will not be shown again.',
            'api_key': new_api_key,
            'secret_key': new_secret_key,
            'name': partner.name,
            'email': partner.email,
        }, status=status.HTTP_201_CREATED)
