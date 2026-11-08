import time
from django.core.management.base import BaseCommand, CommandError
from django.db import transaction

from api.models import DimCompany, DimCompanies, FactMlScores
from api.services.scoring_engine import calculate_company_health, assign_health_label


class Command(BaseCommand):
    help = 'Run the dynamic ML Health Scoring Engine across the Nifty 100 universe and update FactMlScores.'

    def add_arguments(self, parser):
        parser.add_argument(
            '--symbol',
            type=str,
            default=None,
            help='Run scoring for a specific company symbol only (e.g., RELIANCE, TCS)'
        )
        parser.add_argument(
            '--sector',
            type=str,
            default=None,
            help='Run scoring for companies in a specific sector only (e.g., "Information Technology")'
        )
        parser.add_argument(
            '--snapshot',
            action='store_true',
            help='Create a new timestamped snapshot in FactMlScores rather than updating existing latest score'
        )
        parser.add_argument(
            '--quiet',
            action='store_true',
            help='Suppress per-company output and only display summary metrics'
        )

    def handle(self, *args, **options):
        symbol_arg = options.get('symbol')
        sector_arg = options.get('sector')
        create_snapshot = options.get('snapshot', False)
        quiet = options.get('quiet', False)

        start_time = time.time()

        self.stdout.write("")
        self.stdout.write(self.style.SUCCESS("=" * 75))
        self.stdout.write(self.style.SUCCESS("  🚀 NiftyIQ Phase 3: ML Health Scoring Engine"))
        self.stdout.write(self.style.SUCCESS("=" * 75))

        # Query universe
        queryset = DimCompany.objects.all().order_by('symbol_id')

        if symbol_arg:
            clean_sym = symbol_arg.strip().upper()
            queryset = queryset.filter(symbol__symbol__iexact=clean_sym)
            if not queryset.exists():
                # Check fallback in DimCompanies
                if not DimCompanies.objects.filter(symbol__iexact=clean_sym).exists():
                    raise CommandError(f"Company with symbol '{clean_sym}' not found in database.")
                # Allow scoring even if only in DimCompanies
                symbols_to_score = [clean_sym]
            else:
                symbols_to_score = [c.symbol_id for c in queryset]
        elif sector_arg:
            queryset = queryset.filter(sector__iexact=sector_arg.strip())
            symbols_to_score = [c.symbol_id for c in queryset]
            self.stdout.write(f"  Filtering by Sector: {sector_arg.strip()} ({len(symbols_to_score)} companies)")
        else:
            symbols_to_score = [c.symbol_id for c in queryset]
            if not symbols_to_score:
                # Fallback to DimCompanies if DimCompany has no entries
                symbols_to_score = list(DimCompanies.objects.values_list('symbol', flat=True))

        total_companies = len(symbols_to_score)
        if total_companies == 0:
            self.stdout.write(self.style.WARNING("  ⚠️ No companies found matching the selection criteria."))
            return

        self.stdout.write(f"  Universe to score: {total_companies} companies")
        self.stdout.write(f"  Persistence mode:  {'New historical snapshot' if create_snapshot else 'In-place update'}")
        self.stdout.write(self.style.SUCCESS("-" * 75))

        results = []
        success_count = 0
        error_count = 0

        for idx, sym in enumerate(symbols_to_score, start=1):
            try:
                res = calculate_company_health(
                    symbol=sym,
                    save_to_db=True,
                    create_snapshot=create_snapshot
                )
                results.append(res)
                success_count += 1

                if not quiet:
                    score = res['overall_score']
                    label = res['health_label']
                    p = res['pillar_scores']
                    
                    if score >= 80:
                        score_styled = self.style.SUCCESS(f"{score:>6.2f} [{label:<9}]")
                    elif score >= 60:
                        score_styled = self.style.SUCCESS(f"{score:>6.2f} [{label:<9}]")
                    elif score >= 40:
                        score_styled = self.style.WARNING(f"{score:>6.2f} [{label:<9}]")
                    else:
                        score_styled = self.style.ERROR(f"{score:>6.2f} [{label:<9}]")

                    self.stdout.write(
                        f"  [{idx:>3}/{total_companies}] {sym:<12} -> {score_styled} | "
                        f"Prof:{p['profitability']:>5.1f}/35 | Solv:{p['solvency']:>5.1f}/35 | "
                        f"Eff:{p['efficiency']:>4.1f}/15 | Gro:{p['growth']:>4.1f}/15"
                    )

            except Exception as e:
                error_count += 1
                self.stdout.write(self.style.ERROR(f"  [{idx:>3}/{total_companies}] {sym:<12} -> FAILED: {e}"))

        elapsed = round(time.time() - start_time, 2)

        # Universe Analytics
        self.stdout.write(self.style.SUCCESS("=" * 75))
        self.stdout.write(self.style.SUCCESS("  📊 Health Scoring Summary"))
        self.stdout.write(self.style.SUCCESS("=" * 75))
        self.stdout.write(f"  Completed in: {elapsed} seconds")
        self.stdout.write(f"  Successfully Scored: {success_count}/{total_companies}")
        if error_count > 0:
            self.stdout.write(self.style.ERROR(f"  Errors: {error_count}"))

        if results:
            scores = [r['overall_score'] for r in results]
            avg_score = round(sum(scores) / len(scores), 2)
            
            # Label Distribution
            label_counts = {'EXCELLENT': 0, 'GOOD': 0, 'AVERAGE': 0, 'WEAK': 0, 'POOR': 0}
            for r in results:
                lbl = r['health_label']
                label_counts[lbl] = label_counts.get(lbl, 0) + 1

            self.stdout.write(f"  Average Universe Score: {avg_score} / 100")
            self.stdout.write("")
            self.stdout.write("  Distribution:")
            for lbl in ['EXCELLENT', 'GOOD', 'AVERAGE', 'WEAK', 'POOR']:
                cnt = label_counts.get(lbl, 0)
                pct = round((cnt / len(results)) * 100, 1)
                bar = "█" * int(pct / 4)
                self.stdout.write(f"    {lbl:<10}: {cnt:>3} ({pct:>5.1f}%)  {bar}")

            # Top 5 & Bottom 5 Rankings
            sorted_results = sorted(results, key=lambda x: x['overall_score'], reverse=True)
            self.stdout.write("")
            self.stdout.write(self.style.SUCCESS("  Top 5 Companies by Health Score:"))
            for rank, r in enumerate(sorted_results[:5], start=1):
                self.stdout.write(f"    {rank}. {r['symbol']:<12} Score: {r['overall_score']:>5.2f} [{r['health_label']}]")

            self.stdout.write("")
            self.stdout.write(self.style.WARNING("  Bottom 5 Companies by Health Score:"))
            for rank, r in enumerate(reversed(sorted_results[-5:]), start=1):
                self.stdout.write(f"    {rank}. {r['symbol']:<12} Score: {r['overall_score']:>5.2f} [{r['health_label']}]")

        self.stdout.write(self.style.SUCCESS("=" * 75))
        self.stdout.write("")

