from collections import defaultdict
from dataclasses import dataclass, field
import datetime
import json
import os
import time


@dataclass
class Resource:
    id: str
    type: str
    name: str
    raw_id: str
    status: str
    meta: dict = field(default_factory=dict)
    tags: dict = field(default_factory=dict)
    sparkline: list = field(default_factory=list)
    current: float | None = None
    stats_7d: dict = field(default_factory=lambda: {"avg": None, "p95": None, "max": None})
    stats_30d: dict = field(default_factory=lambda: {"avg": None, "p95": None, "max": None})


def _load_regions() -> list[str]:
    """从 dashboard_config.json 读取 regions 配置."""
    config_path = os.path.join(os.path.dirname(__file__), "..", "dashboard_config.json")
    if os.path.exists(config_path):
        with open(config_path) as f:
            config = json.load(f)
            return config.get("regions", [])
    return []


def discover_ec2(region: str | None = None):
    try:
        import boto3
    except ImportError:
        return []
    kwargs = {"region_name": region} if region else {}
    client = boto3.client("ec2", **kwargs)
    region_name = region or client._client_config.region_name or ""
    resp = client.describe_instances(
        Filters=[{"Name": "instance-state-name", "Values": ["running", "stopped"]}]
    )
    resources = []
    for reservation in resp.get("Reservations", []):
        for inst in reservation.get("Instances", []):
            tags = {tag.get("Key", ""): tag.get("Value", "") for tag in inst.get("Tags", [])}
            name = tags.get("Name", "")
            platform = inst.get("Platform")
            os_name = "Windows" if platform == "windows" else "Linux/Unix"
            resources.append(
                Resource(
                    id=f"ec2:{region_name}:{inst['InstanceId']}",
                    type="ec2",
                    name=name or inst["InstanceId"],
                    raw_id=inst["InstanceId"],
                    status=inst["State"]["Name"],
                    meta={
                        "instance_type": inst.get("InstanceType", ""),
                        "region": region_name,
                        "os": os_name,
                    },
                    tags=tags,
                )
            )
    return resources


def discover_rds(region: str | None = None):
    try:
        import boto3
    except ImportError:
        return []
    kwargs = {"region_name": region} if region else {}
    client = boto3.client("rds", **kwargs)
    region_name = region or client._client_config.region_name or ""
    resp = client.describe_db_instances()
    resources = []
    for db in resp.get("DBInstances", []):
        tags = {}
        db_arn = db.get("DBInstanceArn", "")
        if db_arn:
            try:
                tag_resp = client.list_tags_for_resource(ResourceName=db_arn)
                tags = {tag.get("Key", ""): tag.get("Value", "") for tag in tag_resp.get("TagList", [])}
            except Exception:
                pass
        name = tags.get("Name", db["DBInstanceIdentifier"])
        resources.append(
            Resource(
                id=f"rds:{region_name}:{db['DBInstanceIdentifier']}",
                type="rds",
                name=name,
                raw_id=db["DBInstanceIdentifier"],
                status=db["DBInstanceStatus"],
                meta={
                    "engine": db.get("Engine", ""),
                    "region": region_name,
                    "db_instance_class": db.get("DBInstanceClass", ""),
                },
                tags=tags,
            )
        )
    return resources


def discover_all():
    regions = _load_regions()
    if not regions:
        # 回退到默认行为：单个默认区域
        return discover_ec2() + discover_rds()
    all_resources = []
    for region in regions:
        try:
            all_resources.extend(discover_ec2(region))
        except Exception as e:
            # 记录错误但继续其他区域
            import logging
            logging.getLogger("dashboard.resources").warning(f"EC2 discovery failed in {region}: {e}")
        try:
            all_resources.extend(discover_rds(region))
        except Exception as e:
            import logging
            logging.getLogger("dashboard.resources").warning(f"RDS discovery failed in {region}: {e}")
    return all_resources


def get_cloudwatch_metrics(resource_id, namespace, dimension_name, days=7, region=None):
    try:
        import boto3
    except ImportError:
        return []
    kwargs = {"region_name": region} if region else {}
    client = boto3.client("cloudwatch", **kwargs)
    end = datetime.datetime.utcnow()
    start = end - datetime.timedelta(days=days)
    resp = client.get_metric_statistics(
        Namespace=namespace,
        MetricName="CPUUtilization",
        Dimensions=[{"Name": dimension_name, "Value": resource_id}],
        StartTime=start,
        EndTime=end,
        Period=3600,
        Statistics=["Average", "Maximum"],
    )
    points = sorted(resp.get("Datapoints", []), key=lambda x: x["Timestamp"])
    return [
        {
            "Timestamp": p["Timestamp"],
            "Average": p["Average"],
            "Maximum": p["Maximum"],
        }
        for p in points
    ]


def compute_stats(points):
    if not points:
        return {"avg": None, "p95": None, "max": None}
    averages = [p["Average"] for p in points]
    maxima = [p["Maximum"] for p in points]
    sorted_avgs = sorted(averages)
    idx = int(len(sorted_avgs) * 0.95)
    p95_val = sorted_avgs[min(idx, len(sorted_avgs) - 1)]
    return {
        "avg": round(sum(averages) / len(averages), 1),
        "p95": round(p95_val, 1),
        "max": round(max(maxima), 1),
    }


def sparkline_from_points(points):
    if not points:
        return []
    daily = defaultdict(list)
    for p in points:
        day = p["Timestamp"].strftime("%Y-%m-%d")
        daily[day].append(p["Average"])
    return [round(sum(v) / len(v), 1) for v in daily.values()]


_cache = {"data": None, "ts": 0}
CACHE_TTL = 300


def get_all_resources_with_metrics(refresh=False):
    global _cache
    if (
        not refresh
        and _cache["data"] is not None
        and (time.time() - _cache["ts"]) < CACHE_TTL
    ):
        return _cache["data"]

    resources = discover_all()
    for r in resources:
        region = r.meta.get("region")
        if r.type == "ec2":
            metrics_7d = get_cloudwatch_metrics(r.raw_id, "AWS/EC2", "InstanceId", days=7, region=region)
            metrics_30d = get_cloudwatch_metrics(r.raw_id, "AWS/EC2", "InstanceId", days=30, region=region)
        elif r.type == "rds":
            metrics_7d = get_cloudwatch_metrics(
                r.raw_id, "AWS/RDS", "DBInstanceIdentifier", days=7, region=region
            )
            metrics_30d = get_cloudwatch_metrics(
                r.raw_id, "AWS/RDS", "DBInstanceIdentifier", days=30, region=region
            )
        else:
            metrics_7d = []
            metrics_30d = []

        r.sparkline = sparkline_from_points(metrics_7d)
        r.current = r.sparkline[-1] if r.sparkline else None
        r.stats_7d = compute_stats(metrics_7d)
        r.stats_30d = compute_stats(metrics_30d)

    data = {
        "resources": [resource_to_dict(r) for r in resources],
        "cached": False,
        "error": None,
    }
    _cache = {"data": data, "ts": time.time()}
    return data


def resource_to_dict(r: Resource) -> dict:
    return {
        "id": r.id,
        "type": r.type,
        "name": r.name,
        "raw_id": r.raw_id,
        "status": r.status,
        "meta": r.meta,
        "tags": r.tags,
        "sparkline": r.sparkline,
        "current": r.current,
        "stats_7d": r.stats_7d,
        "stats_30d": r.stats_30d,
    }
