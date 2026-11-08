from django.urls import path, include
from rest_framework.routers import DefaultRouter
from .partner_views import PartnerCompanyViewSet, PartnerWebhookViewSet, PartnerKeyViewSet

partner_router = DefaultRouter()
partner_router.register(r'companies', PartnerCompanyViewSet, basename='partner-companies')
partner_router.register(r'webhooks', PartnerWebhookViewSet, basename='partner-webhooks')
partner_router.register(r'keys', PartnerKeyViewSet, basename='partner-keys')

urlpatterns = [
    path('', include(partner_router.urls)),
    path('health-scores/', PartnerCompanyViewSet.as_view({'get': 'health_scores'}), name='partner-health-scores-direct'),
    path('scores/', PartnerCompanyViewSet.as_view({'get': 'scores'}), name='partner-scores-direct'),
    path('overview/', PartnerCompanyViewSet.as_view({'get': 'overview'}), name='partner-overview-direct'),
    path('bulk-financials/', PartnerCompanyViewSet.as_view({'get': 'bulk_financials'}), name='partner-bulk-financials-direct'),
    path('screener/', PartnerCompanyViewSet.as_view({'get': 'screener'}), name='partner-screener-direct'),
]
