"""Immaculate Vibes 254 Retention Action Engine."""
from .pipeline import (  # noqa: F401
    DEFAULT_CONFIG, DataValidationError, RunResult, backtest_rules, build_action_list,
    build_customers, compute_kpis, lifecycle, load_orders, make_config, quality_report,
    roi_scenario, run, score_rfm, segment_summary, sensitivity_grid, thresholds,
    winback_conversion,
)

__version__ = "1.0.0"
