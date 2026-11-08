"""
ML Health Scoring Engine Service for NiftyIQ Platform.
Translates Phase 3 notebook scoring logic (02_ml_scoring.ipynb) into the Django backend:

1. Base PRD Dimensions & Weights (Sum = 100):
   - Profitability (25%): Percentile rank of average OPM% and Net Profit Margin %
   - Growth (20%): 3-Year Sales CAGR percentile rank (penalized by 50% if CAGR < 5%)
   - Leverage (20%): Latest Debt-to-Equity ratio (D/E < 0.1 is 100%, D/E > 2.0 is 0%)
   - Cashflow (15%): Average Cash Conversion Ratio (Operating Cash Flow / Net Profit)
   - Dividend (10%): Consistency (payout > 0) & Strength (payout / 30%) over last 5 years
   - Trend (10%): Sales linear regression slope normalized by mean sales (percentile rank)

2. Structural Exclusions & Dynamic Re-weighting:
   - Banking, NBFC, and Insurance companies are excluded from Leverage and Cashflow metrics.
   - Their available weights (65%) are dynamically re-weighted to 100%.

3. Health Labels (PRD Thresholds):
   - 85+: 'EXCELLENT'
   - 70 - 84: 'GOOD'
   - 50 - 69: 'AVERAGE'
   - 35 - 49: 'WEAK'
   - < 35: 'POOR'
"""

import logging
from decimal import Decimal
import numpy as np
import pandas as pd
from django.utils import timezone
from django.db import transaction

from api.models import (
    DimCompany,
    DimCompanies,
    FactProfitLoss,
    FactBalanceSheet,
    FactCashFlow,
    FactMlScores,
)

logger = logging.getLogger(__name__)

# Base Dimension Weights (must sum to 100)
WEIGHTS = {
    'profitability': 25.0,
    'growth': 20.0,
    'leverage': 20.0,
    'cashflow': 15.0,
    'dividend': 10.0,
    'trend': 10.0,
}

# Sectors where leverage and cashflow metrics are structurally misleading
FINANCIAL_SECTORS = {
    'BANKING',
    'BANKS',
    'NBFC',
    'FINANCIAL SERVICES',
    'INSURANCE',
    'HOUSING FINANCE',
    'ASSET MANAGEMENT',
}

# Well-known financial symbols as safety fallback
KNOWN_FINANCIAL_SYMBOLS = {
    'HDFCBANK', 'ICICIBANK', 'SBIN', 'KOTAKBANK', 'AXISBANK',
    'BAJFINANCE', 'BAJAJFINSV', 'HDFCLIFE', 'SBILIFE', 'ICICIPRULI',
    'CHOLAFIN', 'MUTHOOTFIN', 'SHRIRAMFIN', 'BANKBARODA', 'PNB',
    'CANBK', 'INDUSINDBK', 'BANDHANBNK', 'FEDERALBNK', 'IDFCFIRSTB',
    'AUBANK', 'LICHSGFIN', 'RECLTD', 'PFC', 'LICI', 'BAJAJHLDNG',
}


def assign_health_label(score):
    """
    Assign qualitative health label based on final score:
      85+: 'EXCELLENT'
      70-84: 'GOOD'
      50-69: 'AVERAGE'
      35-49: 'WEAK'
      <35: 'POOR'
    """
    if score is None or pd.isna(score):
        return 'UNKNOWN'
    s = float(score)
    if s >= 85.0:
        return 'EXCELLENT'
    elif s >= 70.0:
        return 'GOOD'
    elif s >= 50.0:
        return 'AVERAGE'
    elif s >= 35.0:
        return 'WEAK'
    else:
        return 'POOR'


def is_financial_company(sector, symbol):
    """
    Determine whether a company should be excluded from leverage & cashflow metrics.
    """
    if sector and str(sector).strip().upper() in FINANCIAL_SECTORS:
        return True
    if symbol and str(symbol).strip().upper() in KNOWN_FINANCIAL_SYMBOLS:
        return True
    return False


def _safe_float(val, default=None):
    """Convert value to float safely."""
    if val is None or pd.isna(val):
        return default
    try:
        return float(val)
    except (ValueError, TypeError):
        return default


def compute_ml_scores_dataframe(pl_df, bs_df, cf_df, sectors_map):
    """
    Core ML Scoring Algorithm translated directly from 02_ml_scoring.ipynb.
    Takes cleaned DataFrames for P&L, Balance Sheet, Cash Flow, and a sectors dictionary.
    Returns a pandas DataFrame with all intermediate raw features, pillar scores,
    re-weighted overall scores, and assigned health labels.
    """
    # Unique company universe from available data
    symbols = sorted(list(set(pl_df['symbol'].unique()).union(bs_df['symbol'].unique())))
    if not symbols:
        return pd.DataFrame()

    df_scores = pd.DataFrame({'symbol': symbols})

    # ---------------------------------------------------------
    # A. Profitability (Weight: 25%)
    # Average OPM% & NPM% percentile ranked across universe
    # ---------------------------------------------------------
    if not pl_df.empty and 'opm_pct' in pl_df.columns:
        prof = pl_df.groupby('symbol')[['opm_pct', 'net_profit_margin_pct']].mean().reset_index()
        opm_rank = prof['opm_pct'].rank(pct=True)
        npm_rank = prof['net_profit_margin_pct'].rank(pct=True)
        prof['profitability_raw'] = (opm_rank.fillna(npm_rank) + npm_rank.fillna(opm_rank)) / 2.0
        df_scores = df_scores.merge(prof[['symbol', 'profitability_raw']], on='symbol', how='left')
    else:
        df_scores['profitability_raw'] = np.nan

    # ---------------------------------------------------------
    # B. Revenue Growth (Weight: 20%)
    # 3-Year Sales CAGR percentile rank, penalized by 50% if < 5%
    # ---------------------------------------------------------
    def calc_3y_growth(group):
        g = group.sort_values('year_id')
        g_valid = g[g['sales'].notna() & (g['sales'] > 0)]
        if len(g_valid) >= 4:
            recent = g_valid.iloc[-1]['sales']
            past = g_valid.iloc[-4]['sales']
            if past > 0:
                return ((recent / past) ** (1.0 / 3.0) - 1.0) * 100.0
        elif len(g_valid) >= 2:
            periods = len(g_valid) - 1
            recent = g_valid.iloc[-1]['sales']
            past = g_valid.iloc[0]['sales']
            if past > 0 and periods > 0:
                return ((recent / past) ** (1.0 / periods) - 1.0) * 100.0
        return np.nan

    if not pl_df.empty and 'sales' in pl_df.columns:
        try:
            growth_s = pl_df.groupby('symbol').apply(calc_3y_growth, include_groups=False)
        except TypeError:
            growth_s = pl_df.groupby('symbol').apply(calc_3y_growth)
        growth = growth_s.reset_index(name='sales_cagr_3y')
        growth['growth_raw'] = growth['sales_cagr_3y'].rank(pct=True)
        growth.loc[growth['sales_cagr_3y'] < 5.0, 'growth_raw'] *= 0.5
        df_scores = df_scores.merge(growth[['symbol', 'growth_raw']], on='symbol', how='left')
    else:
        df_scores['growth_raw'] = np.nan

    # ---------------------------------------------------------
    # C. Leverage (Weight: 20%)
    # Latest Debt to Equity. D/E < 0.1 is 1.0, > 2.0 is 0.0
    # ---------------------------------------------------------
    if not bs_df.empty and 'debt_to_equity' in bs_df.columns:
        latest_bs = bs_df.sort_values('year_id').groupby('symbol').tail(1).copy()
        latest_bs['debt_to_equity'] = pd.to_numeric(latest_bs['debt_to_equity'], errors='coerce')
        latest_bs['leverage_raw'] = 1.0 - np.clip((latest_bs['debt_to_equity'] - 0.1) / 1.9, 0.0, 1.0)
        latest_bs.loc[latest_bs['debt_to_equity'] < 0.1, 'leverage_raw'] = 1.0
        latest_bs.loc[latest_bs['debt_to_equity'] > 2.0, 'leverage_raw'] = 0.0
        df_scores = df_scores.merge(latest_bs[['symbol', 'leverage_raw']], on='symbol', how='left')
    else:
        df_scores['leverage_raw'] = np.nan

    # ---------------------------------------------------------
    # D. Cashflow Quality (Weight: 15%)
    # Average Cash Conversion Ratio (scaled by 1.2, max 1.0)
    # ---------------------------------------------------------
    if not cf_df.empty:
        ccr_df = cf_df.copy()
        if 'cash_conversion_ratio' not in ccr_df.columns or ccr_df['cash_conversion_ratio'].isna().all():
            if not pl_df.empty and 'net_profit' in pl_df.columns and 'operating_activity' in ccr_df.columns:
                ccr_df = ccr_df.merge(pl_df[['symbol', 'year_id', 'net_profit']], on=['symbol', 'year_id'], how='left')
                ccr_df['cash_conversion_ratio'] = np.where(
                    ccr_df['net_profit'] != 0,
                    ccr_df['operating_activity'] / ccr_df['net_profit'],
                    np.nan
                )
            else:
                ccr_df['cash_conversion_ratio'] = np.nan
        ccr = ccr_df.groupby('symbol')['cash_conversion_ratio'].mean().reset_index()
        ccr['cashflow_raw'] = np.clip(ccr['cash_conversion_ratio'] / 1.2, 0.0, 1.0)
        df_scores = df_scores.merge(ccr[['symbol', 'cashflow_raw']], on='symbol', how='left')
    else:
        df_scores['cashflow_raw'] = np.nan

    # ---------------------------------------------------------
    # E. Dividend Track Record (Weight: 10%)
    # Consistency (payout > 0) & Strength (payout / 30%) over 5 years
    # ---------------------------------------------------------
    def calc_div(group):
        g = group.sort_values('year_id').tail(5)
        payouts = g['dividend_payout_pct'].dropna()
        if len(payouts) == 0 or len(g) == 0:
            return 0.0
        consistency = (payouts > 0).sum() / len(g)
        avg_p = payouts.mean()
        strength = min(avg_p / 30.0, 1.0) if avg_p > 0 else 0.0
        return consistency * strength

    if not pl_df.empty and 'dividend_payout_pct' in pl_df.columns:
        try:
            div_s = pl_df.groupby('symbol').apply(calc_div, include_groups=False)
        except TypeError:
            div_s = pl_df.groupby('symbol').apply(calc_div)
        div = div_s.reset_index(name='dividend_raw')
        df_scores = df_scores.merge(div[['symbol', 'dividend_raw']], on='symbol', how='left')
    else:
        df_scores['dividend_raw'] = np.nan

    # ---------------------------------------------------------
    # F. Growth Trend (Weight: 10%)
    # Linear regression slope of sales over last 5 years / mean sales
    # ---------------------------------------------------------
    def calc_trend(group):
        g = group.sort_values('year_id').tail(5)
        s = g['sales'].dropna()
        if len(s) < 3 or s.mean() == 0:
            return np.nan
        x = np.arange(len(s))
        y = s.values
        slope, _ = np.polyfit(x, y, 1)
        return slope / s.mean()

    if not pl_df.empty and 'sales' in pl_df.columns:
        try:
            trend_s = pl_df.groupby('symbol').apply(calc_trend, include_groups=False)
        except TypeError:
            trend_s = pl_df.groupby('symbol').apply(calc_trend)
        trend = trend_s.reset_index(name='trend_slope')
        trend['trend_raw'] = trend['trend_slope'].rank(pct=True)
        trend.loc[trend['trend_slope'] < 0, 'trend_raw'] *= 0.5
        df_scores = df_scores.merge(trend[['symbol', 'trend_raw']], on='symbol', how='left')
    else:
        df_scores['trend_raw'] = np.nan

    # ---------------------------------------------------------
    # Clear Banks/NBFCs from Leverage and Cashflow penalties
    # ---------------------------------------------------------
    for idx, row in df_scores.iterrows():
        sym = row['symbol']
        sec = sectors_map.get(sym, '')
        if is_financial_company(sec, sym):
            df_scores.at[idx, 'leverage_raw'] = np.nan
            df_scores.at[idx, 'cashflow_raw'] = np.nan

    # ---------------------------------------------------------
    # Dynamic Re-weighting to 100
    # ---------------------------------------------------------
    metrics = ['profitability_raw', 'growth_raw', 'leverage_raw', 'cashflow_raw', 'dividend_raw', 'trend_raw']
    w_keys = ['profitability', 'growth', 'leverage', 'cashflow', 'dividend', 'trend']

    earned_list = []
    avail_list = []
    overall_list = []

    for _, row in df_scores.iterrows():
        earned = 0.0
        available = 0.0
        for m, k in zip(metrics, w_keys):
            val = row.get(m)
            if pd.notna(val):
                earned += float(val) * WEIGHTS[k]
                available += WEIGHTS[k]
        earned_list.append(earned)
        avail_list.append(available)
        if available > 0:
            score = round((earned / available) * 100.0, 2)
        else:
            score = np.nan
        overall_list.append(score)

    df_scores['earned'] = earned_list
    df_scores['available_weight'] = avail_list
    df_scores['overall_score'] = overall_list
    df_scores['health_label'] = df_scores['overall_score'].apply(assign_health_label)

    # Calculate absolute pillar scores out of their respective weight allocations
    df_scores['profitability_score'] = df_scores['profitability_raw'].apply(
        lambda x: round(float(x) * WEIGHTS['profitability'], 2) if pd.notna(x) else None
    )
    df_scores['growth_score'] = df_scores['growth_raw'].apply(
        lambda x: round(float(x) * WEIGHTS['growth'], 2) if pd.notna(x) else None
    )
    df_scores['leverage_score'] = df_scores['leverage_raw'].apply(
        lambda x: round(float(x) * WEIGHTS['leverage'], 2) if pd.notna(x) else None
    )
    df_scores['cashflow_score'] = df_scores['cashflow_raw'].apply(
        lambda x: round(float(x) * WEIGHTS['cashflow'], 2) if pd.notna(x) else None
    )
    df_scores['dividend_score'] = df_scores['dividend_raw'].apply(
        lambda x: round(float(x) * WEIGHTS['dividend'], 2) if pd.notna(x) else None
    )
    df_scores['trend_score'] = df_scores['trend_raw'].apply(
        lambda x: round(float(x) * WEIGHTS['trend'], 2) if pd.notna(x) else None
    )

    return df_scores


def calculate_and_save_health_scores(symbol=None, create_snapshot=False, quiet=False):
    """
    Main entry point for Phase 3 ML Scoring Engine.
    1. Fetches all historical fundamental records for P&L, Balance Sheet, and Cash Flow
       (excluding TTM records for historical consistency).
    2. Executes the 6-pillar ML scoring algorithm with dynamic re-weighting for financial firms.
    3. Saves or updates the resulting scores and labels in the FactMlScores model.
    4. Triggers webhook notifications for significant score shifts (>= 2.0 points).
    
    Returns a comprehensive execution summary dictionary.
    """
    now = timezone.now()

    # 1. Fetch Universe & Sectors Mapping
    company_records = list(DimCompany.objects.all().values('symbol_id', 'sector', 'company_name'))
    sectors_map = {c['symbol_id']: c['sector'] for c in company_records}
    names_map = {c['symbol_id']: c['company_name'] for c in company_records}

    # 2. Query Financial Statements from Database (Excluding TTM)
    pl_records = list(
        FactProfitLoss.objects.exclude(year_label='TTM')
        .values(
            'symbol_id', 'year_id', 'year_label', 'sales', 'expenses',
            'operating_profit', 'opm_pct', 'net_profit',
            'net_profit_margin_pct', 'dividend_payout_pct'
        )
    )
    bs_records = list(
        FactBalanceSheet.objects.exclude(year_label='TTM')
        .values(
            'symbol_id', 'year_id', 'year_label', 'debt_to_equity',
            'total_assets', 'total_liabilities'
        )
    )
    cf_records = list(
        FactCashFlow.objects.exclude(year_label='TTM')
        .values(
            'symbol_id', 'year_id', 'year_label', 'operating_activity',
            'cash_conversion_ratio', 'net_cash_flow'
        )
    )

    # 3. Format DataFrames with float conversion
    def prepare_df(records, rename_symbol=True):
        if not records:
            return pd.DataFrame()
        df = pd.DataFrame(records)
        if rename_symbol and 'symbol_id' in df.columns:
            df.rename(columns={'symbol_id': 'symbol'}, inplace=True)
        # Convert Decimals and strings to float
        for col in df.columns:
            if col not in ('symbol', 'year_label'):
                df[col] = pd.to_numeric(df[col], errors='coerce')
        return df

    pl_df = prepare_df(pl_records)
    bs_df = prepare_df(bs_records)
    cf_df = prepare_df(cf_records)

    # 4. Compute Scores via ML Engine
    df_scores = compute_ml_scores_dataframe(pl_df, bs_df, cf_df, sectors_map)
    if df_scores.empty:
        logger.warning("ML Scoring Engine: No fundamental records found to score.")
        return {
            'total_companies': 0,
            'updated_count': 0,
            'distribution': {},
            'average_score': 0.0,
            'results': []
        }

    # Filter to specific symbol if requested
    if symbol:
        target_sym = str(symbol).strip().upper()
        df_target = df_scores[df_scores['symbol'].str.upper() == target_sym]
        if df_target.empty:
            logger.warning(f"Symbol '{target_sym}' not found in scoring universe.")
    else:
        df_target = df_scores

    # 5. Database Integration: Persist or Update FactMlScores
    results = []
    distribution = {'EXCELLENT': 0, 'GOOD': 0, 'AVERAGE': 0, 'WEAK': 0, 'POOR': 0, 'UNKNOWN': 0}
    updated_count = 0

    with transaction.atomic():
        for _, row in df_target.iterrows():
            sym = str(row['symbol']).strip()
            overall_score = _safe_float(row.get('overall_score'))
            prof_score = _safe_float(row.get('profitability_score'))
            growth_score = _safe_float(row.get('growth_score'))
            lev_score = _safe_float(row.get('leverage_score'))
            cf_score = _safe_float(row.get('cashflow_score'))
            div_score = _safe_float(row.get('dividend_score'))
            trend_score = _safe_float(row.get('trend_score'))
            health_label = str(row.get('health_label') or assign_health_label(overall_score))

            if health_label in distribution:
                distribution[health_label] += 1
            else:
                distribution['UNKNOWN'] += 1

            # Fetch previous latest score for delta tracking & webhooks
            previous_record = FactMlScores.objects.filter(symbol_id=sym).order_by('-computed_at').first()
            previous_score = float(previous_record.overall_score) if (previous_record and previous_record.overall_score is not None) else None

            # Guarantee DimCompanies foreign key integrity
            dim_comp_fk = DimCompanies.objects.filter(symbol=sym).first()
            if not dim_comp_fk:
                dim_comp_rec = DimCompany.objects.filter(symbol_id=sym).first()
                if dim_comp_rec:
                    dim_comp_fk = dim_comp_rec.symbol
                else:
                    dim_comp_fk = DimCompanies.objects.create(symbol=sym)

            defaults = {
                'overall_score': Decimal(f"{overall_score:.4f}") if overall_score is not None else None,
                'profitability_score': Decimal(f"{prof_score:.4f}") if prof_score is not None else None,
                'growth_score': Decimal(f"{growth_score:.4f}") if growth_score is not None else None,
                'leverage_score': Decimal(f"{lev_score:.4f}") if lev_score is not None else None,
                'cashflow_score': Decimal(f"{cf_score:.4f}") if cf_score is not None else None,
                'dividend_score': Decimal(f"{div_score:.4f}") if div_score is not None else None,
                'trend_score': Decimal(f"{trend_score:.4f}") if trend_score is not None else None,
                'health_label': health_label,
                'computed_at': now,
            }

            if create_snapshot:
                score_obj = FactMlScores.objects.create(
                    symbol=dim_comp_fk,
                    **defaults
                )
            else:
                lookup_computed_at = previous_record.computed_at if previous_record else now
                score_obj, _ = FactMlScores.objects.update_or_create(
                    symbol=dim_comp_fk,
                    computed_at=lookup_computed_at,
                    defaults=defaults
                )

            updated_count += 1

            # Check for significant shift (>= 2.0 points) to dispatch partner webhooks
            score_diff = abs(overall_score - previous_score) if (overall_score is not None and previous_score is not None) else None
            is_new = (previous_score is None) or create_snapshot
            if is_new or (score_diff is not None and score_diff >= 2.0):
                try:
                    from api.services.scoring_engine import trigger_score_updated_webhooks
                    company_obj = DimCompany.objects.filter(symbol_id=sym).first()
                    trigger_score_updated_webhooks(
                        symbol_str=sym,
                        company=company_obj,
                        overall_score=overall_score or 0.0,
                        health_label=health_label,
                        previous_score=previous_score,
                        prof_score=prof_score or 0.0,
                        solv_score=lev_score or 0.0,
                        eff_score=cf_score or 0.0,
                        growth_score=growth_score or 0.0
                    )
                except Exception as w_exc:
                    logger.debug(f"Webhook dispatch deferred for {sym}: {w_exc}")

            results.append({
                'symbol': sym,
                'company_name': names_map.get(sym, sym),
                'sector': sectors_map.get(sym, 'Unknown'),
                'overall_score': overall_score,
                'health_label': health_label,
                'previous_score': previous_score,
                'pillar_scores': {
                    'profitability': prof_score,
                    'growth': growth_score,
                    'leverage': lev_score,
                    'cashflow': cf_score,
                    'dividend': div_score,
                    'trend': trend_score,
                }
            })

    valid_scores = [r['overall_score'] for r in results if r['overall_score'] is not None]
    average_score = round(sum(valid_scores) / len(valid_scores), 2) if valid_scores else 0.0

    return {
        'total_companies': len(df_target),
        'updated_count': updated_count,
        'average_score': average_score,
        'distribution': distribution,
        'results': results,
    }

