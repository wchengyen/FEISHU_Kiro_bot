#!/usr/bin/env python3
"""微信 Phase 2A 集成测试：图片/文件接收与发送."""
import os
import tempfile
from unittest.mock import MagicMock, patch

import pytest

from adapters.base import IncomingMessage, OutgoingPayload
from adapters.weixin import WeixinAdapter
from platform_dispatcher import PlatformDispatcher


class TestWeixinAdapterMediaReceive:
    """测试微信适配器接收媒体消息."""

    def test_handle_incoming_image(self):
        adapter = WeixinAdapter(bot_token="test", on_message=MagicMock())
        adapter._context_tokens["user1@im.wechat"] = "ctx123"

        mock_image_data = b"fake_image_bytes"
        with patch("adapters.weixin.download_media", return_value=mock_image_data):
            with patch("adapters.weixin.save_media_to_temp", return_value="/tmp/ilink_abc.jpg"):
                msg = {
                    "message_type": 1,
                    "from_user_id": "user1@im.wechat",
                    "context_token": "ctx123",
                    "client_id": "msg-001",
                    "item_list": [
                        {"type": 2, "image_item": {"url": "https://cdn.test/img", "aes_key": "dGVzdA=="}}
                    ],
                }
                adapter._handle_incoming(msg)

        call_args = adapter.on_message.call_args[0][0]
        assert isinstance(call_args, IncomingMessage)
        assert call_args.images == ["/tmp/ilink_abc.jpg"]
        assert call_args.text == ""
        assert call_args.platform == "weixin"

    def test_handle_incoming_text_with_image(self):
        adapter = WeixinAdapter(bot_token="test", on_message=MagicMock())
        adapter._context_tokens["user1@im.wechat"] = "ctx123"

        mock_image_data = b"fake_image_bytes"
        with patch("adapters.weixin.download_media", return_value=mock_image_data):
            with patch("adapters.weixin.save_media_to_temp", return_value="/tmp/ilink_abc.jpg"):
                msg = {
                    "message_type": 1,
                    "from_user_id": "user1@im.wechat",
                    "context_token": "ctx123",
                    "client_id": "msg-002",
                    "item_list": [
                        {"type": 1, "text_item": {"text": "看看这张图"}},
                        {"type": 2, "image_item": {"url": "https://cdn.test/img", "aes_key": "dGVzdA=="}},
                    ],
                }
                adapter._handle_incoming(msg)

        call_args = adapter.on_message.call_args[0][0]
        assert call_args.text == "看看这张图"
        assert call_args.images == ["/tmp/ilink_abc.jpg"]

    def test_handle_incoming_file(self):
        adapter = WeixinAdapter(bot_token="test", on_message=MagicMock())
        adapter._context_tokens["user1@im.wechat"] = "ctx123"

        mock_file_data = b"fake_pdf_bytes"
        with patch("adapters.weixin.download_media", return_value=mock_file_data):
            with patch("adapters.weixin.save_media_to_temp", return_value="/tmp/ilink_doc.pdf"):
                msg = {
                    "message_type": 1,
                    "from_user_id": "user1@im.wechat",
                    "context_token": "ctx123",
                    "client_id": "msg-003",
                    "item_list": [
                        {"type": 4, "file_item": {"url": "https://cdn.test/file", "aes_key": "dGVzdA==", "file_name": "report.pdf"}}
                    ],
                }
                adapter._handle_incoming(msg)

        call_args = adapter.on_message.call_args[0][0]
        assert call_args.files == ["/tmp/ilink_doc.pdf"]

    def test_handle_incoming_non_user_message_ignored(self):
        adapter = WeixinAdapter(bot_token="test", on_message=MagicMock())
        msg = {"message_type": 2, "from_user_id": "user1@im.wechat"}
        adapter._handle_incoming(msg)
        adapter.on_message.assert_not_called()


class TestWeixinAdapterSendImage:
    """测试微信适配器发送图片."""

    def test_send_image_no_context_token(self):
        adapter = WeixinAdapter(bot_token="test", on_message=MagicMock())
        result = adapter.send_image("user1@im.wechat", "/tmp/test.jpg")
        assert result is False

    def test_send_image_success(self):
        adapter = WeixinAdapter(bot_token="test", on_message=MagicMock())
        adapter._context_tokens["user1@im.wechat"] = "ctx123"
        adapter.base_url = "https://test.weixin.qq.com"

        fd, tmp_path = tempfile.mkstemp(suffix=".jpg")
        os.write(fd, b"fake_image")
        os.close(fd)

        with patch("adapters.weixin.aes_encrypt", return_value=(b"encrypted", b"key" * 4)):
            with patch("adapters.weixin._post") as mock_post:
                with patch("adapters.weixin.upload_media", return_value="enc_param_xyz"):
                    with patch("adapters.weixin.get_image_dimensions", return_value=(100, 200)):
                        mock_post.side_effect = [
                            {"ret": 0, "upload_param": {"upload_url": "https://cdn.test/up"}},
                            {"ret": 0},
                        ]
                        result = adapter.send_image("user1@im.wechat", tmp_path)

        assert result is True
        assert mock_post.call_count == 2
        # 第二次调用是 sendmessage
        call_body = mock_post.call_args_list[1][0][3]
        msg = call_body["msg"]
        assert msg["to_user_id"] == "user1@im.wechat"
        assert msg["context_token"] == "ctx123"
        assert len(msg["item_list"]) == 1
        assert msg["item_list"][0]["type"] == 2
        assert msg["item_list"][0]["image_item"]["width"] == 100
        assert msg["item_list"][0]["image_item"]["height"] == 200
        os.unlink(tmp_path)

    def test_send_file_success(self):
        adapter = WeixinAdapter(bot_token="test", on_message=MagicMock())
        adapter._context_tokens["user1@im.wechat"] = "ctx123"
        adapter.base_url = "https://test.weixin.qq.com"

        fd, tmp_path = tempfile.mkstemp(suffix=".pdf")
        os.write(fd, b"fake_pdf")
        os.close(fd)

        with patch("adapters.weixin.aes_encrypt", return_value=(b"encrypted", b"key" * 4)):
            with patch("adapters.weixin._post") as mock_post:
                with patch("adapters.weixin.upload_media", return_value="enc_param_xyz"):
                    mock_post.side_effect = [
                        {"ret": 0, "upload_param": {"upload_url": "https://cdn.test/up"}},
                        {"ret": 0},
                    ]
                    result = adapter.send_file("user1@im.wechat", tmp_path)

        assert result is True
        call_body = mock_post.call_args_list[1][0][3]
        msg = call_body["msg"]
        assert msg["item_list"][0]["type"] == 4
        assert msg["item_list"][0]["file_item"]["file_name"] == os.path.basename(tmp_path)
        os.unlink(tmp_path)


class TestPlatformDispatcherMedia:
    """测试 PlatformDispatcher 媒体路由."""

    def test_send_image_routes_to_adapter(self):
        dispatcher = PlatformDispatcher()
        mock_adapter = MagicMock()
        mock_adapter.platform = "weixin"
        mock_adapter._context_tokens = {"user1": "ctx123"}
        dispatcher.register(mock_adapter)

        dispatcher.send_image("weixin:user1", "/tmp/test.jpg")
        mock_adapter.send_image.assert_called_once_with("user1", "/tmp/test.jpg", context_token="ctx123")

    def test_send_file_routes_to_adapter(self):
        dispatcher = PlatformDispatcher()
        mock_adapter = MagicMock()
        mock_adapter.platform = "feishu"
        dispatcher.register(mock_adapter)

        dispatcher.send_file("feishu:ou_xxx", "/tmp/test.pdf")
        mock_adapter.send_file.assert_called_once_with("ou_xxx", "/tmp/test.pdf", context_token=None)

    def test_send_image_unknown_platform_returns_false(self):
        dispatcher = PlatformDispatcher()
        result = dispatcher.send_image("unknown:user", "/tmp/test.jpg")
        assert result is False
