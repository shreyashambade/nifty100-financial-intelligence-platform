from rest_framework import serializers
from .models import (
    DimCompany, 
    FactProfitLoss, 
    FactMlScores, 
    FactAnalysis, 
    FactBalanceSheet, 
    FactCashFlow,
    ChannelPartner,
    WebhookSubscription,
)


class FactProfitLossSerializer(serializers.ModelSerializer):
    sales = serializers.DecimalField(max_digits=15, decimal_places=4, required=False, allow_null=True)
    expenses = serializers.DecimalField(max_digits=15, decimal_places=4, required=False, allow_null=True)
    operating_profit = serializers.DecimalField(max_digits=15, decimal_places=4, required=False, allow_null=True)
    opm_pct = serializers.DecimalField(max_digits=15, decimal_places=4, required=False, allow_null=True)
    other_income = serializers.DecimalField(max_digits=15, decimal_places=4, required=False, allow_null=True)
    interest = serializers.DecimalField(max_digits=15, decimal_places=4, required=False, allow_null=True)
    depreciation = serializers.DecimalField(max_digits=15, decimal_places=4, required=False, allow_null=True)
    profit_before_tax = serializers.DecimalField(max_digits=15, decimal_places=4, required=False, allow_null=True)
    tax_pct = serializers.DecimalField(max_digits=15, decimal_places=4, required=False, allow_null=True)
    net_profit = serializers.DecimalField(max_digits=15, decimal_places=4, required=False, allow_null=True)
    eps = serializers.DecimalField(max_digits=15, decimal_places=4, required=False, allow_null=True)
    dividend_payout_pct = serializers.DecimalField(max_digits=15, decimal_places=4, required=False, allow_null=True)
    net_profit_margin_pct = serializers.DecimalField(max_digits=15, decimal_places=4, required=False, allow_null=True)
    expense_ratio_pct = serializers.DecimalField(max_digits=15, decimal_places=4, required=False, allow_null=True)
    interest_coverage = serializers.DecimalField(max_digits=15, decimal_places=4, required=False, allow_null=True)
    asset_turnover = serializers.DecimalField(max_digits=15, decimal_places=4, required=False, allow_null=True)
    return_on_assets = serializers.DecimalField(max_digits=15, decimal_places=4, required=False, allow_null=True)

    class Meta:
        model = FactProfitLoss
        fields = '__all__'


class FactBalanceSheetSerializer(serializers.ModelSerializer):
    equity_capital = serializers.DecimalField(max_digits=15, decimal_places=4, required=False, allow_null=True)
    reserves = serializers.DecimalField(max_digits=15, decimal_places=4, required=False, allow_null=True)
    borrowings = serializers.DecimalField(max_digits=15, decimal_places=4, required=False, allow_null=True)
    other_liabilities = serializers.DecimalField(max_digits=15, decimal_places=4, required=False, allow_null=True)
    total_liabilities = serializers.DecimalField(max_digits=15, decimal_places=4, required=False, allow_null=True)
    fixed_assets = serializers.DecimalField(max_digits=15, decimal_places=4, required=False, allow_null=True)
    cwip = serializers.DecimalField(max_digits=15, decimal_places=4, required=False, allow_null=True)
    investments = serializers.DecimalField(max_digits=15, decimal_places=4, required=False, allow_null=True)
    other_assets = serializers.DecimalField(max_digits=15, decimal_places=4, required=False, allow_null=True)
    total_assets = serializers.DecimalField(max_digits=15, decimal_places=4, required=False, allow_null=True)
    debt_to_equity = serializers.DecimalField(max_digits=15, decimal_places=4, required=False, allow_null=True)
    equity_ratio = serializers.DecimalField(max_digits=15, decimal_places=4, required=False, allow_null=True)
    book_value_per_share = serializers.DecimalField(max_digits=15, decimal_places=4, required=False, allow_null=True)

    class Meta:
        model = FactBalanceSheet
        fields = '__all__'


class FactCashFlowSerializer(serializers.ModelSerializer):
    operating_activity = serializers.DecimalField(max_digits=15, decimal_places=4, required=False, allow_null=True)
    investing_activity = serializers.DecimalField(max_digits=15, decimal_places=4, required=False, allow_null=True)
    financing_activity = serializers.DecimalField(max_digits=15, decimal_places=4, required=False, allow_null=True)
    net_cash_flow = serializers.DecimalField(max_digits=15, decimal_places=4, required=False, allow_null=True)
    free_cash_flow = serializers.DecimalField(max_digits=15, decimal_places=4, required=False, allow_null=True)
    cash_conversion_ratio = serializers.DecimalField(max_digits=15, decimal_places=4, required=False, allow_null=True)

    class Meta:
        model = FactCashFlow
        fields = '__all__'


class FactAnalysisSerializer(serializers.ModelSerializer):
    compounded_sales_growth_pct = serializers.DecimalField(max_digits=15, decimal_places=4, required=False, allow_null=True)
    compounded_profit_growth_pct = serializers.DecimalField(max_digits=15, decimal_places=4, required=False, allow_null=True)
    stock_price_cagr_pct = serializers.DecimalField(max_digits=15, decimal_places=4, required=False, allow_null=True)
    roe_pct = serializers.DecimalField(max_digits=15, decimal_places=4, required=False, allow_null=True)

    class Meta:
        model = FactAnalysis
        fields = '__all__'


class FactMlScoresSerializer(serializers.ModelSerializer):
    overall_score = serializers.DecimalField(max_digits=15, decimal_places=4, required=False, allow_null=True)
    profitability_score = serializers.DecimalField(max_digits=15, decimal_places=4, required=False, allow_null=True)
    growth_score = serializers.DecimalField(max_digits=15, decimal_places=4, required=False, allow_null=True)
    leverage_score = serializers.DecimalField(max_digits=15, decimal_places=4, required=False, allow_null=True)
    cashflow_score = serializers.DecimalField(max_digits=15, decimal_places=4, required=False, allow_null=True)
    dividend_score = serializers.DecimalField(max_digits=15, decimal_places=4, required=False, allow_null=True)
    trend_score = serializers.DecimalField(max_digits=15, decimal_places=4, required=False, allow_null=True)

    class Meta:
        model = FactMlScores
        fields = '__all__'


class CompanySerializer(serializers.ModelSerializer):
    # This nests the P&L data inside the company endpoint
    profit_and_loss = FactProfitLossSerializer(source='factprofitloss_set', many=True, read_only=True)
    face_value = serializers.DecimalField(max_digits=15, decimal_places=4, required=False, allow_null=True)
    book_value = serializers.DecimalField(max_digits=15, decimal_places=4, required=False, allow_null=True)
    
    class Meta:
        model = DimCompany
        fields = [
            'symbol', 
            'company_name', 
            'sector', 
            'sub_sector', 
            'website', 
            'face_value',
            'book_value',
            'profit_and_loss'
        ]


class PartnerCompanySerializer(serializers.ModelSerializer):
    """
    Serializer for Phase 2 Secure Channel Partner API.
    Provides standardized fundamental ratios, automated health scores, and financial metrics.
    """
    symbol = serializers.CharField(source='symbol_id')
    face_value = serializers.DecimalField(max_digits=15, decimal_places=4, required=False, allow_null=True)
    book_value = serializers.DecimalField(max_digits=15, decimal_places=4, required=False, allow_null=True)
    health_score = serializers.SerializerMethodField()
    health_label = serializers.SerializerMethodField()
    scores_breakdown = serializers.SerializerMethodField()
    ratios = serializers.SerializerMethodField()
    financials = serializers.SerializerMethodField()
    urls = serializers.SerializerMethodField()

    class Meta:
        model = DimCompany
        fields = [
            'symbol',
            'company_name',
            'sector',
            'sub_sector',
            'face_value',
            'book_value',
            'health_score',
            'health_label',
            'scores_breakdown',
            'ratios',
            'financials',
            'urls',
        ]

    def _get_ml_score(self, obj):
        ml_map = self.context.get('ml_scores')
        if ml_map is not None:
            return ml_map.get(obj.symbol_id)
        return FactMlScores.objects.filter(symbol_id=obj.symbol_id).order_by('-computed_at').first()

    def _get_analysis(self, obj):
        analysis_map = self.context.get('analysis_map')
        if analysis_map is not None:
            return analysis_map.get(obj.symbol_id)
        return FactAnalysis.objects.filter(symbol_id=obj.symbol_id).order_by('-period_label').first()

    def _get_balance_sheet(self, obj):
        bs_map = self.context.get('bs_map')
        if bs_map is not None:
            return bs_map.get(obj.symbol_id)
        return FactBalanceSheet.objects.filter(symbol_id=obj.symbol_id).order_by('-year_id').first()

    def _get_profit_loss(self, obj):
        pl_map = self.context.get('pl_map')
        if pl_map is not None:
            return pl_map.get(obj.symbol_id)
        return FactProfitLoss.objects.filter(symbol_id=obj.symbol_id).order_by('-year_id').first()

    def _get_cash_flow(self, obj):
        cf_map = self.context.get('cf_map')
        if cf_map is not None:
            return cf_map.get(obj.symbol_id)
        return FactCashFlow.objects.filter(symbol_id=obj.symbol_id).order_by('-year_id').first()

    def get_health_score(self, obj):
        score_obj = self._get_ml_score(obj)
        if score_obj and score_obj.overall_score is not None:
            return float(score_obj.overall_score)
        return None

    def get_health_label(self, obj):
        score_obj = self._get_ml_score(obj)
        return score_obj.health_label if score_obj else None

    def get_scores_breakdown(self, obj):
        score_obj = self._get_ml_score(obj)
        if not score_obj:
            return None
        return {
            'profitability': float(score_obj.profitability_score) if score_obj.profitability_score is not None else None,
            'growth': float(score_obj.growth_score) if score_obj.growth_score is not None else None,
            'leverage': float(score_obj.leverage_score) if score_obj.leverage_score is not None else None,
            'cashflow': float(score_obj.cashflow_score) if score_obj.cashflow_score is not None else None,
            'dividend': float(score_obj.dividend_score) if score_obj.dividend_score is not None else None,
            'trend': float(score_obj.trend_score) if score_obj.trend_score is not None else None,
        }

    def get_ratios(self, obj):
        analysis = self._get_analysis(obj)
        bs = self._get_balance_sheet(obj)
        pl = self._get_profit_loss(obj)

        return {
            'roe_pct': float(analysis.roe_pct) if analysis and analysis.roe_pct is not None else None,
            'debt_to_equity': float(bs.debt_to_equity) if bs and bs.debt_to_equity is not None else None,
            'opm_pct': float(pl.opm_pct) if pl and pl.opm_pct is not None else None,
            'eps': float(pl.eps) if pl and pl.eps is not None else None,
            'net_profit_margin_pct': float(pl.net_profit_margin_pct) if pl and pl.net_profit_margin_pct is not None else None,
            'interest_coverage': float(pl.interest_coverage) if pl and pl.interest_coverage is not None else None,
            'return_on_assets': float(pl.return_on_assets) if pl and pl.return_on_assets is not None else None,
        }

    def get_financials(self, obj):
        pl = self._get_profit_loss(obj)
        cf = self._get_cash_flow(obj)

        return {
            'sales': float(pl.sales) if pl and pl.sales is not None else None,
            'net_profit': float(pl.net_profit) if pl and pl.net_profit is not None else None,
            'operating_cash_flow': float(cf.operating_activity) if cf and cf.operating_activity is not None else None,
            'free_cash_flow': float(cf.free_cash_flow) if cf and cf.free_cash_flow is not None else None,
        }

    def get_urls(self, obj):
        return {
            'website': obj.website or None,
            'nse_url': obj.nse_url or None,
            'bse_url': obj.bse_url or None,
        }


class WebhookSubscriptionSerializer(serializers.ModelSerializer):
    """
    Serializer for Channel Partner Webhook Subscriptions.
    """
    class Meta:
        model = WebhookSubscription
        fields = ['id', 'event_type', 'target_url', 'is_active', 'created_at']
        read_only_fields = ['id', 'created_at']


class ChannelPartnerKeySerializer(serializers.ModelSerializer):
    """
    Serializer for Channel Partner API Key metadata and credentials.
    """
    key_id = serializers.CharField(source='api_key', read_only=True)

    class Meta:
        model = ChannelPartner
        fields = ['name', 'email', 'api_key', 'key_id', 'tier', 'is_active', 'created_at']
        read_only_fields = ['name', 'email', 'api_key', 'key_id', 'tier', 'is_active', 'created_at']

