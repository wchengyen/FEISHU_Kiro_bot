#!/usr/bin/env python3
"""统一发送路由，按 platform:raw_id 前缀分发到对应适配器."""
import logging

from adapters.base import PlatformAdapter

log = logging.getLogger("platform-dispatcher")


class PlatformDispatcher:
    def __init__(self):
        self._adapters: dict[str, PlatformAdapter] = {}

    def register(self, adapter: PlatformAdapter) -> None:
        self._adapters[adapter.platform] = adapter

    def _resolve(self, unified_user_id: str):
        """解析 unified_user_id 为 (adapter, raw_id, context_token)."""
        if ":" not in unified_user_id:
            log.error(f"非法的统一用户ID格式: {unified_user_id}")
            return None, None, None
        platform, raw_id = unified_user_id.split(":", 1)
        adapter = self._adapters.get(platform)
        if not adapter:
            log.error(f"未知平台: {platform}")
            return None, None, None
        ctx = None
        if platform == "weixin":
            ctx = getattr(adapter, "_context_tokens", {}).get(raw_id)
        return adapter, raw_id, ctx

    def send(self, unified_user_id: str, text: str) -> None:
        adapter, raw_id, ctx = self._resolve(unified_user_id)
        if adapter:
            adapter.send_text(raw_id, text, context_token=ctx)

    def send_image(self, unified_user_id: str, image_path: str) -> bool:
        adapter, raw_id, ctx = self._resolve(unified_user_id)
        if not adapter:
            return False
        return adapter.send_image(raw_id, image_path, context_token=ctx)

    def send_file(self, unified_user_id: str, file_path: str) -> bool:
        adapter, raw_id, ctx = self._resolve(unified_user_id)
        if not adapter:
            return False
        return adapter.send_file(raw_id, file_path, context_token=ctx)

    def get_adapter(self, platform: str) -> PlatformAdapter | None:
        return self._adapters.get(platform)
