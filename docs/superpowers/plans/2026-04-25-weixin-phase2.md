# 微信渠道 Phase 2 方案：富媒体支持

> 基于 iLink Bot API 协议调研（Java SDK 2.1.0 / Go SDK / 官方 protocol-spec / 社区逆向文档）

---

## 一、可行性结论速览

| 功能 | 可行性 | 优先级 | 说明 |
|------|--------|--------|------|
| **图片接收** | ✅ 可行 | P0 | getupdates 返回 image_item，含 CDN 下载链接 |
| **图片发送** | ✅ 可行 | P0 | getuploadurl → AES-128-ECB 加密上传 CDN → sendmessage |
| **文件接收** | ✅ 可行 | P0 | getupdates 返回 file_item |
| **文件发送** | ✅ 可行 | P0 | 同图片流程，item type=4 |
| **视频接收** | ✅ 可行 | P1 | getupdates 返回 video_item（type=5） |
| **视频发送** | ✅ 可行 | P1 | 同图片流程，需传入 durationMs |
| **语音接收** | ✅ 可行 | P1 | getupdates 返回 voice_item（type=3），silk 编码 |
| **语音发送** | ⚠️ 复杂 | P2 | 需 silk 格式，采样率/时长参数 |
| **群聊** | ❌ 不支持 | — | 多个权威来源一致确认 iLink 仅支持私聊 |
| **输入状态** | ✅ 可行 | P2 | getconfig → sendtyping，可提升交互体验 |

---

## 二、协议细节

### 2.1 消息类型对照表（item_list[].type）

| type | 含义 | 接收字段 | 发送方式 |
|------|------|----------|----------|
| 1 | 文本 | `text_item.text` | sendmessage 直接发送 |
| 2 | 图片 | `image_item` | CDN 上传 + sendmessage |
| 3 | 语音 | `voice_item`（silk 编码） | CDN 上传 + sendmessage（需 duration/sampleRate） |
| 4 | 文件 | `file_item` | CDN 上传 + sendmessage |
| 5 | 视频 | `video_item` | CDN 上传 + sendmessage（需 durationMs） |

### 2.2 媒体发送三步流程

```
┌─────────────┐    POST /getuploadurl    ┌─────────────┐
│   Bot 本地   │ ───────────────────────>│  iLink 服务端 │
│  明文文件    │                         │              │
└─────────────┘    返回 upload_param      └─────────────┘
       │                                          │
       │  1. AES-128-ECB 加密本地文件              │
       │  2. POST CDN /upload（加密后文件）         │
       │  3. 返回 x-encrypted-param               │
       ▼                                          ▼
┌─────────────┐    POST /sendmessage      ┌─────────────┐
│   Bot 本地   │ ───────────────────────>│  iLink 服务端 │
│  item_list   │  携带 context_token +    │              │
│  [{type:2,   │  CDNMedia 引用           │              │
│   image_item}]│                         │              │
└─────────────┘                           └─────────────┘
```

### 2.3 媒体下载流程

```
getupdates 返回 msg.item_list[]:
  image_item: { url, aes_key, width, height }
  file_item:  { url, aes_key, file_name, file_size }

1. GET CDN /download（url）
2. 本地 AES-128-ECB 解密（aes_key）
3. 保存为原始文件
```

### 2.4 关键请求头

所有业务请求（含 getuploadurl / sendmessage）：
```
Content-Type: application/json
AuthorizationType: ilink_bot_token
Authorization: Bearer <bot_token>
X-WECHAT-UIN: <base64(String(random_uint32))>
```

### 2.5 base_info 版本

Java SDK 2.1.0 使用 `"channel_version": "2.0.0"`，当前项目使用 `"1.0.3"`。
建议 Phase 2 同步升级到 `"2.0.0"`。

---

## 三、群聊：明确不支持

以下权威来源一致确认 iLink Bot API **仅支持私聊（DM）**：

| 来源 | 结论 |
|------|------|
| 腾讯新闻官方分析 | "不能发群消息 (ilink 只支持 direct chat)" |
| Qwen Code 官方文档 | "微信 iLink Bot 仅支持私聊（DM）——不支持群聊" |
| yage.ai 深度分析 | "能力集中在直接消息往返，群聊运营...不在目前的覆盖范围内" |
| 51CTO 技术社区 | "不覆盖群聊运营、联系人管理、朋友圈交互这类场景" |

**虽然消息结构中有 `group_id` 字段的注释，但实际未开放使用。**

> 💡 结论：Phase 2 **不投入群聊**，等待腾讯官方后续开放。

---

## 四、实现方案

### 4.1 新增依赖

```bash
pip3 install cryptography  # AES-128-ECB 加解密
pip3 install pillow        # 图片处理（缩略图/格式转换）
```

### 4.2 adapters/weixin.py 改动

#### A. 接收媒体消息（_poll_loop）

```python
def _on_message(self, msg: dict) -> None:
    # 当前：只处理 message_type == 1（用户消息）
    # Phase 2：处理所有消息类型
    items = msg.get("item_list", [])
    for item in items:
        item_type = item.get("type")
        if item_type == 1:      # 文本
            text = item["text_item"]["text"]
            # ... 现有逻辑
        elif item_type == 2:    # 图片
            image = item.get("image_item")
            if image:
                local_path = self._download_media(image["url"], image["aes_key"])
                self._handle_image(incoming, local_path, image)
        elif item_type == 4:    # 文件
            file_info = item.get("file_item")
            if file_info:
                local_path = self._download_media(file_info["url"], file_info["aes_key"])
                self._handle_file(incoming, local_path, file_info)
        # type 3=语音, 5=视频 同理
```

#### B. 下载媒体（_download_media）

```python
def _download_media(self, url: str, aes_key: str) -> str:
    """从 CDN 下载并 AES-128-ECB 解密，返回本地临时文件路径."""
    import requests
    from cryptography.hazmat.primitives.ciphers import Cipher, algorithms, modes
    from cryptography.hazmat.backends import default_backend

    # 1. 下载加密文件
    resp = requests.get(url, timeout=60)
    encrypted = resp.content

    # 2. AES-128-ECB 解密
    key = base64.b64decode(aes_key) if aes_key else b""
    cipher = Cipher(algorithms.AES(key), modes.ECB(), backend=default_backend())
    decryptor = cipher.decryptor()
    decrypted = decryptor.update(encrypted) + decryptor.finalize()

    # 3. 去除 PKCS7 填充
    pad_len = decrypted[-1]
    decrypted = decrypted[:-pad_len]

    # 4. 保存到临时文件
    tmp_path = f"/tmp/ilink_{secrets.token_hex(8)}"
    with open(tmp_path, "wb") as f:
        f.write(decrypted)
    return tmp_path
```

#### C. 发送媒体（send_image / send_file）

```python
def send_image(self, raw_user_id: str, image_path: str, context_token: str | None = None) -> None:
    """发送图片消息."""
    # 1. 读取并加密图片
    with open(image_path, "rb") as f:
        plain = f.read()
    encrypted, aes_key = self._aes_encrypt(plain)

    # 2. 获取上传 URL
    upload_resp = self._post("getuploadurl", {"msg": {"item_list": [{"type": 2}]}})
    upload_url = upload_resp["upload_param"]["upload_url"]

    # 3. 上传加密文件到 CDN
    cdn_resp = requests.post(upload_url, data=encrypted, headers={"Content-Type": "application/octet-stream"})
    x_encrypted = cdn_resp.headers.get("x-encrypted-param")

    # 4. 发送消息
    body = {
        "msg": {
            "from_user_id": "",
            "to_user_id": raw_user_id,
            "client_id": f"kiro-{secrets.token_hex(8)}",
            "message_type": 2,
            "message_state": 2,
            "context_token": context_token,
            "item_list": [{
                "type": 2,
                "image_item": {
                    "cdn_url": upload_url,
                    "x_encrypted_param": x_encrypted,
                    "aes_key": base64.b64encode(aes_key).decode(),
                    "width": 800,   # 需从实际图片获取
                    "height": 600,
                }
            }],
        },
        "base_info": {"channel_version": "2.0.0"},
    }
    self._post("sendmessage", body)
```

### 4.3 PlatformAdapter 抽象层扩展

```python
# adapters/base.py
class PlatformAdapter(ABC):
    @abstractmethod
    def send_text(self, raw_user_id: str, text: str, context_token: str | None = None) -> None: ...

    # Phase 2 新增
    def send_image(self, raw_user_id: str, image_path: str, context_token: str | None = None) -> None:
        """默认实现：不支持图片发送的适配器可复用此 fallback."""
        log.warning(f"{self.platform} 图片发送未实现")

    def send_file(self, raw_user_id: str, file_path: str, context_token: str | None = None) -> None:
        log.warning(f"{self.platform} 文件发送未实现")
```

### 4.4 MessageHandler 改动

当前 `MessageHandler` 在 Kiro 返回结果后，调用 `_reply()` → `PlatformDispatcher.send()` → `adapter.send_text()`。

Phase 2 需要支持：Kiro 输出中包含图片/文件路径时，自动检测并调用对应平台的媒体发送。

```python
# message_handler.py 中 _reply 逻辑扩展
if incoming.platform == "weixin":
    # 微信：只发送文本（图片/文件暂不支持）
    # 或：检测到图片路径 → adapter.send_image()
    pass
```

> ⚠️ 注意：微信发送图片需要先上传 CDN，比飞书直接传 bytes 慢。如果 Kiro 输出包含多个图片，需要串行或并发上传。

### 4.5 文件清理策略

下载的媒体临时文件需要定期清理：

```python
import tempfile
import atexit

# 使用 tempfile.TemporaryDirectory
_media_tmpdir = tempfile.TemporaryDirectory(prefix="ilink_media_")
atexit.register(_media_tmpdir.cleanup)
```

---

## 五、工作量估算

| 任务 | 预估工时 | 依赖 |
|------|----------|------|
| AES-128-ECB 加解密工具模块 | 0.5d | cryptography |
| CDN 上传/下载封装 | 1d | requests |
| 图片接收 + 本地保存 | 0.5d | — |
| 图片发送（完整流程） | 1d | — |
| 文件接收 + 本地保存 | 0.5d | — |
| 文件发送 | 0d | 复用图片流程，item type 不同 |
| PlatformAdapter 扩展 send_image/send_file | 0.5d | — |
| FeishuAdapter 同步实现 send_image（当前 inline 在 reply 中） | 1d | 重构现有代码 |
| MessageHandler 自动检测媒体路径 + 平台分发 | 1d | — |
| 测试（图片/文件收发端到端） | 1d | — |
| **总计** | **~7d** | — |

---

## 六、风险与注意事项

| 风险 | 级别 | 应对 |
|------|------|------|
| AES-128-ECB 加解密参数与社区 SDK 不一致 | 中 | 参考 Java SDK 2.1.0 源码，fallback 为明文上传 |
| CDN 上传超时/失败 | 低 | 重试 3 次，失败后转为发送文本提示 |
| 大文件上传（>10MB）限制未知 | 中 | 先限制为 5MB，超限时发送文本链接 |
| silk 语音格式转换复杂 | 高 | P2 阶段再处理，或依赖用户发送语音转文字 |
| channel_version 升级影响 | 低 | 2.0.0 向后兼容，先灰度测试 |

---

## 七、建议的二期排期

**Phase 2A（2-3 天）：图片收发**
- 图片接收 + 下载解密
- 图片发送（CDN 上传流程）
- 端到端测试

**Phase 2B（2-3 天）：文件收发**
- 文件接收 + 下载解密
- 文件发送
- MessageHandler 自动检测媒体路径

**Phase 2C（可选，1-2 天）：视频 + 语音**
- 视频收发（同图片流程，需 duration）
- 语音接收（silk 格式保存，可选转码为 mp3）

**Phase 2D（可选，1 天）：输入状态**
- getconfig → sendtyping
- Kiro 处理耗时任务时显示"正在输入..."

---

## 八、参考文档

1. [微信 iLink Bot Java SDK 2.1.0](https://juejin.cn/post/7629907187461488674) — 媒体类型值、AES 加密、完整 API
2. [wechat-robot-go / iLink API Reference](https://github.com/SpellingDragon/wechat-robot-go/wiki/iLink-API-Reference) — Go SDK 富媒体发送示例
3. [weixin-bot protocol-spec](https://github.com/epiral/weixin-bot/blob/main/docs/protocol-spec.md) — 媒体流程时序图、AES-128-ECB 细节
4. [微信Bot API 技术解析](https://github.com/hao-ji-xing/openclaw-weixin/blob/main/weixin-bot-api.md) — 消息结构、context_token 机制
5. [wechatbot.dev / 协议文档](https://www.wechatbot.dev/zh/protocol) — 完整 API 列表、CDN 域名
