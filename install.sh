#!/bin/bash
# 生成 systemd service 文件并安装
set -e

SCRIPT_DIR="$(cd "$(dirname "$0")" && pwd)"
SERVICE_NAME="feishu-kiro-bot"

echo "📦 生成 ${SERVICE_NAME}.service ..."
sed -e "s|__USER__|$(whoami)|g" \
    -e "s|__INSTALL_DIR__|${SCRIPT_DIR}|g" \
    "${SCRIPT_DIR}/${SERVICE_NAME}.service.template" > "${SCRIPT_DIR}/${SERVICE_NAME}.service"

echo "📋 安装到 systemd ..."
sudo cp "${SCRIPT_DIR}/${SERVICE_NAME}.service" /etc/systemd/system/
sudo systemctl daemon-reload
sudo systemctl enable "${SERVICE_NAME}"

echo "✅ 安装完成。使用以下命令管理服务："
echo "   sudo systemctl start ${SERVICE_NAME}"
echo "   sudo systemctl restart ${SERVICE_NAME}"
echo "   sudo systemctl status ${SERVICE_NAME}"
