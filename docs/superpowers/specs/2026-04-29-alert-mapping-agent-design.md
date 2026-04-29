# Alert Mapping → Dynamic Agent/Skill Invocation Design

## 背景

当前 `webhook_server.py` 的 `_trigger_analysis` 硬编码了 `--agent ec2-alert-analyzer`，所有告警（NodeNotReady、NodeExporterDown、CloudWatch 状态检查、成本告警等）都走同一个 agent 分析。这导致：

- Kubernetes 节点告警无法使用 EKS 专用 agent
- AWS 成本告警无法使用成本分析 agent
- 不同告警类型缺乏针对性的分析策略和指标查询模板

Dashboard 虽已存在 "Alert Mappings" 页面，但当前配置不被 webhook 处理逻辑读取，处于"可配但无效"状态。

## 目标

实现一套**可配置的告警规则引擎**，根据事件的多维度特征（source、alertname、severity、labels）动态选择 kiro-cli agent 和调用参数，并在 Dashboard UI 上可管理。

## 架构

```
┌─────────────────┐     ┌──────────────────┐     ┌─────────────────┐
│ Alertmanager    │────▶│ webhook_server   │────▶│ AlertMatcher    │
│ /event POST     │     │ /event           │     │ (规则引擎)       │
└─────────────────┘     └──────────────────┘     └─────────────────┘
                              │                           │
                              ▼                           ▼
                        ┌──────────┐              ┌──────────────┐
                        │ 去重缓存  │              │ dashboard_   │
                        │ (不变)    │              │ config.json  │
                        └──────────┘              │ mappings[]   │
                                                  └──────────────┘
                                                           │ mtime 检测
                                                           ▼
                                                  ┌──────────────┐
                                                  │ 内存缓存     │
                                                  │ 1s 刷新     │
                                                  └──────────────┘
                                                           │
                                                           ▼
                                                  ┌──────────────┐
                                                  │ kiro-cli     │
                                                  │ --agent X    │
                                                  │ --trust-tool │
                                                  └──────────────┘
```

## 数据模型

### 单条规则

```json
{
  "name": "k8s-node-notready",
  "enabled": true,
  "match": {
    "source": "prometheus",
    "alertname": "NodeNotReady",
    "severity": ["critical", "high"],
    "labels": {
      "job": "node-exporter"
    }
  },
  "action": {
    "agent": "eks-node-analyzer",
    "tools": ["execute_bash", "fs_read", "grep"],
    "instruction": "分析 K8s Node NotReady 根因，查询 kubectl get node 和 EC2 状态检查",
    "timeout": 300
  }
}
```

### 配置根结构

```json
{
  "mappings": [
    {
      "name": "k8s-node-notready",
      "enabled": true,
      "match": { ... },
      "action": { ... }
    },
    {
      "name": "aws-cost-spike",
      "enabled": true,
      "match": {
        "source": "cloudwatch",
        "alertname": ".*cost.*|.*billing.*"
      },
      "action": {
        "agent": "aws-cost-analyzer",
        "tools": ["execute_bash", "fs_read"],
        "instruction": null,
        "timeout": 300
      }
    }
  ],
  "alert_defaults": {
    "agent": "ec2-alert-analyzer",
    "tools": ["execute_bash"],
    "timeout": 300
  }
}
```

### 字段语义

| 字段 | 类型 | 说明 |
|------|------|------|
| `match.source` | string | 事件来源，如 `prometheus`、`cloudwatch` |
| `match.alertname` | string | 告警名称，支持正则（含 `.*` `\|` 自动识别） |
| `match.severity` | string \| string[] | 严重级别，数组表示 OR 匹配 |
| `match.labels` | dict | 任意 label 键值对匹配，值支持等值或正则 |
| `action.agent` | string | kiro-cli `--agent` 参数值 |
| `action.tools` | string[] | 每个工具生成一个 `--trust-tools=X` 参数 |
| `action.instruction` | string \| null | 覆盖默认 instruction，null 使用 agent 默认 prompt |
| `action.timeout` | int | kiro-cli 执行超时（秒） |
| `enabled` | bool | 是否启用该规则 |

## 匹配引擎

### 算法

规则按数组**顺序遍历**，第一个满足所有 match 条件的规则生效；无匹配时返回 `alert_defaults`。

### 条件求值

| match 值类型 | 求值逻辑 |
|-------------|---------|
| `string`（不含正则元字符） | 等值匹配：`record[field] == value` |
| `string`（含 `.*` `\|` `^` `$`） | 正则匹配：`re.match(value, record[field])` |
| `string[]` | OR 匹配：`record[field] in values` |
| `dict`（labels） | 每个 key 单独按上述逻辑匹配 |

### 从 record 提取 match 字段

```python
def _extract_match_field(record: dict, field: str):
    if field == "alertname":
        # 优先从 title 提取，其次从原始 payload labels
        title = record.get("title", "")
        if "[" in title and "]" in title:
            return title.split("]")[0].strip("[")
        return title.split()[0] if title else ""
    return record.get(field, "")
```

### 类设计

新增 `alert_matcher.py`：

```python
class AlertMatcher:
    def __init__(self, mappings: list[dict], defaults: dict):
        self.rules = [r for r in (mappings or []) if r.get("enabled", True)]
        self.defaults = defaults or {}

    def match(self, record: dict) -> dict:
        for rule in self.rules:
            if self._rule_matches(rule.get("match", {}), record):
                action = {**self.defaults, **rule.get("action", {})}
                return action
        return self.defaults.copy()

    def _rule_matches(self, match: dict, record: dict) -> bool:
        for field, expected in match.items():
            if field == "labels":
                if not self._labels_match(expected, record):
                    return False
            else:
                actual = self._extract_field(record, field)
                if not self._value_matches(expected, actual):
                    return False
        return True

    def _value_matches(self, expected, actual: str) -> bool:
        if isinstance(expected, list):
            return actual in expected
        if isinstance(expected, str) and re.search(r"[.*|^$|+?{}\[\]]", expected):
            return bool(re.search(expected, actual))
        return expected == actual

    def _labels_match(self, expected_labels: dict, record: dict) -> bool:
        raw_labels = record.get("_raw_labels", {})
        for k, v in expected_labels.items():
            if not self._value_matches(v, raw_labels.get(k, "")):
                return False
        return True

    def _extract_field(self, record: dict, field: str) -> str:
        if field == "alertname":
            title = record.get("title", "")
            m = re.search(r"\[([^\]]+)\]", title)
            return m.group(1) if m else title.split()[0]
        return record.get(field, "")
```

## Webhook 集成

### 配置热加载（方案 A-2：mtime 检测）

```python
import os
import time

class ConfigReloader:
    def __init__(self, store: ConfigStore):
        self.store = store
        self._matcher: AlertMatcher | None = None
        self._mtime = 0.0
        self._lock = threading.Lock()

    def get_matcher(self) -> AlertMatcher:
        with self._lock:
            path = self.store.mappings_path
            try:
                mtime = os.path.getmtime(path)
            except OSError:
                mtime = 0
            if self._matcher is None or mtime > self._mtime:
                cfg = self.store.load()
                self._matcher = AlertMatcher(
                    cfg.get("mappings", []),
                    cfg.get("alert_defaults", {})
                )
                self._mtime = mtime
            return self._matcher
```

### `_trigger_analysis` 改造

```python
def _trigger_analysis(handler, record: dict):
    matcher = config_reloader.get_matcher()
    action = matcher.match(record)

    agent = action.get("agent", "ec2-alert-analyzer")
    tools = action.get("tools", ["execute_bash"])
    timeout = action.get("timeout", 300)
    instruction = action.get("instruction")
    if not instruction:
        instruction = "请分析此告警的根因，查询相关指标数据，给出结构化的诊断报告。"

    alert_payload = json.dumps({
        "alert": {
            "source": record["source"],
            "event_type": record["event_type"],
            "title": record["title"],
            "description": record.get("description", ""),
            "entities": record.get("entities", []),
            "severity": record["severity"],
            "timestamp": record.get("timestamp"),
        },
        "instruction": instruction,
    }, ensure_ascii=False, indent=2)

    cmd = [kiro_bin, "chat", "--no-interactive", "-a", "--wrap", "never"]
    for tool in tools:
        cmd.append(f"--trust-tools={tool}")
    cmd += ["--agent", agent, alert_payload]

    # 后续执行逻辑不变...
```

### `_parse_alertmanager` 增强

保留原始 labels 供 `labels` 匹配使用：

```python
def _parse_alertmanager(payload: dict) -> dict:
    alert = payload["alerts"][0]
    labels = {**payload.get("commonLabels", {}), **alert.get("labels", {})}
    # ... 原有逻辑 ...
    result["_raw_labels"] = labels  # 新增：供匹配引擎使用
    return result
```

## Dashboard UI 扩展

### 现有页面问题

当前 Alert Mappings 表格为 5 列：`source`、`service`、`severity`、`agent`、`skill`。数据结构扁平，无法表达多维度 match 和丰富的 action 配置。

### 新设计：规则卡片列表

每条规则从表格行扩展为**独立卡片**，使用现有 `info-card` 样式（圆角边框 + 阴影）：

```
┌─────────────────────────────────────────────────────────────┐
│ ① 规则: k8s-node-notready                    [启用☑] [删除🗑] │
│ ─────────────────────────────────────────────────────────── │
│ Match 条件                                                  │
│   Source:     [prometheus ▼]                                │
│   Alertname:  [NodeNotReady        ]  (支持正则)            │
│   Severity:   [☑ critical] [☑ high] [☐ medium] [☐ low]     │
│   Labels:     job = node-exporter  [+]                      │
│ ─────────────────────────────────────────────────────────── │
│ Action                                                      │
│   Agent:      [eks-node-analyzer ▼]                         │
│   Tools:      [☑ execute_bash] [☑ fs_read] [☐ grep] ...    │
│   Timeout:    [300] 秒                                      │
│   Instruction: [分析 K8s Node NotReady 根因...    ]         │
└─────────────────────────────────────────────────────────────┘
```

### 交互细节

1. **规则排序**：每条卡片左上角显示序号（①②③...），上下箭头按钮调整优先级。因为规则按顺序匹配，顺序至关重要。

2. **启用/停用**：开关切换（启用=绿色边框，停用=整体置灰+半透明）。

3. **Severity 多选**：从单选下拉改为 checkbox 组（critical / high / medium / low）。

4. **Labels 动态列表**：键值对输入，支持添加/删除。值输入框旁小字提示"支持正则"。

5. **Tools 多选**：从 agent 的 skills 加载可用工具列表，checkbox 多选。

6. **Instruction 覆盖**：textarea，placeholder 为"留空使用 Agent 默认 Prompt"。

7. **Default 配置折叠面板**：规则列表底部增加可折叠的 "Fallback Defaults" 区域，编辑 `alert_defaults`。

### 向后兼容

- **读取旧格式**：`{source, service, severity, agent, skill}` 自动转换为新格式：
  - `service` → `match.labels.service`
  - `skill` → 忽略（skill 与 agent 在 kiro-cli 中是绑定关系，由 agent 配置决定）
  - 自动生成 `name` 为 `"legacy-" + agent`
- **保存**：只写入新格式，旧格式不再保留。

## 测试策略

1. **单元测试**：`test_alert_matcher.py`
   - 等值匹配、正则匹配、数组 OR 匹配
   - labels 匹配
   - 顺序优先级
   - fallback 默认
   - 旧格式向后兼容

2. **集成测试**：
   - Dashboard API `POST /mappings` 保存新格式后，webhook 能正确读取
   - 修改 `dashboard_config.json` 后 1 秒内 webhook 使用新规则

## 实现步骤

1. 新增 `alert_matcher.py`（匹配引擎 + ConfigReloader）
2. 修改 `webhook_server.py`：
   - 集成 ConfigReloader
   - 改造 `_trigger_analysis` 支持动态 agent/tools/instruction/timeout
   - `_parse_alertmanager` 保留 `_raw_labels`
3. 新增/修改 Dashboard API：
   - `GET/POST /mappings` 支持新格式读写
   - `GET/POST /alert-defaults` 读写 fallback 配置
4. 修改 `dashboard/static/app.js`：
   - Alert Mappings 页面重构为规则卡片列表
   - 增加 match / action 编辑器
   - 增加规则排序和启用/停用
5. 编写测试

## 风险评估

| 风险 | 缓解措施 |
|------|---------|
| 正则匹配性能差（复杂正则 + 大量规则） | 规则数量预期 < 50，正则预编译，无影响 |
| 旧格式 mapping 升级后丢失 service/skill 语义 | 自动迁移：`service` → `match.labels.service`，`skill` 字段忽略（由 agent 配置决定） |
| Dashboard 前端 JS 体积增大 | 规则卡片复用现有 Vue 组件和 CSS，不引入新依赖 |
| 配置写坏导致 webhook 无法匹配 | 新增 `POST /mappings` 校验：检查必填字段、正则语法合法性 |
