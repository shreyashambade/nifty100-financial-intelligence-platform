"""
URL configuration for nifty_core project.

The `urlpatterns` list routes URLs to views. For more information please see:
    https://docs.djangoproject.com/en/6.1/topics/http/urls/
Examples:
Function views
    1. Add an import:  from my_app import views
    2. Add a URL to urlpatterns:  path('', views.home, name='home')
Class-based views
    1. Add an import:  from other_app.views import Home
    2. Add a URL to urlpatterns:  path('', Home.as_view(), name='home')
Including another URLconf
    1. Import the include() function: from django.urls import include, path
    2. Add a URL to urlpatterns:  path('blog/', include('blog.urls'))
"""


from django.contrib import admin
from django.urls import path, include




from api.views import (
    home_page, company_list, company_detail, compare_page, 
    screener_page, sector_detail, company_charts_api 
)

urlpatterns = [
    path('admin/', admin.site.urls),
    path('api/v1/', include('api.urls')),
    
    # Web App Routes
    path('', home_page, name='home'), 
    path('companies/', company_list, name='company_list'),
    
    path('company/<str:symbol>/', company_detail, name='company_detail'),
    path('compare/', compare_page, name='compare'),
    
    path('screener/', screener_page, name='screener'),
    
    path('sector/<str:name>/', sector_detail, name='sector_detail'),
    path('api/v1/companies/<str:symbol>/charts/', company_charts_api, name='company_charts_api'),
]