#!/usr/bin/env python3
"""微信 iLink 媒体工具：AES-128-ECB 加解密 + CDN 上传/下载."""

import base64
import logging
import os
import secrets
import tempfile
import urllib.request

from cryptography.hazmat.backends import default_backend
from cryptography.hazmat.primitives.ciphers import Cipher, algorithms, modes

log = logging.getLogger("weixin-media")
CDN_BASE_URL = "https://novac2c.cdn.weixin.qq.com/c2c"


def _pad_pkcs7(data: bytes, block_size: int = 16) -> bytes:
    pad_len = block_size - (len(data) % block_size)
    return data + bytes([pad_len] * pad_len)


def _unpad_pkcs7(data: bytes) -> bytes:
    pad_len = data[-1]
    if pad_len > 16:
        raise ValueError(f"Invalid PKCS7 padding length: {pad_len}")
    return data[:-pad_len]


def aes_encrypt(plain: bytes, key: bytes | None = None) -> tuple[bytes, bytes]:
    """AES-128-ECB 加密，返回 (encrypted_bytes, key_bytes)."""
    if key is None:
        key = os.urandom(16)
    cipher = Cipher(algorithms.AES(key), modes.ECB(), backend=default_backend())
    encryptor = cipher.encryptor()
    padded = _pad_pkcs7(plain)
    encrypted = encryptor.update(padded) + encryptor.finalize()
    return encrypted, key


def aes_decrypt(encrypted: bytes, key: bytes) -> bytes:
    """AES-128-ECB 解密，返回明文 bytes."""
    cipher = Cipher(algorithms.AES(key), modes.ECB(), backend=default_backend())
    decryptor = cipher.decryptor()
    decrypted = decryptor.update(encrypted) + decryptor.finalize()
    return _unpad_pkcs7(decrypted)


def download_media(url: str, aes_key_b64: str | None, timeout: int = 60) -> bytes:
    """从 CDN 下载并解密媒体文件，返回明文 bytes."""
    req = urllib.request.Request(url, method="GET")
    with urllib.request.urlopen(req, timeout=timeout) as resp:
        encrypted = resp.read()

    if not aes_key_b64:
        log.warning("下载媒体缺少 aes_key，返回原始内容")
        return encrypted

    key = base64.b64decode(aes_key_b64)
    if len(key) not in (16, 24, 32):
        raise ValueError(f"AES key 长度异常: {len(key)} bytes")

    return aes_decrypt(encrypted, key)


def upload_media(upload_url: str, encrypted_data: bytes, timeout: int = 60) -> str:
    """上传加密后的媒体到 CDN，返回 x-encrypted-param."""
    req = urllib.request.Request(
        upload_url,
        data=encrypted_data,
        headers={"Content-Type": "application/octet-stream"},
        method="POST",
    )
    with urllib.request.urlopen(req, timeout=timeout) as resp:
        x_encrypted = resp.headers.get("x-encrypted-param", "")
        if not x_encrypted:
            # 某些情况下 header 名可能是小写
            x_encrypted = resp.headers.get("X-Encrypted-Param", "")
    return x_encrypted


def save_media_to_temp(data: bytes, suffix: str = "") -> str:
    """保存媒体数据到临时文件，返回路径."""
    fd, path = tempfile.mkstemp(prefix="ilink_", suffix=suffix)
    with os.fdopen(fd, "wb") as f:
        f.write(data)
    return path


def get_image_dimensions(path: str) -> tuple[int, int]:
    """获取图片尺寸，返回 (width, height)."""
    try:
        from PIL import Image
        with Image.open(path) as img:
            return img.size
    except Exception as e:
        log.warning(f"获取图片尺寸失败: {e}")
        return (800, 600)
