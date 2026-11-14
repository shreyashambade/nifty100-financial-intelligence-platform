from django.urls import path, include
from rest_framework.routers import DefaultRouter
from .views import CompanyViewSet, company_charts_api, company_search_api
from .cron_views import RunScheduledTasksView


router = DefaultRouter()
router.register(r'companies', CompanyViewSet)

urlpatterns = [
    path('internal/run-ml-tasks/', RunScheduledTasksView.as_view(), name='internal_run_ml_tasks'),
    path('companies/search/', company_search_api, name='api_company_search'),
    path('companies/<str:symbol>/charts/', company_charts_api, name='api_company_charts'),
    path('', include(router.urls)),
]
