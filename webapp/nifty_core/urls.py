"""
URL configuration for nifty_core project.

The `urlpatterns` list routes URLs to views. For more information please see:
    https://docs.djangoproject.com/en/6.1/topics/http/urls/
"""

from django.contrib import admin
from django.urls import path, include
from drf_spectacular.views import (
    SpectacularAPIView,
    SpectacularSwaggerView,
    SpectacularRedocView,
)

from api.views import (
    home_page, company_list, company_detail, compare_page, 
    screener_page, sector_detail, company_charts_api, screener_api, 
    compare_api, company_search_api, export_companies_csv,
    AdminInsightsView, SectorDetailView,
)

urlpatterns = [
    path('admin/', admin.site.urls),
    path('admin-insights/', AdminInsightsView.as_view(), name='admin_insights'),
    
    # OpenAPI 3.0 Documentation Schema & Interactive UIs
    path('api/schema/', SpectacularAPIView.as_view(), name='schema'),
    path('api/docs/', SpectacularSwaggerView.as_view(url_name='schema'), name='swagger-ui'),
    path('api/redoc/', SpectacularRedocView.as_view(url_name='schema'), name='redoc'),

    # 1. Custom API Endpoints (Must go BEFORE the router)
    path('api/v1/companies/search/', company_search_api, name='company_search_api'),
    path('api/v1/companies/export/', export_companies_csv, name='api_export_companies_csv'),
    path('api/v1/screener/results/', screener_api, name='screener_api'),
    path('api/v1/compare/results/', compare_api, name='compare_api'),
    path('api/v1/companies/<str:symbol>/charts/', company_charts_api, name='company_charts_api'),
    
    # 2. Phase 1 DRF Router
    path('api/v1/', include('api.urls')),
    
    # 3. Secure Channel Partner API (HMAC-SHA256 Protected - Stream C PRD)
    path('api/partner/v1/', include('api.partner_urls')),
    path('api/v2/partner/', include('api.partner_urls')),  # Backward compatibility alias
    
    # 4. Web App Routes
    path('', home_page, name='home'), 
    path('companies/', company_list, name='company_list'),
    path('companies/', company_list, name='companies'),
    path('companies/export/', export_companies_csv, name='export_companies_csv'),
    path('company/<str:symbol>/', company_detail, name='company_detail'),
    path('compare/', compare_page, name='compare'),
    path('screener/', screener_page, name='screener'),
    path('sectors/<str:sector_name>/', SectorDetailView.as_view(), name='sector_detail'),
    path('sector/<str:sector_name>/', SectorDetailView.as_view()),
]
