#!/usr/bin/env python3
"""Sync resource metrics from CloudWatch to local SQLite.

Cron setup example:
    0 3 * * * cd /home/ubuntu/kiro-devops && PYTHONPATH=/home/ubuntu/kiro-devops /usr/bin/python3 scripts/sync_resource_metrics.py --incremental >> /var/log/kiro-metrics-sync.log 2>&1

First run (backfill 30 days):
    PYTHONPATH=/home/ubuntu/kiro-devops python3 scripts/sync_resource_metrics.py --backfill
"""
import argparse
import datetime
import logging

from dashboard.resources import discover_all
from dashboard.metrics_store import MetricsStore

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(message)s",
)
logger = logging.getLogger("sync_resource_metrics")


def parse_args(argv=None):
    parser = argparse.ArgumentParser(description="Sync resource metrics from CloudWatch to local SQLite")
    parser.add_argument("--backfill", action="store_true", help="Backfill past 30 days of hourly data")
    parser.add_argument("--incremental", action="store_true", help="Sync previous 24 hours")
    parser.add_argument("--downsample", nargs=2, type=int, metavar=("YEAR", "MONTH"), help="Downsample a specific month")
    parser.add_argument("--base-dir", default=None, help="Override metrics base directory")
    return parser.parse_args(argv)


def fetch_cloudwatch_hourly(resource, metric_name="CPUUtilization", hours=24, end=None) -> list[tuple]:
    try:
        import boto3
    except ImportError:
        logger.warning("boto3 not installed, skipping CloudWatch fetch")
        return []

    region = resource.meta.get("region")
    kwargs = {"region_name": region} if region else {}
    client = boto3.client("cloudwatch", **kwargs)

    if end is None:
        end = datetime.datetime.utcnow()
    start = end - datetime.timedelta(hours=hours)

    if resource.type == "ec2":
        namespace = "AWS/EC2"
        dimension_name = "InstanceId"
        dimension_value = resource.raw_id
    elif resource.type == "rds":
        namespace = "AWS/RDS"
        dimension_name = "DBInstanceIdentifier"
        dimension_value = resource.raw_id
    else:
        return []

    resp = client.get_metric_statistics(
        Namespace=namespace,
        MetricName=metric_name,
        Dimensions=[{"Name": dimension_name, "Value": dimension_value}],
        StartTime=start,
        EndTime=end,
        Period=3600,
        Statistics=["Average"],
    )

    points = sorted(resp.get("Datapoints", []), key=lambda x: x["Timestamp"])
    records = []
    for p in points:
        ts = int(p["Timestamp"].replace(tzinfo=datetime.timezone.utc).timestamp())
        # Round to nearest hour
        ts = ts // 3600 * 3600
        records.append((resource.id, metric_name, ts, round(p["Average"], 2), region))
    return records


def run_backfill(base_dir=None) -> int:
    store = MetricsStore(base_dir=base_dir)
    resources = discover_all()
    logger.info(f"Discovered {len(resources)} resources for backfill")
    total = 0
    for resource in resources:
        try:
            records = fetch_cloudwatch_hourly(resource, hours=24 * 30)
            if records:
                store.write_hourly(records)
                total += len(records)
                logger.info(f"Backfilled {len(records)} points for {resource.id}")
        except Exception as e:
            logger.warning(f"Backfill failed for {resource.id}: {e}")
    store.close()
    logger.info(f"Backfill complete: {total} total points")
    return total


def run_incremental(base_dir=None) -> int:
    store = MetricsStore(base_dir=base_dir)
    resources = discover_all()
    logger.info(f"Discovered {len(resources)} resources for incremental sync")
    total = 0
    for resource in resources:
        try:
            records = fetch_cloudwatch_hourly(resource, hours=24)
            if records:
                store.write_hourly(records)
                total += len(records)
                logger.info(f"Synced {len(records)} points for {resource.id}")
        except Exception as e:
            logger.warning(f"Sync failed for {resource.id}: {e}")

    # Downsample previous month if it has just completed
    now = datetime.datetime.utcnow()
    prev_month = now.month - 1 or 12
    prev_year = now.year if now.month > 1 else now.year - 1
    try:
        inserted = store.downsample_month(prev_year, prev_month)
        if inserted:
            logger.info(f"Downsampled {inserted} daily rows for {prev_year}-{prev_month:02d}")
    except Exception as e:
        logger.warning(f"Downsample failed for {prev_year}-{prev_month:02d}: {e}")

    # Cleanup old aggregated data
    try:
        deleted = store.cleanup_old_daily(keep_days=180)
        if deleted:
            logger.info(f"Cleaned up {deleted} old daily rows")
    except Exception as e:
        logger.warning(f"Cleanup failed: {e}")

    store.close()
    logger.info(f"Incremental sync complete: {total} total points")
    return total


def main() -> None:
    args = parse_args()
    if args.backfill:
        run_backfill(base_dir=args.base_dir)
    elif args.incremental:
        run_incremental(base_dir=args.base_dir)
    elif args.downsample:
        year, month = args.downsample
        store = MetricsStore(base_dir=args.base_dir)
        inserted = store.downsample_month(year, month)
        logger.info(f"Downsampled {inserted} rows for {year}-{month:02d}")
        store.close()
    else:
        # Default to incremental
        run_incremental(base_dir=args.base_dir)


if __name__ == "__main__":
    main()
