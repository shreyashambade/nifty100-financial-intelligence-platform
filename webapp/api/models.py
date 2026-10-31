# This is an auto-generated Django model module.
# You'll have to do the following manually to clean this up:
#   * Rearrange models' order
#   * Make sure each model has one field with primary_key=True
#   * Make sure each ForeignKey and OneToOneField has `on_delete` set to the desired behavior
#   * Remove `managed = False` lines if you wish to allow Django to create, modify, and delete the table
# Feel free to rename the models, but don't rename db_table values or field names.
from django.db import models


class DimCompanies(models.Model):
    symbol = models.CharField(primary_key=True, max_length=255)

    class Meta:
        managed = False
        db_table = 'dim_companies'
        
    def __str__(self):
            return str(self.symbol)


class DimCompany(models.Model):
    symbol = models.OneToOneField(DimCompanies, models.DO_NOTHING, db_column='symbol', primary_key=True)
    company_name = models.CharField(max_length=255, blank=True, null=True)
    sector = models.CharField(max_length=255, blank=True, null=True)
    sub_sector = models.CharField(max_length=255, blank=True, null=True)
    company_logo = models.TextField(blank=True, null=True)
    website = models.TextField(blank=True, null=True)
    nse_url = models.TextField(blank=True, null=True)
    bse_url = models.TextField(blank=True, null=True)
    face_value = models.DecimalField(blank=True, null=True)
    book_value = models.DecimalField(blank=True, null=True)
    about_company = models.TextField(blank=True, null=True)
    
    
    class Meta:
        managed = False
        db_table = 'dim_company'
        
    


class DimHealthLabel(models.Model):
    label_id = models.AutoField(primary_key=True)
    label_name = models.CharField(max_length=50, blank=True, null=True)
    min_score = models.DecimalField(blank=True, null=True)
    max_score = models.DecimalField(blank=True, null=True)
    color_hex = models.CharField(max_length=10, blank=True, null=True)

    class Meta:
        managed = False
        db_table = 'dim_health_label'


class DimYears(models.Model):
    year_id = models.IntegerField(primary_key=True)
    year_label = models.CharField(max_length=50, blank=True, null=True)
    sort_order = models.IntegerField(blank=True, null=True)
    fiscal_year = models.IntegerField(blank=True, null=True)
    quarter = models.CharField(max_length=10, blank=True, null=True)
    is_ttm = models.BooleanField(blank=True, null=True)
    is_half_year = models.BooleanField(blank=True, null=True)

    class Meta:
        managed = False
        db_table = 'dim_years'


class FactAnalysis(models.Model):
    pk = models.CompositePrimaryKey('symbol', 'period_label')
    symbol = models.ForeignKey(DimCompanies, models.DO_NOTHING, db_column='symbol')
    period_label = models.CharField(max_length=50)
    compounded_sales_growth_pct = models.DecimalField(blank=True, null=True)
    compounded_profit_growth_pct = models.DecimalField(blank=True, null=True)
    stock_price_cagr_pct = models.DecimalField(blank=True, null=True)
    roe_pct = models.DecimalField(blank=True, null=True)

    class Meta:
        managed = False
        db_table = 'fact_analysis'


class FactBalanceSheet(models.Model):
    pk = models.CompositePrimaryKey('symbol', 'year_id')
    symbol = models.ForeignKey(DimCompanies, models.DO_NOTHING, db_column='symbol')
    year = models.ForeignKey(DimYears, models.DO_NOTHING)
    year_label = models.CharField(max_length=50, blank=True, null=True)
    sort_order = models.IntegerField(blank=True, null=True)
    equity_capital = models.DecimalField(blank=True, null=True)
    reserves = models.DecimalField(blank=True, null=True)
    borrowings = models.DecimalField(blank=True, null=True)
    other_liabilities = models.DecimalField(blank=True, null=True)
    total_liabilities = models.DecimalField(blank=True, null=True)
    fixed_assets = models.DecimalField(blank=True, null=True)
    cwip = models.DecimalField(blank=True, null=True)
    investments = models.DecimalField(blank=True, null=True)
    other_assets = models.DecimalField(blank=True, null=True)
    total_assets = models.DecimalField(blank=True, null=True)
    debt_to_equity = models.DecimalField(blank=True, null=True)
    equity_ratio = models.DecimalField(blank=True, null=True)
    book_value_per_share = models.DecimalField(blank=True, null=True)

    class Meta:
        managed = False
        db_table = 'fact_balance_sheet'


class FactCashFlow(models.Model):
    pk = models.CompositePrimaryKey('symbol', 'year_id')
    symbol = models.ForeignKey(DimCompanies, models.DO_NOTHING, db_column='symbol')
    year = models.ForeignKey(DimYears, models.DO_NOTHING)
    year_label = models.CharField(max_length=50, blank=True, null=True)
    sort_order = models.IntegerField(blank=True, null=True)
    operating_activity = models.DecimalField(blank=True, null=True)
    investing_activity = models.DecimalField(blank=True, null=True)
    financing_activity = models.DecimalField(blank=True, null=True)
    net_cash_flow = models.DecimalField(blank=True, null=True)
    free_cash_flow = models.DecimalField(blank=True, null=True)
    cash_conversion_ratio = models.DecimalField(blank=True, null=True)

    class Meta:
        managed = False
        db_table = 'fact_cash_flow'


class FactMlScores(models.Model):
    pk = models.CompositePrimaryKey('symbol', 'computed_at')
    symbol = models.ForeignKey(DimCompanies, models.DO_NOTHING, db_column='symbol')
    computed_at = models.DateTimeField()
    overall_score = models.DecimalField(blank=True, null=True)
    profitability_score = models.DecimalField(blank=True, null=True)
    growth_score = models.DecimalField(blank=True, null=True)
    leverage_score = models.DecimalField(blank=True, null=True)
    cashflow_score = models.DecimalField(blank=True, null=True)
    dividend_score = models.DecimalField(blank=True, null=True)
    trend_score = models.DecimalField(blank=True, null=True)
    health_label = models.CharField(max_length=50, blank=True, null=True)

    class Meta:
        managed = False
        db_table = 'fact_ml_scores'


class FactProfitLoss(models.Model):
    pk = models.CompositePrimaryKey('symbol', 'year_id')
    symbol = models.ForeignKey(DimCompanies, models.DO_NOTHING, db_column='symbol')
    year = models.ForeignKey(DimYears, models.DO_NOTHING)
    year_label = models.CharField(max_length=50, blank=True, null=True)
    sort_order = models.IntegerField(blank=True, null=True)
    sales = models.DecimalField(blank=True, null=True)
    expenses = models.DecimalField(blank=True, null=True)
    operating_profit = models.DecimalField(blank=True, null=True)
    opm_pct = models.DecimalField(blank=True, null=True)
    other_income = models.DecimalField(blank=True, null=True)
    interest = models.DecimalField(blank=True, null=True)
    depreciation = models.DecimalField(blank=True, null=True)
    profit_before_tax = models.DecimalField(blank=True, null=True)
    tax_pct = models.DecimalField(blank=True, null=True)
    net_profit = models.DecimalField(blank=True, null=True)
    eps = models.DecimalField(blank=True, null=True)
    dividend_payout_pct = models.DecimalField(blank=True, null=True)
    net_profit_margin_pct = models.DecimalField(blank=True, null=True)
    expense_ratio_pct = models.DecimalField(blank=True, null=True)
    interest_coverage = models.DecimalField(blank=True, null=True)
    asset_turnover = models.DecimalField(blank=True, null=True)
    return_on_assets = models.DecimalField(blank=True, null=True)

    class Meta:
        managed = False
        db_table = 'fact_profit_loss'


class FactProsCons(models.Model):
    symbol = models.ForeignKey(DimCompanies, models.DO_NOTHING, db_column='symbol', blank=True, null=True)
    is_pro = models.BooleanField(blank=True, null=True)
    category = models.CharField(max_length=255, blank=True, null=True)
    text = models.TextField(blank=True, null=True)
    source = models.CharField(max_length=50, blank=True, null=True)
    confidence = models.DecimalField(blank=True, null=True)
    generated_at = models.DateTimeField(blank=True, null=True)

    class Meta:
        managed = False
        db_table = 'fact_pros_cons'
