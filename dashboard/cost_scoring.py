"""Cost-efficiency scoring system.

Scoring is based solely on CPU utilisation.  The optimal target is 80 %.
Lower values indicate wasted capacity; higher values indicate overload.
"""

from __future__ import annotations

# ---------------------------------------------------------------------------
# AWS China (cn-north-1 / cn-northwest-1) On-Demand Linux prices (USD/hr).
# These are representative values; exact prices should be refreshed from the
# AWS Price List API for production use.
# ---------------------------------------------------------------------------
EC2_HOURLY_PRICES: dict[str, float] = {
    # T3
    "t3.micro": 0.0104,
    "t3.small": 0.0208,
    "t3.medium": 0.0416,
    "t3.large": 0.0832,
    "t3.xlarge": 0.1664,
    "t3.2xlarge": 0.3328,
    # T4g (Graviton)
    "t4g.micro": 0.0084,
    "t4g.small": 0.0168,
    "t4g.medium": 0.0336,
    "t4g.large": 0.0672,
    "t4g.xlarge": 0.1344,
    "t4g.2xlarge": 0.2688,
    # M5
    "m5.large": 0.096,
    "m5.xlarge": 0.192,
    "m5.2xlarge": 0.384,
    "m5.4xlarge": 0.768,
    "m5.8xlarge": 1.536,
    "m5.12xlarge": 2.304,
    "m5.16xlarge": 3.072,
    "m5.24xlarge": 4.608,
    # M6g (Graviton)
    "m6g.large": 0.077,
    "m6g.xlarge": 0.154,
    "m6g.2xlarge": 0.308,
    "m6g.4xlarge": 0.616,
    "m6g.8xlarge": 1.232,
    "m6g.12xlarge": 1.848,
    "m6g.16xlarge": 2.464,
    # C5
    "c5.large": 0.085,
    "c5.xlarge": 0.17,
    "c5.2xlarge": 0.34,
    "c5.4xlarge": 0.68,
    "c5.9xlarge": 1.53,
    "c5.12xlarge": 2.04,
    "c5.18xlarge": 3.06,
    "c5.24xlarge": 4.08,
    # C6g (Graviton)
    "c6g.large": 0.068,
    "c6g.xlarge": 0.136,
    "c6g.2xlarge": 0.272,
    "c6g.4xlarge": 0.544,
    "c6g.8xlarge": 1.088,
    "c6g.12xlarge": 1.632,
    "c6g.16xlarge": 2.176,
    # R5
    "r5.large": 0.126,
    "r5.xlarge": 0.252,
    "r5.2xlarge": 0.504,
    "r5.4xlarge": 1.008,
    # R6g
    "r6g.large": 0.1008,
    "r6g.xlarge": 0.2016,
    "r6g.2xlarge": 0.4032,
}

RDS_HOURLY_PRICES: dict[str, float] = {
    "db.t3.micro": 0.017,
    "db.t3.small": 0.034,
    "db.t3.medium": 0.068,
    "db.t3.large": 0.136,
    "db.t3.xlarge": 0.272,
    "db.t3.2xlarge": 0.544,
    "db.t4g.micro": 0.0136,
    "db.t4g.small": 0.0272,
    "db.t4g.medium": 0.0544,
    "db.t4g.large": 0.1088,
    "db.m5.large": 0.136,
    "db.m5.xlarge": 0.272,
    "db.m5.2xlarge": 0.544,
    "db.m6g.large": 0.109,
    "db.m6g.xlarge": 0.218,
    "db.r5.large": 0.175,
    "db.r5.xlarge": 0.35,
    "db.r6g.large": 0.14,
    "db.r6g.xlarge": 0.28,
}

HOURS_PER_MONTH = 730
OPTIMAL_CPU = 80.0


def compute_cost_score(cpu_avg: float | None) -> float:
    """Return a 0-100 cost-efficiency score based on average CPU utilisation.

    80 % is the sweet spot (score = 100).
    * Under-utilisation  (cpu < 80 %) is penalised linearly.
    * Over-utilisation   (cpu > 80 %) is penalised more aggressively
      because sustained overload hurts stability.
    """
    if cpu_avg is None:
        return 0.0

    deviation = abs(cpu_avg - OPTIMAL_CPU)
    penalty = 1.0 if cpu_avg <= OPTIMAL_CPU else 1.5
    score = 100.0 - (deviation / OPTIMAL_CPU) * 100.0 * penalty
    return max(0.0, min(100.0, round(score, 1)))


def get_cost_grade(score: float) -> str:
    if score >= 90:
        return "A"
    if score >= 70:
        return "B"
    if score >= 50:
        return "C"
    if score >= 30:
        return "D"
    return "F"


def get_cost_advice(cpu_avg: float | None) -> str:
    if cpu_avg is None:
        return "无 CPU 数据，无法评估"
    if cpu_avg < 30:
        return "资源严重浪费，强烈建议降配或停用"
    if cpu_avg < 50:
        return "资源利用率偏低，建议降配"
    if cpu_avg < 70:
        return "利用率一般，可考虑优化"
    if cpu_avg < 90:
        return "利用率合理，成本效率良好"
    return "资源过载，存在性能风险，建议升配"


def grade_color(grade: str) -> str:
    return {
        "A": "#22c55e",   # green
        "B": "#14b8a6",   # teal
        "C": "#eab308",   # yellow
        "D": "#f97316",   # orange
        "F": "#ef4444",   # red
    }.get(grade, "#94a3b8")


def get_hourly_price(resource_type: str, class_type: str | None) -> float | None:
    if not class_type:
        return None
    if resource_type == "ec2":
        return EC2_HOURLY_PRICES.get(class_type)
    if resource_type == "rds":
        return RDS_HOURLY_PRICES.get(class_type)
    return None


def compute_waste_cost(hourly_price: float | None, score: float) -> dict | None:
    """Return monthly cost breakdown."""
    if hourly_price is None:
        return None
    monthly = round(hourly_price * HOURS_PER_MONTH, 2)
    effective = round(monthly * (score / 100.0), 2)
    waste = round(monthly - effective, 2)
    return {
        "monthly": monthly,
        "effective": effective,
        "waste": waste,
    }
