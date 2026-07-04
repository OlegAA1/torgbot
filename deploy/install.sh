#!/usr/bin/env bash
# Установка torgbot как systemd-сервиса. Запускать из корня репозитория:
#   bash deploy/install.sh
set -euo pipefail

cd "$(dirname "$0")/.."
DIR="$(pwd)"
RUN_USER="$(whoami)"

[ -f .env ] || { echo "ОШИБКА: нет .env — скопируйте .env.example в .env и заполните ключи"; exit 1; }
[ -x .venv/bin/python ] || { echo "ОШИБКА: нет .venv — создайте: python3 -m venv .venv && .venv/bin/pip install -r requirements.txt"; exit 1; }

sed "s|__DIR__|$DIR|g; s|__USER__|$RUN_USER|g" deploy/torgbot.service | sudo tee /etc/systemd/system/torgbot.service >/dev/null
sudo systemctl daemon-reload
sudo systemctl enable --now torgbot

echo
echo "Сервис запущен. Полезные команды:"
echo "  systemctl status torgbot          # состояние"
echo "  journalctl -u torgbot -f          # живой лог"
echo "  sudo systemctl restart torgbot    # перезапуск (после git pull / правки config)"
echo "  sudo systemctl stop torgbot       # остановить"
