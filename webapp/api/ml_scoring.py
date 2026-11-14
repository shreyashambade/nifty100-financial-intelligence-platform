"""
ml_scoring.py

Data science and machine learning service utility for NiftyIQ:
1. Dynamic ML Health Scoring (0-100) using scikit-learn (StandardScaler)
   across multi-dimensional financial fundamental pillars.
2. Statistical Z-Score & Isolation Forest Anomaly Detection flagging
   financial metric deviations outside 3 standard deviations.
"""

import os
import logging
import numpy as np
import pandas as pd
from django.utils import timezone
from django.db import connection, transaction
from django.conf import settings

from .models import (
    DimCompany,
    DimCompanies,
    FactProfitLoss,
    FactBalanceSheet,
    FactCashFlow,
    FactAnalysis,
    FactMlScores,
    FactAnomalyFlag,
)

logger = logging.getLogger(__name__)

# Dimension weights as per PRD Section 7.1 (Total: 100)
DIMENSION_WEIGHTS = {
    'profitability': 25.0,
    'growth': 20.0,
    'leverage': 20.0,
    'cashflow': 15.0,
    'dividend': 10.0,
    'trend': 10.0,
}

# Sectors where standard leverage and cashflow metrics are structurally misleading
FINANCIAL_SECTORS = {'Banking', 'NBFC', 'Insurance', 'Financial Services'}


def _resolve_data_path(*subpaths):
    """Resolve file path across project root or webapp directory candidates."""
    base_dir = getattr(settings, 'BASE_DIR', None)
    candidates = []
    if base_dir:
        candidates.append(os.path.join(str(base_dir), '..', *subpaths))
        candidates.append(os.path.join(str(base_dir), *subpaths))
    candidates.append(os.path.abspath(os.path.join(os.path.dirname(__file__), '..', '..', *subpaths)))
    candidates.append(os.path.abspath(os.path.join(os.path.dirname(__file__), '..', *subpaths)))
    for p in candidates:
        if os.path.exists(p):
            return p
    return candidates[0] if candidates else os.path.join(*subpaths)


def assign_health_label(score):
    """Maps 0-100 score to qualitative PRD health label."""
    if score is None or pd.isna(score):
        return 'AVERAGE'
    score_f = float(score)
    if score_f >= 85.0:
        return 'EXCELLENT'
    elif score_f >= 70.0:
        return 'GOOD'
    elif score_f >= 50.0:
        return 'AVERAGE'
    elif score_f >= 35.0:
        return 'WEAK'
    else:
        return 'POOR'


def ensure_anomaly_table():
    """Ensure the fact_anomaly_flags table exists in database."""
    try:
        with connection.cursor() as cursor:
            is_sqlite = (connection.vendor == 'sqlite')
            if is_sqlite:
                cursor.execute("""
                    CREATE TABLE IF NOT EXISTS fact_anomaly_flags (
                        id INTEGER PRIMARY KEY AUTOINCREMENT,
                        symbol VARCHAR(50),
                        year_label VARCHAR(50),
                        metric_name VARCHAR(100),
                        metric_value NUMERIC,
                        z_score NUMERIC,
                        severity VARCHAR(20) DEFAULT 'HIGH',
                        is_multivariate_anomaly BOOLEAN DEFAULT 0,
                        details TEXT,
                        flagged_at DATETIME,
                        is_reviewed BOOLEAN DEFAULT 0
                    );
                """)
            else:
                cursor.execute("""
                    CREATE TABLE IF NOT EXISTS fact_anomaly_flags (
                        id SERIAL PRIMARY KEY,
                        symbol VARCHAR(50),
                        year_label VARCHAR(50),
                        metric_name VARCHAR(100),
                        metric_value NUMERIC,
                        z_score NUMERIC,
                        severity VARCHAR(20) DEFAULT 'HIGH',
                        is_multivariate_anomaly BOOLEAN DEFAULT FALSE,
                        details TEXT,
                        flagged_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
                        is_reviewed BOOLEAN DEFAULT FALSE
                    );
                """)
    except Exception as exc:
        logger.warning(f"ensure_anomaly_table execution: {exc}")


def calculate_company_health_scores(dry_run=False):
    """
    Computes a 0-100 financial health score for every company using scikit-learn
    StandardScaler across 6 weighted fundamental dimensions:
      - Profitability (25%): OPM %, Net Profit Margin %
      - Revenue Growth (20%): 3-Year Sales CAGR %
      - Leverage / Solvency (20%): Debt-to-Equity (inverted)
      - Cash Flow Quality (15%): Cash Conversion Ratio
      - Dividend Consistency (10%): Dividend payout track record
      - Growth Trend (10%): Multi-year sales regression slope
    
    Inserts newly calculated scores into FactMlScores with current timestamp.
    Fires webhook notifications for companies whose scores shifted >= 2.0 points.
    """
    logger.info("Starting ML Health Scoring calculation across Nifty 100 universe...")
    
    # 1. Fetch companies
    companies = list(DimCompany.objects.all().select_related('symbol'))
    use_csv_fallback = False
    
    if not companies:
        logger.info("No DimCompany rows in database, loading clean CSV files for scoring...")
        use_csv_fallback = True

    # Prepare data containers
    symbols = []
    company_sectors = {}
    company_obj_map = {}

    if not use_csv_fallback:
        for c in companies:
            sym = str(getattr(c, 'symbol_id', None) or c.symbol)
            symbols.append(sym)
            company_sectors[sym] = c.sector or 'Unknown'
            company_obj_map[sym] = c
    else:
        # Load from clean CSV
        p_comp = _resolve_data_path('data', 'clean', 'clean_companies.csv')
        if os.path.exists(p_comp):
            df_comp = pd.read_csv(p_comp)
            for _, row in df_comp.iterrows():
                sym = str(row['symbol']).strip()
                symbols.append(sym)
                company_sectors[sym] = row.get('sector', 'Unknown')
        else:
            symbols = ['TCS', 'RELIANCE', 'INFY', 'HDFCBANK', 'ICICIBANK']

        # Enrich sector from sector_mapping.csv if available
        p_sec = _resolve_data_path('data', 'raw', 'sector_mapping.csv')
        if os.path.exists(p_sec):
            try:
                df_sec = pd.read_csv(p_sec)
                for _, r in df_sec.iterrows():
                    s_sym = str(r.get('symbol', '')).strip()
                    s_val = str(r.get('sector', '')).strip()
                    if s_sym and s_val:
                        company_sectors[s_sym] = s_val
            except Exception as e_sec:
                logger.warning(f"Error loading sector_mapping.csv: {e_sec}")

    symbols = sorted(list(set(symbols)))
    if not symbols:
        return {"status": "error", "message": "No company symbols found to score."}

    # 2. Gather raw metrics
    pl_data_by_sym = {s: [] for s in symbols}
    bs_data_by_sym = {s: [] for s in symbols}
    cf_data_by_sym = {s: [] for s in symbols}

    if not use_csv_fallback:
        try:
            for pl in FactProfitLoss.objects.filter(symbol__symbol__in=symbols).order_by('sort_order', 'year_id'):
                sym = str(pl.symbol_id)
                if sym in pl_data_by_sym:
                    pl_data_by_sym[sym].append(pl)
        except Exception as e:
            logger.warning(f"Error querying FactProfitLoss: {e}")

        try:
            for bs in FactBalanceSheet.objects.filter(symbol__symbol__in=symbols).order_by('sort_order', 'year_id'):
                sym = str(bs.symbol_id)
                if sym in bs_data_by_sym:
                    bs_data_by_sym[sym].append(bs)
        except Exception as e:
            logger.warning(f"Error querying FactBalanceSheet: {e}")

        try:
            for cf in FactCashFlow.objects.filter(symbol__symbol__in=symbols).order_by('sort_order', 'year_id'):
                sym = str(cf.symbol_id)
                if sym in cf_data_by_sym:
                    cf_data_by_sym[sym].append(cf)
        except Exception as e:
            logger.warning(f"Error querying FactCashFlow: {e}")

    # Fallback to CSV files if DB lists are empty
    p_pl = _resolve_data_path('data', 'clean', 'clean_profit_loss.csv')
    p_bs = _resolve_data_path('data', 'clean', 'clean_balance_sheet.csv')
    p_cf = _resolve_data_path('data', 'clean', 'clean_cash_flow.csv')

    has_db_data = any(len(v) > 0 for v in pl_data_by_sym.values())
    if not has_db_data and os.path.exists(p_pl) and os.path.exists(p_bs) and os.path.exists(p_cf):
        logger.info("Populating metrics from clean CSV files...")
        df_pl_all = pd.read_csv(p_pl)
        df_bs_all = pd.read_csv(p_bs)
        df_cf_all = pd.read_csv(p_cf)

        # Exclude TTM
        df_pl_all = df_pl_all[df_pl_all['year_label'] != 'TTM']
        df_bs_all = df_bs_all[df_bs_all['year_label'] != 'TTM']
        df_cf_all = df_cf_all[df_cf_all['year_label'] != 'TTM']

        for sym in symbols:
            pl_rows = df_pl_all[df_pl_all['symbol'] == sym].sort_values('year_id').to_dict('records')
            bs_rows = df_bs_all[df_bs_all['symbol'] == sym].sort_values('year_id').to_dict('records')
            cf_rows = df_cf_all[df_cf_all['symbol'] == sym].sort_values('year_id').to_dict('records')
            pl_data_by_sym[sym] = pl_rows
            bs_data_by_sym[sym] = bs_rows
            cf_data_by_sym[sym] = cf_rows

    # 3. Engineer feature vectors for all companies
    feature_rows = []
    def _val(obj, attr, default=0.0):
        if isinstance(obj, dict):
            v = obj.get(attr)
        else:
            v = getattr(obj, attr, None)
        try:
            return float(v) if v is not None and not pd.isna(v) else default
        except (ValueError, TypeError):
            return default

    for sym in symbols:
        pl_list = pl_data_by_sym.get(sym, [])
        bs_list = bs_data_by_sym.get(sym, [])
        cf_list = cf_data_by_sym.get(sym, [])
        sector = company_sectors.get(sym, '')
        is_financial = any(fin.lower() in sector.lower() for fin in FINANCIAL_SECTORS)

        # A. Profitability: Avg OPM% & NPM%
        if pl_list:
            opms = [_val(p, 'opm_pct') for p in pl_list]
            npms = [_val(p, 'net_profit_margin_pct') for p in pl_list]
            prof_raw = (np.mean(opms) + np.mean(npms)) / 2.0
        else:
            prof_raw = np.nan

        # B. Growth: 3-Year Sales CAGR
        growth_raw = np.nan
        if len(pl_list) >= 4:
            s_now = _val(pl_list[-1], 'sales')
            s_past = _val(pl_list[-4], 'sales')
            if s_past > 0 and s_now > 0:
                growth_raw = ((s_now / s_past) ** (1.0 / 3.0) - 1.0) * 100.0
        elif len(pl_list) >= 2:
            s_now = _val(pl_list[-1], 'sales')
            s_past = _val(pl_list[-2], 'sales')
            if s_past > 0:
                growth_raw = ((s_now - s_past) / s_past) * 100.0

        # C. Leverage: Latest Debt to Equity
        if is_financial:
            lev_raw = np.nan  # Excluded for financial firms
        elif bs_list:
            latest_bs = bs_list[-1]
            lev_raw = _val(latest_bs, 'debt_to_equity', default=0.5)
        else:
            lev_raw = np.nan

        # D. Cash Flow: Avg Cash Conversion Ratio
        if is_financial:
            cf_raw = np.nan  # Excluded for financial firms
        elif cf_list:
            ccrs = [_val(c, 'cash_conversion_ratio', default=np.nan) for c in cf_list]
            valid_ccrs = [x for x in ccrs if not np.isnan(x)]
            cf_raw = np.mean(valid_ccrs) if valid_ccrs else np.nan
        else:
            cf_raw = np.nan

        # E. Dividend: 5-year payout consistency & avg payout
        if pl_list:
            last5 = pl_list[-5:]
            payouts = [_val(p, 'dividend_payout_pct') for p in last5]
            pos_years = sum(1 for p in payouts if p > 0)
            consistency = pos_years / len(last5) if last5 else 0.0
            avg_payout = np.mean(payouts) if payouts else 0.0
            strength = min(avg_payout / 30.0, 1.0) if avg_payout > 0 else 0.0
            div_raw = consistency * strength
        else:
            div_raw = np.nan

        # F. Growth Trend: Linear regression slope on sales (last 5 years)
        if pl_list and len(pl_list) >= 3:
            sales_pts = [_val(p, 'sales') for p in pl_list[-5:]]
            if len(sales_pts) >= 3 and np.mean(sales_pts) > 0:
                x_axis = np.arange(len(sales_pts))
                slope, _ = np.polyfit(x_axis, sales_pts, 1)
                trend_raw = slope / np.mean(sales_pts)
            else:
                trend_raw = np.nan
        else:
            trend_raw = np.nan

        feature_rows.append({
            'symbol': sym,
            'is_financial': is_financial,
            'prof_raw': prof_raw,
            'growth_raw': growth_raw,
            'lev_raw': lev_raw,
            'cf_raw': cf_raw,
            'div_raw': div_raw,
            'trend_raw': trend_raw,
        })

    df_feats = pd.DataFrame(feature_rows)

    # 4. Apply StandardScaler from scikit-learn
    metric_cols = ['prof_raw', 'growth_raw', 'lev_raw', 'cf_raw', 'div_raw', 'trend_raw']
    
    # Impute missing values temporarily with median for scaling
    imputed_matrix = np.zeros((len(df_feats), len(metric_cols)))
    for j, col in enumerate(metric_cols):
        series = df_feats[col].copy()
        med = series.median() if not series.dropna().empty else 0.0
        imputed_matrix[:, j] = series.fillna(med).values

    try:
        from sklearn.preprocessing import StandardScaler
        scaler = StandardScaler()
        scaled_matrix = scaler.fit_transform(imputed_matrix)
    except Exception as exc:
        logger.info(f"Using pure NumPy StandardScaler fallback: {exc}")
        means = np.mean(imputed_matrix, axis=0)
        stds = np.std(imputed_matrix, axis=0)
        stds[stds == 0] = 1.0
        scaled_matrix = (imputed_matrix - means) / stds

    # 5. Convert Scaled Z-Scores to 0-100 Sub-Scores
    for j, col in enumerate(metric_cols):
        z = scaled_matrix[:, j]
        # Bounded sigmoid/clipping mapping [-2.5, +2.5] -> [0, 100]
        mapped = np.clip((z + 2.5) / 5.0 * 100.0, 0.0, 100.0)
        score_col = col.replace('_raw', '_score')
        
        if col == 'lev_raw':
            # For leverage: lower debt is better, invert score
            df_feats[score_col] = 100.0 - mapped
            # Preserve NaN for financial firms
            df_feats.loc[df_feats['is_financial'], score_col] = np.nan
        elif col == 'cf_raw':
            df_feats[score_col] = mapped
            # Preserve NaN for financial firms
            df_feats.loc[df_feats['is_financial'], score_col] = np.nan
        else:
            df_feats[score_col] = mapped

    # 6. Calculate Weighted Overall Health Score (0-100)
    def compute_composite_score(row):
        earned = 0.0
        available_weight = 0.0
        
        mapping = {
            'prof_score': 'profitability',
            'growth_score': 'growth',
            'lev_score': 'leverage',
            'cf_score': 'cashflow',
            'div_score': 'dividend',
            'trend_score': 'trend',
        }
        
        for score_field, dim_name in mapping.items():
            val = row[score_field]
            if pd.notna(val):
                earned += float(val) * DIMENSION_WEIGHTS[dim_name]
                available_weight += DIMENSION_WEIGHTS[dim_name]
                
        if available_weight > 0:
            return round((earned / available_weight), 2)
        return 50.0

    df_feats['overall_score'] = df_feats.apply(compute_composite_score, axis=1)
    df_feats['health_label'] = df_feats['overall_score'].apply(assign_health_label)

    # 7. Update Database (FactMlScores) & Webhook Event Triggering
    now = timezone.now()
    updated_records = []
    score_shifts_detected = []

    # Local import to avoid circular dependencies
    try:
        from .tasks import trigger_webhook_events
    except Exception:
        trigger_webhook_events = None

    if not dry_run:
        # Load latest previous scores to detect >= 2.0 shifts
        prev_scores = {}
        try:
            for s in FactMlScores.objects.filter(symbol__symbol__in=symbols).order_by('symbol_id', '-computed_at'):
                if s.symbol_id not in prev_scores:
                    prev_scores[s.symbol_id] = float(s.overall_score or 0)
        except Exception as e:
            logger.warning(f"Error fetching previous FactMlScores: {e}")

        for _, row in df_feats.iterrows():
            sym = row['symbol']
            overall = float(row['overall_score'])
            label = row['health_label']
            
            # Fetch or resolve DimCompanies foreign key instance
            dim_comp_inst = None
            try:
                dim_comp_inst = DimCompanies.objects.filter(symbol__iexact=sym).first()
                if not dim_comp_inst:
                    dim_comp_inst = DimCompanies.objects.create(symbol=sym)
            except Exception:
                dim_comp_inst = None

            if dim_comp_inst:
                try:
                    record = FactMlScores.objects.create(
                        symbol=dim_comp_inst,
                        computed_at=now,
                        overall_score=overall,
                        profitability_score=round(float(row.get('prof_score', 0)), 2) if pd.notna(row.get('prof_score')) else None,
                        growth_score=round(float(row.get('growth_score', 0)), 2) if pd.notna(row.get('growth_score')) else None,
                        leverage_score=round(float(row.get('lev_score', 0)), 2) if pd.notna(row.get('lev_score')) else None,
                        cashflow_score=round(float(row.get('cf_score', 0)), 2) if pd.notna(row.get('cf_score')) else None,
                        dividend_score=round(float(row.get('div_score', 0)), 2) if pd.notna(row.get('div_score')) else None,
                        trend_score=round(float(row.get('trend_score', 0)), 2) if pd.notna(row.get('trend_score')) else None,
                        health_label=label
                    )
                    updated_records.append(record)
                except Exception as exc:
                    logger.warning(f"Could not insert FactMlScores row for {sym}: {exc}")

            # Check for score shift >= 2.0 points to dispatch webhook
            prev_val = prev_scores.get(sym)
            if prev_val is not None:
                diff = round(overall - prev_val, 2)
                if abs(diff) >= 2.0:
                    score_shifts_detected.append({'symbol': sym, 'prev': prev_val, 'new': overall, 'diff': diff})
                    if trigger_webhook_events:
                        try:
                            trigger_webhook_events(
                                event_type='score_updated',
                                symbol=sym,
                                data_payload={
                                    'previous_score': prev_val,
                                    'new_score': overall,
                                    'score_change': diff,
                                    'health_label': label,
                                    'computed_at': now.isoformat(),
                                }
                            )
                        except Exception as w_err:
                            logger.warning(f"Could not trigger score_updated webhook for {sym}: {w_err}")

    logger.info(
        f"Completed ML scoring: {len(df_feats)} companies scored. "
        f"{len(score_shifts_detected)} significant score shifts triggered."
    )

    return {
        "status": "success",
        "scored_count": len(df_feats),
        "db_records_created": len(updated_records),
        "score_shifts_flagged": len(score_shifts_detected),
        "timestamp": now.isoformat(),
        "sample": df_feats[['symbol', 'overall_score', 'health_label']].head(5).to_dict('records')
    }


def detect_financial_anomalies(threshold_sigma=3.0, dry_run=False):
    """
    Applies statistical Z-Score and Isolation Forest anomaly detection
    across key financial metrics (sales, net_profit, operating_profit, borrowings)
    across the Nifty 100 universe.
    
    Identifies metrics that fall outside of 3 standard deviations (|Z| >= 3.0),
    logs anomaly records to the database (fact_anomaly_flags), exports them to CSV,
    and dispatches webhook notifications for newly flagged anomalies.
    """
    logger.info(f"Running Z-Score Anomaly Detection (Threshold: {threshold_sigma} sigma)...")
    ensure_anomaly_table()

    # 1. Load data
    metrics = ['sales', 'net_profit', 'operating_profit', 'borrowings']
    data_rows = []

    # Attempt database pull
    try:
        pl_qs = FactProfitLoss.objects.all().values('symbol_id', 'year_label', 'sales', 'net_profit', 'operating_profit')
        bs_map = {}
        for b in FactBalanceSheet.objects.all().values('symbol_id', 'year_label', 'borrowings'):
            bs_map[(b['symbol_id'], b['year_label'])] = float(b['borrowings'] or 0)
        
        for p in pl_qs:
            sym = p['symbol_id']
            yr = p['year_label']
            borrow = bs_map.get((sym, yr), 0.0)
            data_rows.append({
                'symbol': sym,
                'year_label': yr,
                'sales': float(p['sales'] or 0),
                'net_profit': float(p['net_profit'] or 0),
                'operating_profit': float(p['operating_profit'] or 0),
                'borrowings': borrow,
            })
    except Exception as e:
        logger.warning(f"Database query in anomaly detection: {e}")

    # Fallback to CSV if DB had no rows
    if not data_rows:
        p_pl = _resolve_data_path('data', 'clean', 'clean_profit_loss.csv')
        p_bs = _resolve_data_path('data', 'clean', 'clean_balance_sheet.csv')
        if os.path.exists(p_pl) and os.path.exists(p_bs):
            pl_df = pd.read_csv(p_pl)
            bs_df = pd.read_csv(p_bs)
            merged = pd.merge(pl_df, bs_df[['symbol', 'year_id', 'borrowings']], on=['symbol', 'year_id'])
            merged = merged[merged['year_label'] != 'TTM']
            for _, r in merged.iterrows():
                data_rows.append({
                    'symbol': str(r['symbol']),
                    'year_label': str(r['year_label']),
                    'sales': float(r.get('sales') or 0),
                    'net_profit': float(r.get('net_profit') or 0),
                    'operating_profit': float(r.get('operating_profit') or 0),
                    'borrowings': float(r.get('borrowings') or 0),
                })

    if not data_rows:
        return {"status": "error", "message": "No financial records available for anomaly detection."}

    df = pd.DataFrame(data_rows)
    df = df[df['year_label'] != 'TTM'].copy()

    # 2. Compute Statistical Z-Scores
    flagged_anomalies = []
    
    # Try scipy.stats.zscore or standard NumPy formula
    for m in metrics:
        vals = df[m].astype(float).values
        std_val = np.std(vals)
        mean_val = np.mean(vals)
        if std_val > 0:
            z_scores = (vals - mean_val) / std_val
        else:
            z_scores = np.zeros_like(vals)
        df[f'{m}_zscore'] = z_scores

    # Also compute within-company time-series Z-scores for extreme YoY shocks
    for m in metrics:
        def _calc_grp_z(group):
            v = group[m].values
            s = np.std(v)
            m_val = np.mean(v)
            if s > 0 and len(v) >= 3:
                return (v - m_val) / s
            return np.zeros_like(v)
            
        try:
            df[f'{m}_ts_zscore'] = df.groupby('symbol', group_keys=False).apply(_calc_grp_z)
        except Exception:
            df[f'{m}_ts_zscore'] = 0.0

    # 3. Multivariate Anomaly Detection using Isolation Forest
    is_multivariate = np.zeros(len(df), dtype=bool)
    try:
        from sklearn.ensemble import IsolationForest
        iso_features = df[metrics].fillna(0)
        iso = IsolationForest(contamination=0.05, random_state=42)
        outliers = iso.fit_predict(iso_features)
        is_multivariate = (outliers == -1)
    except Exception as exc:
        logger.info(f"IsolationForest not applied (using statistical z-score bounds): {exc}")

    df['is_multivariate_anomaly'] = is_multivariate

    # 4. Identify Outliers Outside 3 Standard Deviations
    now = timezone.now()
    seen_flags = set()

    for idx, row in df.iterrows():
        sym = row['symbol']
        yr = row['year_label']
        is_multi = bool(row['is_multivariate_anomaly'])

        for m in metrics:
            z_val = float(row.get(f'{m}_zscore', 0.0))
            ts_z_val = float(row.get(f'{m}_ts_zscore', 0.0))
            raw_val = float(row[m])

            # Check cross-sectional or time-series deviation outside 3 sigma
            is_anomaly = (abs(z_val) >= threshold_sigma) or (abs(ts_z_val) >= threshold_sigma) or (is_multi and abs(z_val) >= 2.0)

            if is_anomaly:
                key = (sym, yr, m)
                if key not in seen_flags:
                    seen_flags.add(key)
                    max_z = z_val if abs(z_val) >= abs(ts_z_val) else ts_z_val
                    severity = 'HIGH' if abs(max_z) >= 3.5 else 'MEDIUM'
                    desc = f"Extreme deviation in {m}: value ₹{raw_val:,.1f} Cr (|Z|={abs(max_z):.2f} >= {threshold_sigma}σ)"
                    
                    flagged_anomalies.append({
                        'symbol': sym,
                        'year_label': yr,
                        'metric_name': m,
                        'metric_value': raw_val,
                        'z_score': round(max_z, 4),
                        'severity': severity,
                        'is_multivariate_anomaly': is_multi,
                        'details': desc,
                        'sales': row['sales'],
                        'net_profit': row['net_profit'],
                        'operating_profit': row['operating_profit'],
                        'borrowings': row['borrowings'],
                    })

    # 5. Log Anomalies into Database
    try:
        from .tasks import trigger_webhook_events
    except Exception:
        trigger_webhook_events = None

    db_logged_count = 0
    if not dry_run:
        for item in flagged_anomalies:
            sym_str = item['symbol']
            
            # ORM insert with fallback to cursor
            try:
                dim_c = DimCompanies.objects.filter(symbol__iexact=sym_str).first()
                FactAnomalyFlag.objects.create(
                    symbol=dim_c,
                    year_label=item['year_label'],
                    metric_name=item['metric_name'],
                    metric_value=item['metric_value'],
                    z_score=item['z_score'],
                    severity=item['severity'],
                    is_multivariate_anomaly=item['is_multivariate_anomaly'],
                    details=item['details'],
                )
                db_logged_count += 1
            except Exception as orm_err:
                # Direct SQL insert fallback
                try:
                    with connection.cursor() as cur:
                        cur.execute("""
                            INSERT INTO fact_anomaly_flags 
                            (symbol, year_label, metric_name, metric_value, z_score, severity, is_multivariate_anomaly, details, flagged_at, is_reviewed)
                            VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s)
                        """, [
                            sym_str, item['year_label'], item['metric_name'],
                            item['metric_value'], item['z_score'], item['severity'],
                            item['is_multivariate_anomaly'], item['details'], now, False
                        ])
                        db_logged_count += 1
                except Exception as sql_err:
                    logger.warning(f"Could not log anomaly to DB for {sym_str}: {sql_err}")

            # Dispatch anomaly_flagged webhook notification
            if trigger_webhook_events:
                try:
                    trigger_webhook_events(
                        event_type='anomaly_flagged',
                        symbol=sym_str,
                        data_payload={
                            'year_label': item['year_label'],
                            'metric': item['metric_name'],
                            'metric_value': item['metric_value'],
                            'z_score': item['z_score'],
                            'severity': item['severity'],
                            'is_multivariate': item['is_multivariate_anomaly'],
                            'details': item['details'],
                            'flagged_at': now.isoformat(),
                        }
                    )
                except Exception as w_err:
                    logger.warning(f"Could not trigger anomaly_flagged webhook for {sym_str}: {w_err}")

        # Update CSV export file
        try:
            csv_path = _resolve_data_path('data', 'fact_anomaly_flags.csv')
            if flagged_anomalies:
                df_out = pd.DataFrame(flagged_anomalies)
                export_cols = ['symbol', 'year_label', 'sales', 'net_profit', 'operating_profit', 'borrowings', 'is_multivariate_anomaly']
                existing_cols = [c for c in export_cols if c in df_out.columns]
                df_out[existing_cols].to_csv(csv_path, index=False)
        except Exception as csv_err:
            logger.warning(f"Could not update fact_anomaly_flags.csv: {csv_err}")

    flagged_symbols = sorted(list(set(a['symbol'] for a in flagged_anomalies)))
    logger.info(
        f"Anomaly detection complete. Flagged {len(flagged_anomalies)} anomalies "
        f"outside {threshold_sigma}σ across {len(flagged_symbols)} companies."
    )

    return {
        "status": "success",
        "threshold_sigma": threshold_sigma,
        "anomalies_detected": len(flagged_anomalies),
        "companies_flagged_count": len(flagged_symbols),
        "db_records_logged": db_logged_count,
        "flagged_companies": flagged_symbols[:15],
    }
