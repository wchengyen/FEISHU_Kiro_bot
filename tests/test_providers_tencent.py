import json
import subprocess
import pytest
from unittest.mock import patch, MagicMock
from dashboard.providers.tencent import TencentProvider, _tccli
from dashboard.providers.base import Resource


@pytest.fixture
def provider():
    with patch("dashboard.providers.tencent._load_config") as m:
        m.return_value = {"providers": {"tencent": {"enabled": True, "regions": ["ap-tokyo"]}}}
        yield TencentProvider()


def test_name(provider):
    assert provider.name == "tencent"


def test_resource_types(provider):
    assert set(provider.resource_types()) == {"cvm", "lighthouse"}


@patch("dashboard.providers.tencent.subprocess.run")
def test_discover_cvm(mock_run, provider):
    with open("tests/fixtures/tencent_cvm_describe.json") as f:
        data = json.load(f)
    mock_run.return_value = MagicMock(stdout=json.dumps(data), returncode=0)
    resources = provider.discover_resources("ap-tokyo", "cvm")
    assert len(resources) == 1
    assert resources[0].id == "ins-123456"
    assert resources[0].provider == "tencent"
    assert resources[0].resource_type == "cvm"
    assert resources[0].unique_id == "tencent:cvm:ap-tokyo:ins-123456"


@patch("dashboard.providers.tencent.subprocess.run")
def test_get_metrics(mock_run, provider):
    with open("tests/fixtures/tencent_monitor_cpu.json") as f:
        data = json.load(f)
    mock_run.return_value = MagicMock(stdout=json.dumps(data), returncode=0)
    r = Resource(provider="tencent", resource_type="cvm", region="ap-tokyo", id="ins-123456", name="t", status="RUNNING")
    metrics = provider.get_metrics(r, range_days=7)
    assert metrics.metric_name == "cpu_utilization"
    assert len(metrics.points_7d) == 2


@patch("dashboard.providers.tencent.subprocess.run")
def test_tccli_timeout(mock_run):
    mock_run.side_effect = subprocess.TimeoutExpired(cmd=["tccli"], timeout=60)
    with pytest.raises(RuntimeError, match="tccli timeout"):
        _tccli("cvm", "DescribeInstances", "ap-tokyo")


@patch("dashboard.providers.tencent.subprocess.run")
def test_tccli_called_process_error(mock_run):
    mock_run.side_effect = subprocess.CalledProcessError(returncode=1, cmd=["tccli"], stderr="auth failed")
    with pytest.raises(RuntimeError, match="tccli failed"):
        _tccli("cvm", "DescribeInstances", "ap-tokyo")


@patch("dashboard.providers.tencent.subprocess.run")
def test_tccli_invalid_json(mock_run):
    mock_run.return_value = MagicMock(stdout="not json", returncode=0)
    with pytest.raises(RuntimeError, match="tccli returned invalid JSON"):
        _tccli("cvm", "DescribeInstances", "ap-tokyo")


@patch("dashboard.providers.tencent.subprocess.run")
def test_sync_metrics_to_store(mock_run, provider):
    with open("tests/fixtures/tencent_cvm_describe.json") as f:
        cvm_data = json.load(f)
    with open("tests/fixtures/tencent_monitor_cpu.json") as f:
        monitor_data = json.load(f)

    def side_effect(*args, **kwargs):
        service = args[0][1]
        if service == "cvm":
            return MagicMock(stdout=json.dumps(cvm_data), returncode=0)
        if service == "monitor":
            return MagicMock(stdout=json.dumps(monitor_data), returncode=0)
        return MagicMock(stdout="{}", returncode=0)

    mock_run.side_effect = side_effect

    store = MagicMock()
    provider.sync_metrics_to_store(store, backfill_days=1)
    assert store.write_hourly.called
    call_args = store.write_hourly.call_args[0][0]
    assert len(call_args) == 2
    resource_id, metric_name, timestamp, value, region = call_args[0]
    assert resource_id == "tencent:cvm:ap-tokyo:ins-123456"
    assert metric_name == "CPUUtilization"
    assert isinstance(timestamp, int)
    assert timestamp % 3600 == 0
    assert value == 5.2
    assert region == "ap-tokyo"
