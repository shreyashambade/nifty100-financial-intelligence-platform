"""
ML Health Scoring Engine Service for NiftyIQ.
Implements a dynamic multi-pillar financial health scoring algorithm (0 to 100)
evaluating Profitability, Solvency, Efficiency, and Growth across the Nifty 100.
"""

import logging
from decimal import Decimal
from django.utils import timezone
from django.db import connection, transaction

from api.models import (
    DimCompany,
    DimCompanies,
    FactMlScores,
    FactAnalysis,
    FactBalanceSheet,
    FactProfitLoss,
    FactCashFlow,
)

logger = logging.getLogger(__name__)

# Sector classification for structural leverage adjustments
FINANCIAL_SECTORS = {
    'BANKING',
    'FINANCIAL SERVICES',
    'NBFC',
    'INSURANCE',
    'HOUSING FINANCE',
    'ASSET MANAGEMENT',
}

# Pillar weights summing to 100%
WEIGHTS = {
    'profitability': 0.35,  # 35%
    'solvency': 0.35,       # 35%
    'efficiency': 0.15,     # 15%
    'growth': 0.15,         # 15%
}


def _to_float(val, default=None):
    """Safely convert Decimal, float, or string to float."""
    if val is None:
        return default
    try:
        f = float(val)
        import math
        if math.isnan(f) or math.isinf(f):
            return default
        return f
    except (ValueError, TypeError):
        return default


def assign_health_label(score):
    """
    Assign qualitative health label based on final score:
      >= 80.0: EXCELLENT
      60.0 - 80.0: GOOD
      40.0 - 60.0: AVERAGE
      20.0 - 40.0: WEAK
      < 20.0: POOR
    """
    if score is None:
        return "PENDING"
    
    score_f = float(score)
    if score_f >= 80.0:
        return "EXCELLENT"
    elif score_f >= 60.0:
        return "GOOD"
    elif score_f >= 40.0:
        return "AVERAGE"
    elif score_f >= 20.0:
        return "WEAK"
    else:
        return "POOR"


def compute_profitability_score(roe_pct, opm_pct, roa_pct=None):
    """
    Pillar 1: Profitability (Weight: 35%)
    Higher ROE and Operating Profit Margins (OPM%) increase score.
    Returns: (pillar_score_0_to_35, normalized_pct_0_to_100)
    """
    roe = _to_float(roe_pct)
    opm = _to_float(opm_pct)
    roa = _to_float(roa_pct)

    roe_score = None
    if roe is not None:
        if roe >= 25.0:
            roe_score = 100.0
        elif roe >= 18.0:
            roe_score = 85.0 + ((roe - 18.0) / 7.0) * 15.0
        elif roe >= 12.0:
            roe_score = 65.0 + ((roe - 12.0) / 6.0) * 20.0
        elif roe >= 6.0:
            roe_score = 35.0 + ((roe - 6.0) / 6.0) * 30.0
        elif roe >= 0.0:
            roe_score = 10.0 + (roe / 6.0) * 25.0
        else:
            roe_score = 0.0

    opm_score = None
    if opm is not None:
        if opm >= 25.0:
            opm_score = 100.0
        elif opm >= 18.0:
            opm_score = 80.0 + ((opm - 18.0) / 7.0) * 20.0
        elif opm >= 12.0:
            opm_score = 60.0 + ((opm - 12.0) / 6.0) * 20.0
        elif opm >= 6.0:
            opm_score = 35.0 + ((opm - 6.0) / 6.0) * 25.0
        elif opm >= 0.0:
            opm_score = 10.0 + (opm / 6.0) * 25.0
        else:
            opm_score = 0.0

    # Fallback to ROA if ROE missing
    if roe_score is None and roa is not None:
        if roa >= 15.0:
            roe_score = 100.0
        elif roa >= 8.0:
            roe_score = 70.0 + ((roa - 8.0) / 7.0) * 30.0
        elif roa >= 3.0:
            roe_score = 40.0 + ((roa - 3.0) / 5.0) * 30.0
        elif roa >= 0.0:
            roe_score = 15.0 + (roa / 3.0) * 25.0
        else:
            roe_score = 0.0

    if roe_score is not None and opm_score is not None:
        norm_prof = 0.5 * roe_score + 0.5 * opm_score
    elif roe_score is not None:
        norm_prof = roe_score
    elif opm_score is not None:
        norm_prof = opm_score
    else:
        norm_prof = 50.0

    norm_prof = min(100.0, max(0.0, norm_prof))
    pillar_score = round(norm_prof * WEIGHTS['profitability'], 2)
    return pillar_score, round(norm_prof, 2)


def compute_solvency_score(debt_to_equity, sector=None, equity_ratio=None, interest_coverage=None):
    """
    Pillar 2: Solvency (Weight: 35%)
    Lower Debt-to-Equity ratios increase the score.
    Penalize heavily for D/E > 2.0.
    Handles financial sector companies with structurally distinct balance sheets.
    Returns: (pillar_score_0_to_35, normalized_pct_0_to_100)
    """
    de = _to_float(debt_to_equity)
    eq_ratio = _to_float(equity_ratio)
    int_cov = _to_float(interest_coverage)
    sec_str = str(sector).strip().upper() if sector else ""
    is_financial = any(fin in sec_str for fin in FINANCIAL_SECTORS)

    if is_financial:
        # Financial institutions (banks/NBFCs) leverage deposits by structure
        if eq_ratio is not None:
            if eq_ratio >= 0.15:
                norm_solv = 95.0
            elif eq_ratio >= 0.10:
                norm_solv = 75.0 + ((eq_ratio - 0.10) / 0.05) * 20.0
            elif eq_ratio >= 0.06:
                norm_solv = 50.0 + ((eq_ratio - 0.06) / 0.04) * 25.0
            else:
                norm_solv = max(10.0, (eq_ratio / 0.06) * 50.0)
        else:
            norm_solv = 75.0  # Moderate default for systemic financial firms
    else:
        if de is None:
            norm_solv = 50.0
        elif de < 0:
            # Negative net worth / negative equity: severe solvency distress
            norm_solv = 0.0
        elif de <= 0.10:
            # Virtually debt-free
            norm_solv = 100.0
        elif de <= 0.50:
            # Very conservative leverage
            norm_solv = 85.0 + ((0.50 - de) / 0.40) * 15.0
        elif de <= 1.0:
            # Healthy, manageable leverage
            norm_solv = 65.0 + ((1.0 - de) / 0.50) * 20.0
        elif de <= 1.5:
            # Moderate leverage
            norm_solv = 40.0 + ((1.5 - de) / 0.50) * 25.0
        elif de <= 2.0:
            # Elevated leverage
            norm_solv = 15.0 + ((2.0 - de) / 0.50) * 25.0
        else:
            # HEAVY PENALTY FOR D/E > 2.0
            if de <= 3.0:
                norm_solv = max(2.0, 15.0 - ((de - 2.0) / 1.0) * 12.0)
            elif de <= 5.0:
                norm_solv = max(0.5, 2.0 - ((de - 3.0) / 2.0) * 1.5)
            else:
                norm_solv = 0.0

        # Further penalize if interest coverage is dangerously low (< 1.5x)
        if int_cov is not None and not is_financial:
            if int_cov < 1.0:
                norm_solv *= 0.5
            elif int_cov < 1.5:
                norm_solv *= 0.75

    norm_solv = min(100.0, max(0.0, norm_solv))
    pillar_score = round(norm_solv * WEIGHTS['solvency'], 2)
    return pillar_score, round(norm_solv, 2)


def compute_efficiency_score(asset_turnover, cash_conversion_ratio=None, free_cash_flow=None, sales=None):
    """
    Pillar 3: Efficiency (Weight: 15%)
    Asset turnover or working capital improvements.
    Returns: (pillar_score_0_to_15, normalized_pct_0_to_100)
    """
    turnover = _to_float(asset_turnover)
    ccr = _to_float(cash_conversion_ratio)
    fcf = _to_float(free_cash_flow)

    turnover_score = None
    if turnover is not None:
        if turnover >= 1.5:
            turnover_score = 100.0
        elif turnover >= 1.0:
            turnover_score = 80.0 + ((turnover - 1.0) / 0.5) * 20.0
        elif turnover >= 0.6:
            turnover_score = 55.0 + ((turnover - 0.6) / 0.4) * 25.0
        elif turnover >= 0.3:
            turnover_score = 25.0 + ((turnover - 0.3) / 0.3) * 30.0
        elif turnover >= 0.0:
            turnover_score = max(5.0, (turnover / 0.3) * 25.0)
        else:
            turnover_score = 0.0

    ccr_score = None
    if ccr is not None:
        if ccr >= 1.2:
            ccr_score = 100.0
        elif ccr >= 0.9:
            ccr_score = 80.0 + ((ccr - 0.9) / 0.3) * 20.0
        elif ccr >= 0.5:
            ccr_score = 55.0 + ((ccr - 0.5) / 0.4) * 25.0
        elif ccr >= 0.0:
            ccr_score = 20.0 + (ccr / 0.5) * 35.0
        else:
            ccr_score = 0.0

    # Incorporate FCF generation quality
    if ccr_score is None and fcf is not None and sales is not None:
        s = _to_float(sales)
        if s and s > 0:
            fcf_margin = (fcf / s) * 100.0
            if fcf_margin >= 15.0:
                ccr_score = 90.0
            elif fcf_margin >= 8.0:
                ccr_score = 75.0
            elif fcf_margin >= 0.0:
                ccr_score = 50.0
            else:
                ccr_score = 15.0

    if turnover_score is not None and ccr_score is not None:
        norm_eff = 0.6 * turnover_score + 0.4 * ccr_score
    elif turnover_score is not None:
        norm_eff = turnover_score
    elif ccr_score is not None:
        norm_eff = ccr_score
    else:
        norm_eff = 50.0

    norm_eff = min(100.0, max(0.0, norm_eff))
    pillar_score = round(norm_eff * WEIGHTS['efficiency'], 2)
    return pillar_score, round(norm_eff, 2)


def compute_growth_score(sales_cagr, profit_cagr=None, latest_eps=None, prev_eps=None):
    """
    Pillar 4: Growth (Weight: 15%)
    Positive EPS and Revenue compounding.
    Returns: (pillar_score_0_to_15, normalized_pct_0_to_100)
    """
    cagr = _to_float(sales_cagr)
    prof_cagr = _to_float(profit_cagr)
    eps = _to_float(latest_eps)
    p_eps = _to_float(prev_eps)

    revenue_score = None
    if cagr is not None:
        if cagr >= 20.0:
            revenue_score = 100.0
        elif cagr >= 12.0:
            revenue_score = 80.0 + ((cagr - 12.0) / 8.0) * 20.0
        elif cagr >= 7.0:
            revenue_score = 55.0 + ((cagr - 7.0) / 5.0) * 25.0
        elif cagr >= 2.0:
            revenue_score = 30.0 + ((cagr - 2.0) / 5.0) * 25.0
        elif cagr >= 0.0:
            revenue_score = 15.0 + (cagr / 2.0) * 15.0
        else:
            revenue_score = max(0.0, 15.0 + cagr)

    eps_score = None
    if eps is not None:
        if eps <= 0:
            eps_score = 0.0  # Loss-making penalty
        else:
            base_eps_score = 50.0
            if prof_cagr is not None:
                if prof_cagr >= 20.0:
                    eps_score = 100.0
                elif prof_cagr >= 10.0:
                    eps_score = 75.0 + ((prof_cagr - 10.0) / 10.0) * 25.0
                elif prof_cagr >= 0.0:
                    eps_score = 45.0 + (prof_cagr / 10.0) * 30.0
                else:
                    eps_score = max(10.0, 45.0 + prof_cagr)
            elif p_eps is not None and p_eps > 0:
                eps_growth = ((eps - p_eps) / p_eps) * 100.0
                if eps_growth >= 20.0:
                    eps_score = 95.0
                elif eps_growth >= 10.0:
                    eps_score = 75.0
                elif eps_growth >= 0.0:
                    eps_score = 55.0
                else:
                    eps_score = max(10.0, 50.0 + eps_growth)
            else:
                eps_score = base_eps_score

    if revenue_score is not None and eps_score is not None:
        norm_growth = 0.5 * revenue_score + 0.5 * eps_score
    elif revenue_score is not None:
        norm_growth = revenue_score
    elif eps_score is not None:
        norm_growth = eps_score
    else:
        norm_growth = 50.0

    norm_growth = min(100.0, max(0.0, norm_growth))
    pillar_score = round(norm_growth * WEIGHTS['growth'], 2)
    return pillar_score, round(norm_growth, 2)


def calculate_company_health(symbol, save_to_db=True, create_snapshot=False):
    """
    Fetches the latest financial ratios and fundamental data from the database
    for a given company symbol, calculates weighted health score across 4 pillars,
    assigns the qualitative health label, and updates FactMlScores.

    Args:
        symbol (str|DimCompany|DimCompanies): Company symbol identifier (e.g. 'TCS', 'RELIANCE').
        save_to_db (bool): Whether to persist the calculated scores to FactMlScores table.
        create_snapshot (bool): If True, inserts a new timestamped row; otherwise updates latest.

    Returns:
        dict: Detailed result including overall_score, health_label, pillar_scores, and metrics.
    """
    # 1. Normalize symbol string
    if hasattr(symbol, 'symbol'):
        symbol_str = str(symbol.symbol).strip().upper()
    elif hasattr(symbol, 'symbol_id'):
        symbol_str = str(symbol.symbol_id).strip().upper()
    else:
        symbol_str = str(symbol).strip().upper()

    # 2. Retrieve company dimension for sector information
    company = DimCompany.objects.filter(symbol_id=symbol_str).first()
    sector = company.sector if company else None

    # 3. Fetch latest financial analysis (FactAnalysis)
    # Prefer TTM or 3Y / latest period
    analysis = (
        FactAnalysis.objects.filter(symbol_id=symbol_str)
        .order_by('-period_label')
        .first()
    )

    # 4. Fetch latest balance sheet (FactBalanceSheet)
    bs = (
        FactBalanceSheet.objects.filter(symbol_id=symbol_str)
        .order_by('-year_id')
        .first()
    )

    # 5. Fetch recent profit and loss statements (FactProfitLoss)
    pl_records = list(
        FactProfitLoss.objects.filter(symbol_id=symbol_str)
        .order_by('-year_id')[:3]
    )
    latest_pl = pl_records[0] if pl_records else None
    prev_pl = pl_records[1] if len(pl_records) > 1 else None

    # 6. Fetch latest cash flow (FactCashFlow)
    cf = (
        FactCashFlow.objects.filter(symbol_id=symbol_str)
        .order_by('-year_id')
        .first()
    )

    # Extract raw metrics
    roe = analysis.roe_pct if analysis else None
    sales_cagr = analysis.compounded_sales_growth_pct if analysis else None
    profit_cagr = analysis.compounded_profit_growth_pct if analysis else None

    opm = latest_pl.opm_pct if latest_pl else None
    roa = latest_pl.return_on_assets if latest_pl else None
    asset_turnover = latest_pl.asset_turnover if latest_pl else None
    latest_eps = latest_pl.eps if latest_pl else None
    prev_eps = prev_pl.eps if prev_pl else None
    sales = latest_pl.sales if latest_pl else None
    int_cov = latest_pl.interest_coverage if latest_pl else None

    debt_to_equity = bs.debt_to_equity if bs else None
    equity_ratio = bs.equity_ratio if bs else None

    ccr = cf.cash_conversion_ratio if cf else None
    fcf = cf.free_cash_flow if cf else None

    # Calculate 4 Pillars
    prof_score, prof_norm = compute_profitability_score(roe_pct=roe, opm_pct=opm, roa_pct=roa)
    solv_score, solv_norm = compute_solvency_score(
        debt_to_equity=debt_to_equity,
        sector=sector,
        equity_ratio=equity_ratio,
        interest_coverage=int_cov
    )
    eff_score, eff_norm = compute_efficiency_score(
        asset_turnover=asset_turnover,
        cash_conversion_ratio=ccr,
        free_cash_flow=fcf,
        sales=sales
    )
    growth_score, growth_norm = compute_growth_score(
        sales_cagr=sales_cagr,
        profit_cagr=profit_cagr,
        latest_eps=latest_eps,
        prev_eps=prev_eps
    )

    # Overall Score (0 to 100)
    overall_score = round(prof_score + solv_score + eff_score + growth_score, 2)
    overall_score = min(100.0, max(0.0, overall_score))
    health_label = assign_health_label(overall_score)

    # Capture existing score prior to save for webhook change detection
    existing = FactMlScores.objects.filter(symbol_id=symbol_str).order_by('-computed_at').first()
    previous_score = _to_float(existing.overall_score) if existing else None

    score_instance = None
    webhooks_dispatched = 0
    if save_to_db:
        score_instance = _save_health_score(
            symbol_str=symbol_str,
            overall_score=overall_score,
            prof_score=prof_score,
            growth_score=growth_score,
            solv_score=solv_score,
            eff_score=eff_score,
            health_label=health_label,
            create_snapshot=create_snapshot
        )

        # Webhook Notification Check:
        # Trigger if it is a new snapshot or if score changed significantly (>= 2.0 points per PRD)
        is_new = (existing is None) or create_snapshot
        score_diff = abs(overall_score - previous_score) if previous_score is not None else None
        changed_significantly = (score_diff is not None and score_diff >= 2.0)

        if is_new or changed_significantly:
            webhooks_dispatched = trigger_score_updated_webhooks(
                symbol_str=symbol_str,
                company=company,
                overall_score=overall_score,
                health_label=health_label,
                previous_score=previous_score,
                prof_score=prof_score,
                solv_score=solv_score,
                eff_score=eff_score,
                growth_score=growth_score
            )

    result = {
        'symbol': symbol_str,
        'overall_score': overall_score,
        'health_label': health_label,
        'webhooks_dispatched': webhooks_dispatched,
        'pillar_scores': {
            'profitability': prof_score,   # 0 to 35
            'solvency': solv_score,         # 0 to 35
            'efficiency': eff_score,       # 0 to 15
            'growth': growth_score,         # 0 to 15
        },
        'normalized_pillars': {
            'profitability_pct': prof_norm,  # 0 to 100
            'solvency_pct': solv_norm,       # 0 to 100
            'efficiency_pct': eff_norm,     # 0 to 100
            'growth_pct': growth_norm,       # 0 to 100
        },
        'metrics_used': {
            'roe_pct': _to_float(roe),
            'opm_pct': _to_float(opm),
            'debt_to_equity': _to_float(debt_to_equity),
            'asset_turnover': _to_float(asset_turnover),
            'sales_cagr_pct': _to_float(sales_cagr),
            'latest_eps': _to_float(latest_eps),
        },
        'score_instance': score_instance,
    }

    return result


def _save_health_score(symbol_str, overall_score, prof_score, growth_score, solv_score, eff_score, health_label, create_snapshot=False):
    """
    Persists or updates the calculated health score record in the FactMlScores table.
    """
    now = timezone.now()
    dim_company = DimCompanies.objects.filter(symbol=symbol_str).first()
    if not dim_company:
        # Guarantee foreign key integrity if record exists in DimCompany
        dim_comp_record = DimCompany.objects.filter(symbol_id=symbol_str).first()
        if dim_comp_record:
            dim_company = dim_comp_record.symbol

    defaults = {
        'overall_score': Decimal(str(overall_score)),
        'profitability_score': Decimal(str(prof_score)),
        'growth_score': Decimal(str(growth_score)),
        'leverage_score': Decimal(str(solv_score)),
        'cashflow_score': Decimal(str(eff_score)),
        'trend_score': Decimal(str(growth_score)),
        'health_label': health_label,
        'computed_at': now,
    }

    if not create_snapshot:
        # Permanent fix: use update_or_create when create_snapshot=False
        existing = FactMlScores.objects.filter(symbol_id=symbol_str).order_by('-computed_at').first()
        lookup_computed_at = existing.computed_at if existing else now

        obj, _ = FactMlScores.objects.update_or_create(
            symbol_id=symbol_str,
            computed_at=lookup_computed_at,
            defaults=defaults
        )
        return obj
    else:
        # Create new snapshot record
        return FactMlScores.objects.create(
            symbol_id=symbol_str,
            computed_at=now,
            overall_score=Decimal(str(overall_score)),
            profitability_score=Decimal(str(prof_score)),
            growth_score=Decimal(str(growth_score)),
            leverage_score=Decimal(str(solv_score)),
            cashflow_score=Decimal(str(eff_score)),
            trend_score=Decimal(str(growth_score)),
            health_label=health_label,
        )


def trigger_score_updated_webhooks(symbol_str, company, overall_score, health_label, previous_score, prof_score, solv_score, eff_score, growth_score):
    """
    Checks for active WebhookSubscription records for 'score_updated'.
    For each subscription, creates a WebhookEvent and triggers deliver_webhook_task.delay(event_id).
    """
    try:
        from api.models import WebhookSubscription, WebhookEvent
        subscriptions = WebhookSubscription.objects.filter(
            event_type=WebhookSubscription.EVENT_SCORE_UPDATED,
            is_active=True
        ).select_related('partner')

        if not subscriptions.exists():
            return 0

        score_change = round(float(overall_score - previous_score), 2) if previous_score is not None else None

        payload = {
            "event": "score_updated",
            "timestamp": timezone.now().isoformat(),
            "company": {
                "symbol": symbol_str,
                "company_name": company.company_name if company else symbol_str,
                "sector": company.sector if company else None,
            },
            "data": {
                "overall_score": float(overall_score),
                "health_label": health_label,
                "previous_score": float(previous_score) if previous_score is not None else None,
                "score_change": score_change,
                "pillar_scores": {
                    "profitability": float(prof_score),
                    "solvency": float(solv_score),
                    "efficiency": float(eff_score),
                    "growth": float(growth_score),
                }
            }
        }

        created_count = 0
        for sub in subscriptions:
            event = WebhookEvent.objects.create(
                subscription=sub,
                payload=payload,
                status=WebhookEvent.STATUS_PENDING,
                retry_count=0
            )
            created_count += 1
            try:
                from api.tasks import deliver_webhook_event, deliver_webhook_task
                deliver_webhook_event.delay(event.pk)
            except Exception as task_exc:
                logger.warning(f"Could not queue deliver_webhook_task for event #{event.pk}: {task_exc}")

        logger.info(f"Triggered {created_count} webhooks for {symbol_str} score update ({overall_score}).")
        return created_count

    except Exception as exc:
        logger.error(f"Error triggering score_updated webhooks for {symbol_str}: {exc}", exc_info=True)
        return 0


