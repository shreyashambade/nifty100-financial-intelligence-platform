from django.shortcuts import render, get_object_or_404

# Create your views here.    
from rest_framework import viewsets
from django.http import JsonResponse
from .models import DimCompany, FactProfitLoss, FactBalanceSheet, FactCashFlow
from .serializers import CompanySerializer
from .authentication import HMACAuthentication
from .permissions import IsHMACAuthenticated

class CompanyViewSet(viewsets.ReadOnlyModelViewSet):
    queryset = DimCompany.objects.all()
    serializer_class = CompanySerializer
    
    # 1. Cryptographically verify the signature
    authentication_classes = [HMACAuthentication]
    # 2. Strictly enforce that the verification was successful
    permission_classes = [IsHMACAuthenticated]
    
    
# --- WEB APP VIEWS ---
    
def home_page(request):
    # The home page doesn't need the full company list yet
    return render(request, 'index.html')

def company_list(request):
    # Fetch all companies and sort A-Z
    companies = DimCompany.objects.all().order_by('company_name')
    context = {'companies': companies}
    return render(request, 'companies.html', context)


def company_detail(request, symbol):
    # Fetch the specific company by its symbol, or return a 404 Not Found page
    company = get_object_or_404(DimCompany, symbol=symbol)
    context = {'company': company}
    return render(request, 'company_detail.html', context)

def compare_page(request):
    # For now, we just render the page. Later, we will add the AJAX autocomplete logic.
    return render(request, 'compare.html')

def screener_page(request):
    # Fetch distinct sectors to populate the filter dropdown
    sectors = DimCompany.objects.values_list('sector', flat=True).distinct().order_by('sector')
    context = {'sectors': sectors}
    return render(request, 'screener.html', context)

def sector_detail(request, name):
    # Convert URL format back to database format (e.g., "Financial-Services" -> "Financial Services")
    sector_name = name.replace('-', ' ')
    
    # Fetch all companies in this sector
    companies = DimCompany.objects.filter(sector__iexact=sector_name).order_by('company_name')
    
    context = {
        'sector_name': sector_name,
        'companies': companies
    }
    return render(request, 'sector_detail.html', context)


# --- INTERNAL AJAX CHART API ---
def company_charts_api(request, symbol):
    # Fetch historical data from all 3 financial tables
    pl_data = FactProfitLoss.objects.filter(symbol=symbol).order_by('sort_order')
    bs_data = FactBalanceSheet.objects.filter(symbol=symbol).order_by('sort_order')
    cf_data = FactCashFlow.objects.filter(symbol=symbol).order_by('sort_order')
    
    # Extract Data
    labels = [item.year_label for item in pl_data]
    
    # P&L Metrics
    revenue = [float(item.sales) if item.sales else 0 for item in pl_data]
    net_profit = [float(item.net_profit) if item.net_profit else 0 for item in pl_data]
    opm = [float(item.opm_pct) if item.opm_pct else 0 for item in pl_data]
    eps = [float(item.eps) if item.eps else 0 for item in pl_data]
    
    # Balance Sheet Metrics
    debt_to_equity = [float(item.debt_to_equity) if item.debt_to_equity else 0 for item in bs_data]
    
    # Cash Flow Metrics
    operating_cash = [float(item.operating_activity) if item.operating_activity else 0 for item in cf_data]
    
    return JsonResponse({
        'labels': labels,
        'revenue': revenue,
        'net_profit': net_profit,
        'opm': opm,
        'eps': eps,
        'debt_to_equity': debt_to_equity,
        'operating_cash': operating_cash
    })