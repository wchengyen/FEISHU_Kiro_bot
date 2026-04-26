#!/usr/bin/env python3
"""微信 iLink Bot API 适配器."""
import base64
import hashlib
import json
import logging
import os
import secrets
import struct
import time
import urllib.request
import urllib.error
from typing import Callable

import qrcode

from .base import PlatformAdapter, IncomingMessage, OutgoingPayload
from .weixin_media import (
    aes_encrypt,
    download_media,
    upload_media,
    save_media_to_temp,
    get_image_dimensions,
)

log = logging.getLogger("adapter-weixin")
DEFAULT_BASE_URL = "https://ilinkai.weixin.qq.com"
TOKEN_FILE = os.path.expanduser("~/.kiro/weixin_token.json")


def _random_uin() -> str:
    return base64.b64encode(str(struct.unpack(">I", os.urandom(4))[0]).encode()).decode()


def _headers(token: str | None = None) -> dict:
    h = {
        "Content-Type": "application/json",
        "AuthorizationType": "ilink_bot_token",
        "X-WECHAT-UIN": _random_uin(),
    }
    if token:
        h["Authorization"] = f"Bearer {token}"
    return h


def _get(url: str, headers: dict | None = None, timeout: int = 35) -> dict:
    req = urllib.request.Request(url, headers=headers or {}, method="GET")
    with urllib.request.urlopen(req, timeout=timeout) as resp:
        return json.loads(resp.read().decode("utf-8"))


def _post(path: str, base_url: str, token: str, body: dict, timeout: int = 40, channel_version: str = "2.0.0") -> dict:
    url = base_url.rstrip("/") + "/" + path.lstrip("/")
    data = json.dumps({**body, "base_info": {"channel_version": channel_version}}, ensure_ascii=False).encode()
    req = urllib.request.Request(url, data=data, headers=_headers(token), method="POST")
    with urllib.request.urlopen(req, timeout=timeout) as resp:
        return json.loads(resp.read().decode("utf-8"))


def _split_text(text: str, limit: int = 2000) -> list[str]:
    if len(text) <= limit:
        return [text]
    chunks = []
    while text:
        if len(text) <= limit:
            chunks.append(text)
            break
        cut = text.rfind("\n", 0, limit)
        if cut <= 0:
            cut = limit
        chunks.append(text[:cut])
        text = text[cut:].lstrip("\n")
    return chunks


class WeixinAdapter(PlatformAdapter):
    platform = "weixin"

    def __init__(self, bot_token: str | None, on_message: Callable[[IncomingMessage], None]):
        self.bot_token = bot_token
        self.base_url = DEFAULT_BASE_URL
        self.on_message = on_message
        self._get_updates_buf = ""
        self._context_tokens: dict[str, str] = {}
        self._running = False
        self._load_token()

    def _load_token(self) -> None:
        if self.bot_token:
            return
        if os.path.exists(TOKEN_FILE):
            with open(TOKEN_FILE) as f:
                data = json.load(f)
            self.bot_token = data.get("bot_token")
            self.base_url = data.get("base_url", DEFAULT_BASE_URL)
            log.info("已从本地文件加载微信 token")

    def _save_token(self) -> None:
        os.makedirs(os.path.dirname(TOKEN_FILE), exist_ok=True)
        with open(TOKEN_FILE, "w") as f:
            json.dump({"bot_token": self.bot_token, "base_url": self.base_url}, f)

    def _qr_login(self) -> None:
        log.info("=== 微信扫码登录 ===")
        base = self.base_url.rstrip("/") + "/"
        qr_resp = _get(base + "ilink/bot/get_bot_qrcode?bot_type=3")
        qrcode_id = qr_resp.get("qrcode")
        qrcode_url = qr_resp.get("qrcode_img_content")
        print(f"\n📱 请用微信扫描下方二维码登录 Bot:\n", flush=True)
        try:
            qr = qrcode.QRCode(border=1)
            qr.add_data(qrcode_url)
            qr.print_ascii(invert=True)
        except Exception as e:
            log.warning(f"打印二维码失败: {e}")
        print(f"\n或复制链接到浏览器: {qrcode_url}\n", flush=True)

        poll_url = base + f"ilink/bot/get_qrcode_status?qrcode={qrcode_id}"
        deadline = time.time() + 480
        headers = {"iLink-App-ClientVersion": "1"}

        while time.time() < deadline:
            try:
                status = _get(poll_url, headers)
            except Exception as e:
                log.warning(f"轮询错误: {e}")
                time.sleep(2)
                continue

            st = status.get("status", "wait")
            if st == "wait":
                print(".", end="", flush=True)
            elif st == "scaned":
                print("\n👀 已扫码，请在微信中点击确认...", flush=True)
            elif st == "confirmed":
                self.bot_token = status.get("bot_token")
                self.base_url = status.get("baseurl", DEFAULT_BASE_URL)
                self._save_token()
                print(f"\n✅ 微信登录成功！", flush=True)
                return
            elif st == "expired":
                raise RuntimeError("二维码已过期，请重新运行程序。")
            time.sleep(1)
        raise RuntimeError("登录超时（8分钟），请重试。")

    def start(self) -> None:
        if not self.bot_token:
            self._qr_login()
        self._running = True
        log.info("🚀 微信适配器启动（iLink 长轮询）")
        self._poll_loop()

    def _poll_loop(self) -> None:
        consecutive_errors = 0
        while self._running:
            try:
                resp = _post(
                    "ilink/bot/getupdates",
                    self.base_url,
                    self.bot_token,
                    {"get_updates_buf": self._get_updates_buf}
                )
                consecutive_errors = 0

                if resp.get("ret", 0) != 0:
                    err = resp.get("errcode")
                    if err == -14:
                        log.warning("微信 session 过期，重新登录...")
                        self._qr_login()
                        continue
                    log.warning(f"getupdates 返回错误: {resp}")
                    time.sleep(5)
                    continue

                self._get_updates_buf = resp.get("get_updates_buf", self._get_updates_buf)
                msgs = resp.get("msgs") or []
                for msg in msgs:
                    self._handle_incoming(msg)

            except urllib.error.HTTPError as e:
                consecutive_errors += 1
                log.warning(f"HTTP 错误 ({consecutive_errors}/3): {e.code}")
                if consecutive_errors >= 3:
                    log.error("连续 3 次错误，暂停 30 秒后重试")
                    time.sleep(30)
                    consecutive_errors = 0
                else:
                    time.sleep(5)
            except Exception as e:
                log.exception("微信轮询异常")
                time.sleep(10)

    def _handle_incoming(self, msg: dict) -> None:
        if msg.get("message_type") != 1:  # 只处理用户消息
            return
        from_user = msg.get("from_user_id", "")
        context_token = msg.get("context_token", "")
        item_types = [i.get("type") for i in msg.get("item_list", [])]
        log.info(f"[DEBUG] 收到消息 from={from_user}, item_types={item_types}")
        if context_token:
            self._context_tokens[from_user] = context_token

        text = ""
        images: list[str] = []
        files: list[str] = []

        items = msg.get("item_list") or []
        for item in items:
            item_type = item.get("type")
            if item_type == 1:
                text = item.get("text_item", {}).get("text", "")
            elif item_type == 2:
                # 图片接收已禁用，保持文字沟通
                log.info(f"[DEBUG] 收到图片消息 from={from_user}，已跳过（当前仅支持文字）")
            elif item_type == 4:
                # 文件接收已禁用，保持文字沟通
                log.info(f"[DEBUG] 收到文件消息 from={from_user}，已跳过（当前仅支持文字）")

        # 有图片/文件 item（即使被跳过）也要传递给 message_handler，让它回复提示语
        has_skipped_media = any(i.get("type") in (2, 4) for i in items)
        if not text and not images and not files and not has_skipped_media:
            return

        incoming = IncomingMessage(
            platform="weixin",
            raw_user_id=from_user,
            unified_user_id=f"weixin:{from_user}",
            message_id=msg.get("client_id", "") or str(time.time()),
            text=text.strip(),
            chat_type="private",
            is_at_me=False,
            context_token=context_token,
            raw=msg,
            images=images,
            files=files,
        )
        self.on_message(incoming)

    def send_text(self, raw_user_id: str, text: str, context_token: str | None = None) -> None:
        ctx = context_token or self._context_tokens.get(raw_user_id)
        if not ctx:
            log.error(f"无法主动推送给 {raw_user_id}：缺少 context_token")
            return
        chunks = _split_text(text, 2000)
        for chunk in chunks:
            body = {
                "msg": {
                    "from_user_id": "",
                    "to_user_id": raw_user_id,
                    "client_id": f"kiro-{secrets.token_hex(8)}",
                    "message_type": 2,
                    "message_state": 2,
                    "context_token": ctx,
                    "item_list": [{"type": 1, "text_item": {"text": chunk}}],
                }
            }
            try:
                resp = _post("ilink/bot/sendmessage", self.base_url, self.bot_token, body)
                if resp.get("ret", 0) != 0:
                    log.error(f"微信发送失败: {resp}")
            except Exception as e:
                log.error(f"微信发送异常: {e}")

    def reply(self, incoming: IncomingMessage, payload: OutgoingPayload) -> None:
        self.send_text(incoming.raw_user_id, payload.text, incoming.context_token)
        for img_path in payload.images:
            self.send_image(incoming.raw_user_id, img_path, incoming.context_token)
        for file_path in payload.files:
            self.send_file(incoming.raw_user_id, file_path, incoming.context_token)

    def send_image(self, raw_user_id: str, image_path: str, context_token: str | None = None) -> bool:
        ctx = context_token or self._context_tokens.get(raw_user_id)
        if not ctx:
            log.error(f"无法发送图片给 {raw_user_id}：缺少 context_token")
            return False

        try:
            # 1. 读取并加密图片
            with open(image_path, "rb") as f:
                plain = f.read()
            encrypted, aes_key = aes_encrypt(plain)

            # 2. 计算文件元数据
            rawsize = len(plain)
            rawfilemd5 = hashlib.md5(plain).hexdigest()
            filesize = len(encrypted)
            filekey = secrets.token_hex(16)
            aeskey_hex = aes_key.hex()

            # 3. 获取上传参数
            upload_resp = _post(
                "ilink/bot/getuploadurl",
                self.base_url,
                self.bot_token,
                {
                    "filekey": filekey,
                    "media_type": 1,  # IMAGE
                    "to_user_id": raw_user_id,
                    "rawsize": rawsize,
                    "rawfilemd5": rawfilemd5,
                    "filesize": filesize,
                    "no_need_thumb": True,
                    "aeskey": aeskey_hex,
                },
            )
            if upload_resp.get("ret", 0) != 0:
                log.error(f"getuploadurl 失败: {upload_resp}")
                return False

            upload_param = upload_resp.get("upload_param", "")
            if not upload_param:
                log.error("getuploadurl 未返回 upload_param")
                return False

            # 4. 上传加密文件到 CDN
            x_encrypted = upload_media(upload_param, filekey, encrypted)

            # 5. 获取图片尺寸
            width, height = get_image_dimensions(image_path)

            # 6. 发送消息
            body = {
                "msg": {
                    "from_user_id": "",
                    "to_user_id": raw_user_id,
                    "client_id": f"kiro-{secrets.token_hex(8)}",
                    "message_type": 2,
                    "message_state": 2,
                    "context_token": ctx,
                    "item_list": [{
                        "type": 2,
                        "image_item": {
                            "media": {
                                "encrypt_query_param": x_encrypted,
                                "aes_key": base64.b64encode(aeskey_hex.encode()).decode(),
                                "encrypt_type": 1,
                            },
                            "mid_size": filesize,
                        }
                    }],
                }
            }
            resp = _post("ilink/bot/sendmessage", self.base_url, self.bot_token, body)
            if resp.get("ret", 0) != 0:
                log.error(f"微信发送图片失败: {resp}")
                return False
            log.info(f"微信图片发送成功: {image_path}")
            return True

        except Exception as e:
            log.exception(f"微信发送图片异常: {e}")
            return False

    def send_file(self, raw_user_id: str, file_path: str, context_token: str | None = None) -> bool:
        ctx = context_token or self._context_tokens.get(raw_user_id)
        if not ctx:
            log.error(f"无法发送文件给 {raw_user_id}：缺少 context_token")
            return False

        try:
            with open(file_path, "rb") as f:
                plain = f.read()
            encrypted, aes_key = aes_encrypt(plain)

            rawsize = len(plain)
            rawfilemd5 = hashlib.md5(plain).hexdigest()
            filesize = len(encrypted)
            filekey = secrets.token_hex(16)
            aeskey_hex = aes_key.hex()

            upload_resp = _post(
                "ilink/bot/getuploadurl",
                self.base_url,
                self.bot_token,
                {
                    "filekey": filekey,
                    "media_type": 3,  # FILE
                    "to_user_id": raw_user_id,
                    "rawsize": rawsize,
                    "rawfilemd5": rawfilemd5,
                    "filesize": filesize,
                    "aeskey": aeskey_hex,
                },
            )
            if upload_resp.get("ret", 0) != 0:
                log.error(f"getuploadurl 失败: {upload_resp}")
                return False

            upload_param = upload_resp.get("upload_param", "")
            if not upload_param:
                log.error("getuploadurl 未返回 upload_param")
                return False

            x_encrypted = upload_media(upload_param, filekey, encrypted)
            file_name = os.path.basename(file_path)

            body = {
                "msg": {
                    "from_user_id": "",
                    "to_user_id": raw_user_id,
                    "client_id": f"kiro-{secrets.token_hex(8)}",
                    "message_type": 2,
                    "message_state": 2,
                    "context_token": ctx,
                    "item_list": [{
                        "type": 4,
                        "file_item": {
                            "media": {
                                "encrypt_query_param": x_encrypted,
                                "aes_key": base64.b64encode(aeskey_hex.encode()).decode(),
                                "encrypt_type": 1,
                            },
                            "file_name": file_name,
                            "file_size": rawsize,
                        }
                    }],
                }
            }
            resp = _post("ilink/bot/sendmessage", self.base_url, self.bot_token, body)
            if resp.get("ret", 0) != 0:
                log.error(f"微信发送文件失败: {resp}")
                return False
            log.info(f"微信文件发送成功: {file_path}")
            return True

        except Exception as e:
            log.exception(f"微信发送文件异常: {e}")
            return False

    def upload_image(self, path: str) -> str | None:
        """微信没有独立的 upload_image 返回 media_key 的概念，
        图片发送是 upload + sendmessage 原子操作。
        此方法返回一个内部标识，供上层统一接口使用。"""
        return path

    def upload_file(self, path: str) -> str | None:
        return path
