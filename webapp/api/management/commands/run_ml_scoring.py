import time
from django.core.management.base import BaseCommand, CommandError
from nifty_core.ml_scoring import calculate_and_save_health_scores


class Command(BaseCommand):
    help = 'Executes Phase 3: Machine Learning Health Scoring Engine across the Nifty 100 universe.'

    def add_arguments(self, parser):
        parser.add_argument(
            '--symbol',
            type=str,
            default=None,
            help='Run scoring for a specific company symbol only (e.g., TCS, HDFCBANK, RELIANCE)'
        )
        parser.add_argument(
            '--snapshot',
            action='store_true',
            help='Create a new timestamped snapshot in FactMlScores rather than updating the latest record'
        )
        parser.add_argument(
            '--quiet',
            action='store_true',
            help='Suppress per-company console output and display only summary metrics'
        )

    def handle(self, *args, **options):
        symbol_arg = options.get('symbol')
        create_snapshot = options.get('snapshot', False)
        quiet = options.get('quiet', False)

        start_time = time.time()

        self.stdout.write("")
        self.stdout.write(self.style.SUCCESS("=" * 80))
        self.stdout.write(self.style.SUCCESS("  🚀 NiftyIQ Phase 3: Machine Learning Financial Health Scoring Engine"))
        self.stdout.write(self.style.SUCCESS("=" * 80))

        if symbol_arg:
            self.stdout.write(f"  Target Scope:     Single Symbol [{symbol_arg.strip().upper()}]")
        else:
            self.stdout.write("  Target Scope:     Full Nifty 100 Universe")

        self.stdout.write(f"  Mode:             {'New historical snapshot' if create_snapshot else 'In-place update'}")
        self.stdout.write(f"  Pillars:          Profitability (25%), Growth (20%), Leverage (20%), Cashflow (15%), Dividend (10%), Trend (10%)")
        self.stdout.write(f"  Financial Excl:   Banks & NBFCs exempted from Leverage & Cashflow (re-weighted to 100)")
        self.stdout.write(self.style.SUCCESS("-" * 80))

        try:
            summary = calculate_and_save_health_scores(
                symbol=symbol_arg,
                create_snapshot=create_snapshot,
                quiet=quiet
            )
        except Exception as exc:
            raise CommandError(f"Scoring engine execution failed: {exc}")

        total = summary.get('total_companies', 0)
        results = summary.get('results', [])
        distribution = summary.get('distribution', {})
        avg_score = summary.get('average_score', 0.0)

        if total == 0:
            self.stdout.write(self.style.WARNING("  ⚠️ No companies found matching the selection criteria."))
            return

        if not quiet:
            self.stdout.write(f"\n  {'SYMBOL':<12} {'SCORE':<10} {'LABEL':<12} {'SECTOR':<22} {'P / G / L / C / D / T'}")
            self.stdout.write("  " + "-" * 76)

            for r in results:
                sym = r['symbol']
                score = r['overall_score']
                label = r['health_label']
                sector = (r.get('sector') or 'Unknown')[:20]
                p = r['pillar_scores']

                p_str = (
                    f"{p['profitability'] or 0:.1f}/"
                    f"{p['growth'] or 0:.1f}/"
                    f"{p['leverage'] or 0:.1f}/"
                    f"{p['cashflow'] or 0:.1f}/"
                    f"{p['dividend'] or 0:.1f}/"
                    f"{p['trend'] or 0:.1f}"
                )

                score_str = f"{score:.1f}" if score is not None else "N/A"

                if label == 'EXCELLENT':
                    colored_label = self.style.SUCCESS(f"{label:<12}")
                elif label == 'GOOD':
                    colored_label = self.style.NOTICE(f"{label:<12}")
                elif label == 'AVERAGE':
                    colored_label = self.style.WARNING(f"{label:<12}")
                else:
                    colored_label = self.style.ERROR(f"{label:<12}")

                self.stdout.write(f"  {sym:<12} {score_str:<10} {colored_label} {sector:<22} {p_str}")

        elapsed = time.time() - start_time

        self.stdout.write("")
        self.stdout.write(self.style.SUCCESS("=" * 80))
        self.stdout.write(self.style.SUCCESS("  📊 Health Scoring Pipeline Execution Summary"))
        self.stdout.write(self.style.SUCCESS("=" * 80))
        self.stdout.write(f"  Total Processed:     {total} companies")
        self.stdout.write(f"  Records Updated:     {summary.get('updated_count', 0)}")
        self.stdout.write(f"  Average Score:       {avg_score:.2f} / 100")
        self.stdout.write("  Score Distribution:")
        self.stdout.write(f"    • EXCELLENT (85+):  {distribution.get('EXCELLENT', 0)}")
        self.stdout.write(f"    • GOOD (70-84):     {distribution.get('GOOD', 0)}")
        self.stdout.write(f"    • AVERAGE (50-69):  {distribution.get('AVERAGE', 0)}")
        self.stdout.write(f"    • WEAK (35-49):     {distribution.get('WEAK', 0)}")
        self.stdout.write(f"    • POOR (<35):       {distribution.get('POOR', 0)}")
        self.stdout.write(f"  Execution Time:      {elapsed:.2f}s")
        self.stdout.write(self.style.SUCCESS("=" * 80))
        self.stdout.write(self.style.SUCCESS("  ✅ ML Scoring completed successfully.\n"))

