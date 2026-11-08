from django.shortcuts import render
from django.views import View
from django.utils.decorators import method_decorator
from django.contrib.admin.views.decorators import staff_member_required
from django.db.models import Avg, Count

from .models import (
    APIUsageLog,
    ChannelPartner,
    WebhookEvent,
    DimCompany,
    DimYears,
    FactProfitLoss,
    FactBalanceSheet,
    FactMlScores,
)


@method_decorator(staff_member_required(login_url='admin:login'), name='dispatch')
class AdminInsightsView(View):
    """
    Stream C Option 3: Custom Admin Dashboard & Telemetry Insights.
    Strictly restricted to staff and superuser accounts via @staff_member_required.
    Aggregates metrics for:
      1. API Usage (Total logs, average latency, top 5 heavily used endpoints)
      2. Webhook Health (Failed deliveries count, recently failed attempts)
      3. Data Quality Gaps (Nifty 100 companies missing current-year P&L or Balance Sheet)
      4. ML Anomalies (Companies with significant score drops >= 2 points)
    """

    def get(self, request, *args, **kwargs):
        # 1. API Usage Metrics & Registered Channel Partners
        total_api_calls = APIUsageLog.objects.count()
        avg_latency = APIUsageLog.objects.aggregate(avg_ms=Avg('response_ms'))['avg_ms']
        avg_response_ms = round(float(avg_latency), 1) if avg_latency is not None else 0.0

        top_endpoints = list(
            APIUsageLog.objects.values('endpoint', 'method')
            .annotate(call_count=Count('id'), avg_latency=Avg('response_ms'))
            .order_by('-call_count')[:5]
        )
        for ep in top_endpoints:
            if ep.get('avg_latency') is not None:
                ep['avg_latency'] = round(float(ep['avg_latency']), 1)

        channel_partners = list(
            ChannelPartner.objects.all().order_by('-created_at')
        )

        # 2. Webhook Health Metrics
        failed_webhooks_count = WebhookEvent.objects.filter(status='FAILED').count()
        total_webhooks_count = WebhookEvent.objects.count()
        success_webhooks_count = WebhookEvent.objects.filter(status='SUCCESS').count()
        webhook_success_rate = (
            round((success_webhooks_count / total_webhooks_count) * 100.0, 1)
            if total_webhooks_count > 0
            else 100.0
        )

        recent_failed_webhooks = list(
            WebhookEvent.objects.filter(status='FAILED')
            .select_related('subscription', 'subscription__partner')
            .order_by('-created_at')[:10]
        )
        for event in recent_failed_webhooks:
            if event.status_code is None:
                event.status_code = 500
            if not event.response_body:
                event.response_body = event.last_error or "Delivery failed"

        # 3. Data Quality Gaps Metrics
        latest_year_id = FactProfitLoss.objects.order_by('-year_id').values_list('year_id', flat=True).first()
        if not latest_year_id:
            latest_year_id = FactBalanceSheet.objects.order_by('-year_id').values_list('year_id', flat=True).first()

        latest_year_label = "Current Year"
        if latest_year_id:
            year_obj = DimYears.objects.filter(year_id=latest_year_id).first()
            if year_obj and year_obj.year_label:
                latest_year_label = year_obj.year_label
            else:
                latest_year_label = f"FY {latest_year_id}"

        all_companies = list(DimCompany.objects.all().order_by('company_name'))
        total_companies = len(all_companies)

        if latest_year_id:
            symbols_with_pl = set(
                FactProfitLoss.objects.filter(year_id=latest_year_id).values_list('symbol_id', flat=True)
            )
            symbols_with_bs = set(
                FactBalanceSheet.objects.filter(year_id=latest_year_id).values_list('symbol_id', flat=True)
            )
        else:
            symbols_with_pl = set()
            symbols_with_bs = set()

        companies_with_gaps = []
        for comp in all_companies:
            missing_pl = comp.symbol_id not in symbols_with_pl
            missing_bs = comp.symbol_id not in symbols_with_bs
            if missing_pl or missing_bs:
                companies_with_gaps.append({
                    'symbol': comp.symbol_id,
                    'company_name': comp.company_name,
                    'sector': comp.sector,
                    'missing_pl': missing_pl,
                    'missing_bs': missing_bs,
                })

        data_quality_gaps_count = len(companies_with_gaps)

        # 4. ML Anomalies (Significant health score shifts, e.g. dropped by >= 2.0 points)
        recent_scores_by_symbol = {}
        for score in FactMlScores.objects.order_by('symbol_id', '-computed_at'):
            sym = score.symbol_id
            if sym not in recent_scores_by_symbol:
                recent_scores_by_symbol[sym] = [score]
            elif len(recent_scores_by_symbol[sym]) == 1:
                recent_scores_by_symbol[sym].append(score)

        ml_anomalies = []
        for sym, scores in recent_scores_by_symbol.items():
            if len(scores) >= 2:
                curr = float(scores[0].overall_score or 0)
                prev = float(scores[1].overall_score or 0)
                diff = round(curr - prev, 2)
                # Significant shift check (>= 2.0 point shift or drop)
                if diff <= -2.0 or abs(diff) >= 2.0:
                    ml_anomalies.append({
                        'symbol': sym,
                        'current_score': curr,
                        'previous_score': prev,
                        'diff': diff,
                        'dropped': diff <= -2.0,
                        'health_label': scores[0].health_label,
                        'computed_at': scores[0].computed_at,
                    })

        ml_anomalies.sort(key=lambda a: a['diff'])
        score_drops = [a for a in ml_anomalies if a['dropped']]
        ml_anomalies_count = len(score_drops) if score_drops else len(ml_anomalies)

        context = {
            'api_usage': {
                'total_calls': total_api_calls,
                'avg_response_ms': avg_response_ms,
                'top_endpoints': top_endpoints,
                'channel_partners': channel_partners,
            },
            'webhook_health': {
                'failed_count': failed_webhooks_count,
                'total_count': total_webhooks_count,
                'success_rate': webhook_success_rate,
                'recent_failed_events': recent_failed_webhooks,
            },
            'channel_partners': channel_partners,
            'data_quality': {
                'gaps_count': data_quality_gaps_count,
                'total_companies': total_companies,
                'current_year_label': latest_year_label,
                'companies_with_gaps': companies_with_gaps[:12],
            },
            'ml_anomalies': {
                'count': ml_anomalies_count,
                'recent_shifts': ml_anomalies[:12],
            },
        }

        return render(request, 'admin_insights.html', context)


# Function-based alias for compatibility with functional URL configs
admin_insights_view = AdminInsightsView.as_view()

