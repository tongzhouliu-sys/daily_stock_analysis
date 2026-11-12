# -*- coding: utf-8 -*-
"""Parameter sensitivity analysis for VCR Short strategy.

Runs multiple backtests with different parameter values to assess
strategy robustness and parameter stability.
"""

from typing import List, Dict, Any
from src.services.signal_scanner.research.vcr_short.config import VCRShortConfig, SensitivityConfig


class SensitivityAnalyzer:
    """Runs parameter sensitivity analysis."""

    def __init__(self, base_config: VCRShortConfig):
        self.base_config = base_config
        self.sensitivity_config = SensitivityConfig()

    def generate_parameter_grid(self) -> List[Dict[str, Any]]:
        """Generate parameter combinations for sensitivity testing.

        Returns:
            List of parameter dictionaries
        """
        variants = self.sensitivity_config.generate_variants(self.base_config)

        grid = []
        for param_name, param_value, config in variants:
            grid.append({
                "parameter": param_name,
                "value": param_value,
                "config": config,
            })

        return grid

    def format_results(
        self,
        default_result: Dict[str, Any],
        sensitivity_results: List[Dict[str, Any]]
    ) -> Dict[str, Any]:
        """Format sensitivity analysis results.

        Args:
            default_result: Results from default parameter set
            sensitivity_results: Results from parameter variations

        Returns:
            Formatted sensitivity analysis report
        """
        formatted = {
            "default": {
                "parameters": self.base_config.to_dict(),
                "metrics": default_result,
            },
            "sensitivity": [],
        }

        for result in sensitivity_results:
            param_name = result["parameter"]
            param_value = result["value"]
            metrics = result["metrics"]

            # Calculate differences from default
            diff_metrics = {}
            for key in ["cagr", "max_drawdown", "sharpe_ratio", "sortino_ratio", "calmar_ratio"]:
                if key in metrics and key in default_result:
                    default_val = default_result[key]
                    current_val = metrics[key]

                    if default_val is not None and current_val is not None:
                        diff = current_val - default_val
                        pct_change = (diff / abs(default_val) * 100) if default_val != 0 else None
                        diff_metrics[key] = {
                            "default": default_val,
                            "current": current_val,
                            "difference": diff,
                            "percent_change": pct_change,
                        }

            formatted["sensitivity"].append({
                "parameter": param_name,
                "value": param_value,
                "metrics": metrics,
                "differences": diff_metrics,
            })

        return formatted

    def identify_critical_parameters(
        self,
        sensitivity_results: List[Dict[str, Any]]
    ) -> List[Dict[str, Any]]:
        """Identify parameters that have the largest impact on performance.

        Args:
            sensitivity_results: Results from parameter variations

        Returns:
            List of critical parameters sorted by impact
        """
        parameter_impacts = {}

        for result in sensitivity_results:
            param_name = result["parameter"]
            metrics = result["metrics"]

            if param_name not in parameter_impacts:
                parameter_impacts[param_name] = {
                    "parameter": param_name,
                    "values_tested": [],
                    "cagr_range": [float("inf"), float("-inf")],
                    "sharpe_range": [float("inf"), float("-inf")],
                    "max_drawdown_range": [float("inf"), float("-inf")],
                }

            impact = parameter_impacts[param_name]
            impact["values_tested"].append(result["value"])

            # Update ranges
            for metric in ["cagr", "sharpe_ratio", "max_drawdown"]:
                if metric in metrics and metrics[metric] is not None:
                    val = metrics[metric]
                    impact[f"{metric}_range"][0] = min(impact[f"{metric}_range"][0], val)
                    impact[f"{metric}_range"][1] = max(impact[f"{metric}_range"][1], val)

        # Calculate impact scores
        critical_params = []
        for param_name, impact in parameter_impacts.items():
            # Calculate range width for each metric
            cagr_range = impact["cagr_range"][1] - impact["cagr_range"][0]
            sharpe_range = impact["sharpe_range"][1] - impact["sharpe_range"][0]
            dd_range = impact["max_drawdown_range"][1] - impact["max_drawdown_range"][0]

            # Simple impact score (sum of normalized ranges)
            impact_score = cagr_range + sharpe_range + dd_range

            critical_params.append({
                "parameter": param_name,
                "impact_score": impact_score,
                "cagr_range": impact["cagr_range"],
                "sharpe_range": impact["sharpe_range"],
                "max_drawdown_range": impact["max_drawdown_range"],
                "values_tested": sorted(impact["values_tested"]),
            })

        # Sort by impact score (descending)
        critical_params.sort(key=lambda x: x["impact_score"], reverse=True)

        return critical_params
