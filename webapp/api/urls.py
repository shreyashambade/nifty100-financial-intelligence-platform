from django.urls import path, include
from rest_framework.routers import DefaultRouter
from .views import CompanyViewSet, company_charts_api, company_search_api


router = DefaultRouter()
router.register(r'companies', CompanyViewSet)

urlpatterns = [
    path('companies/search/', company_search_api, name='api_company_search'),
    path('companies/<str:symbol>/charts/', company_charts_api, name='api_company_charts'),
    path('', include(router.urls)),
]
