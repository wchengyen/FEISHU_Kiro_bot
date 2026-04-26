#!/usr/bin/env python3
"""微信 iLink 媒体工具测试：AES-128-ECB 加解密 + 填充."""
import os
import tempfile

import pytest

from adapters.weixin_media import (
    aes_encrypt,
    aes_decrypt,
    _pad_pkcs7,
    _unpad_pkcs7,
    save_media_to_temp,
    get_image_dimensions,
)


class TestPkcs7:
    def test_pad_exact_block(self):
        data = b"A" * 16
        padded = _pad_pkcs7(data)
        assert len(padded) == 32  # 满块时加一整块填充
        assert padded[-1] == 16

    def test_pad_partial_block(self):
        data = b"Hello"
        padded = _pad_pkcs7(data)
        assert len(padded) == 16
        assert padded[-1] == 11

    def test_unpad_roundtrip(self):
        for text in [b"", b"x", b"Hello World!!!", b"A" * 16, b"A" * 31]:
            padded = _pad_pkcs7(text)
            assert _unpad_pkcs7(padded) == text


class TestAesEcb:
    def test_encrypt_decrypt_roundtrip(self):
        plain = b"Hello iLink media encryption!"
        encrypted, key = aes_encrypt(plain)
        assert len(key) == 16
        assert encrypted != plain
        decrypted = aes_decrypt(encrypted, key)
        assert decrypted == plain

    def test_encrypt_with_custom_key(self):
        plain = b"Test with custom key"
        key = os.urandom(16)
        encrypted, returned_key = aes_encrypt(plain, key)
        assert returned_key == key
        assert aes_decrypt(encrypted, key) == plain

    def test_large_file_roundtrip(self):
        plain = os.urandom(1024 * 64)  # 64KB
        encrypted, key = aes_encrypt(plain)
        assert aes_decrypt(encrypted, key) == plain


class TestTempFile:
    def test_save_media_to_temp(self):
        data = b"test image bytes"
        path = save_media_to_temp(data, suffix=".jpg")
        assert os.path.exists(path)
        with open(path, "rb") as f:
            assert f.read() == data
        os.unlink(path)


class TestImageDimensions:
    def test_get_image_dimensions_with_pil(self):
        pytest.importorskip("PIL", reason="Pillow not installed")
        from PIL import Image
        img = Image.new("RGB", (120, 80), color="red")
        fd, path = tempfile.mkstemp(suffix=".png")
        os.close(fd)
        img.save(path)
        w, h = get_image_dimensions(path)
        assert (w, h) == (120, 80)
        os.unlink(path)

    def test_get_image_dimensions_without_pil(self):
        """没有 Pillow 时返回默认尺寸."""
        fd, path = tempfile.mkstemp(suffix=".bin")
        os.close(fd)
        w, h = get_image_dimensions(path)
        assert (w, h) == (800, 600)
        os.unlink(path)
