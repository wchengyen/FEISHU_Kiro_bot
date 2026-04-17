"""会话路由 — 按用户维护 kiro session 映射"""
import json
import logging
import re
import subprocess
import threading
import time
from pathlib import Path

log = logging.getLogger("session-router")

SESSIONS_FILE = Path(__file__).parent / "user_sessions.json"
SESSION_TIMEOUT = 1800  # 30 分钟
MAX_SESSIONS_PER_USER = 20


class SessionRouter:
    def __init__(self, kiro_bin: str, kiro_agent: str = ""):
        self._kiro_bin = kiro_bin
        self._kiro_agent = kiro_agent
        self._data: dict[str, list[dict]] = {}
        self._lock = threading.Lock()
        self._load()

    # ---- 持久化 ----
    def _load(self):
        if SESSIONS_FILE.exists():
            try:
                self._data = json.loads(SESSIONS_FILE.read_text())
                log.info(f"加载 {sum(len(v) for v in self._data.values())} 个 session 映射")
            except Exception as e:
                log.warning(f"加载 session 映射失败: {e}")

    def _save(self):
        with open(SESSIONS_FILE, "w") as f:
            json.dump(self._data, f, ensure_ascii=False, indent=2)

    # ---- 路由决策 ----
    def resolve(self, user_id: str, user_text: str) -> str | None:
        """返回应 resume 的 kiro_session_id，None 表示新建"""
        with self._lock:
            sessions = self._data.get(user_id, [])
            if not sessions:
                return None
            latest = sessions[-1]
            elapsed = time.time() - latest.get("last_active", 0)
            if elapsed < SESSION_TIMEOUT:
                return latest["kiro_session_id"]
            return None

    # ---- session 注册 ----
    def register_new(self, user_id: str, topic: str) -> None:
        """新建 session 后，捕获最新 session_id 并注册"""
        sid = self._capture_latest_session_id()
        if not sid:
            log.warning("无法捕获 kiro session id")
            return
        with self._lock:
            sessions = self._data.setdefault(user_id, [])
            short_id = max((s.get("short_id", 0) for s in sessions), default=0) + 1
            sessions.append({
                "kiro_session_id": sid,
                "short_id": short_id,
                "topic": topic[:30],
                "created_at": time.time(),
                "last_active": time.time(),
                "message_count": 1,
            })
            # 保留最近 MAX_SESSIONS_PER_USER 个
            if len(sessions) > MAX_SESSIONS_PER_USER:
                self._data[user_id] = sessions[-MAX_SESSIONS_PER_USER:]
            self._save()

    def touch(self, user_id: str, session_id: str) -> None:
        """更新 session 的 last_active 和 message_count"""
        with self._lock:
            for s in self._data.get(user_id, []):
                if s["kiro_session_id"] == session_id:
                    s["last_active"] = time.time()
                    s["message_count"] = s.get("message_count", 0) + 1
                    self._save()
                    return

    # ---- 显式命令 ----
    def get_by_short_id(self, user_id: str, short_id: int) -> dict | None:
        """根据短编号查找 session"""
        for s in self._data.get(user_id, []):
            if s.get("short_id") == short_id:
                return s
        return None

    def clear_active(self, user_id: str) -> None:
        """标记当前活跃 session 为过期（/new 命令）"""
        with self._lock:
            sessions = self._data.get(user_id, [])
            if sessions:
                sessions[-1]["last_active"] = 0
                self._save()

    def list_sessions(self, user_id: str) -> str:
        """格式化输出用户的历史 sessions"""
        sessions = self._data.get(user_id, [])
        if not sessions:
            return "📭 你还没有历史会话"
        lines = ["📋 你的历史会话：\n"]
        now = time.time()
        for s in reversed(sessions[-10:]):  # 最近 10 个
            elapsed = now - s.get("last_active", 0)
            ago = self._format_elapsed(elapsed)
            count = s.get("message_count", 0)
            lines.append(f"  #{s['short_id']} | {ago} | {s['topic']} ({count}条)")
        lines.append("\n💡 回复 /resume <编号> 恢复对话")
        return "\n".join(lines)

    def get_active_label(self, user_id: str, session_id: str) -> str:
        """返回当前会话的简短标签，附在回复末尾"""
        for s in self._data.get(user_id, []):
            if s["kiro_session_id"] == session_id:
                return f"\n\n📎 会话 #{s['short_id']} {s['topic']} | /new 新对话"
        return ""

    # ---- 内部工具 ----
    def _capture_latest_session_id(self) -> str | None:
        """从 kiro-cli --list-sessions 输出中提取最新 session id"""
        try:
            cmd = [self._kiro_bin, "chat", "--list-sessions"]
            result = subprocess.run(
                cmd, capture_output=True, text=True, timeout=10,
                cwd="/home/ubuntu",
                env={"PATH": "/usr/local/bin:/usr/bin:/bin:/home/ubuntu/.local/bin",
                     "HOME": "/home/ubuntu", "NO_COLOR": "1"},
            )
            # 提取第一个 UUID（最新的 session）
            uuids = re.findall(r'[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}', result.stdout)
            return uuids[0] if uuids else None
        except Exception as e:
            log.error(f"捕获 session id 失败: {e}")
            return None

    @staticmethod
    def _format_elapsed(seconds: float) -> str:
        if seconds < 60:
            return "刚刚"
        if seconds < 3600:
            return f"{int(seconds/60)}分钟前"
        if seconds < 86400:
            return f"{int(seconds/3600)}小时前"
        return f"{int(seconds/86400)}天前"
