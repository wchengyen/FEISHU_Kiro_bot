#!/usr/bin/env python3
"""kiro-devops 统一入口 — 同时运行飞书、微信、Webhook 三通道."""
import logging
import os
import sys
import threading

from adapters import FeishuAdapter, WeixinAdapter
from message_handler import MessageHandler
from platform_dispatcher import PlatformDispatcher
from webhook_server import start_webhook_server

logging.basicConfig(level=logging.INFO, format="%(asctime)s [%(levelname)s] %(message)s")
log = logging.getLogger("gateway")

APP_ID = os.environ.get("FEISHU_APP_ID", "").strip()
APP_SECRET = os.environ.get("FEISHU_APP_SECRET", "").strip()
WEIXIN_BOT_TOKEN = os.environ.get("WEIXIN_BOT_TOKEN", "").strip() or None


def main():
    dispatcher = PlatformDispatcher()
    handler = MessageHandler(dispatcher=dispatcher)

    threads = []

    # 飞书适配器
    if APP_ID and APP_SECRET:
        feishu = FeishuAdapter(
            app_id=APP_ID,
            app_secret=APP_SECRET,
            on_message=handler.handle,
        )
        dispatcher.register(feishu)
        t = threading.Thread(target=feishu.start, name="feishu-ws", daemon=True)
        t.start()
        threads.append(t)
        log.info("✅ 飞书适配器已启动")
    else:
        log.warning("⚠️  FEISHU_APP_ID / FEISHU_APP_SECRET 未设置，跳过飞书")

    # 微信适配器
    weixin = WeixinAdapter(
        bot_token=WEIXIN_BOT_TOKEN,
        on_message=handler.handle,
    )
    dispatcher.register(weixin)
    t = threading.Thread(target=weixin.start, name="weixin-poll", daemon=True)
    t.start()
    threads.append(t)
    log.info("✅ 微信适配器已启动")

    # Webhook HTTP
    if os.environ.get("WEBHOOK_ENABLED", "false").lower() == "true":
        port = int(os.environ.get("WEBHOOK_PORT", "8080"))
        host = os.environ.get("WEBHOOK_HOST", "127.0.0.1")
        start_webhook_server(handler, host=host, port=port)
    else:
        log.info("🌐 Webhook 未启用")

    log.info("🚀 kiro-devops gateway 启动完成")

    # 主线程保持存活
    try:
        while True:
            for t in threads:
                t.join(timeout=1)
    except KeyboardInterrupt:
        log.info("👋 收到退出信号，正在关闭...")
        sys.exit(0)


if __name__ == "__main__":
    main()
