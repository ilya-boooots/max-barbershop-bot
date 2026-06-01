#!/bin/bash
set -e

echo "=== DEPLOY START ==="

cd /opt/cafe-bot

echo "Pulling latest code..."
git pull

echo "Installing dependencies..."
source venv/bin/activate
pip install -r requirements.txt

echo "Restarting bot..."
sudo systemctl restart cafe-bot.service

sleep 2

echo "Checking status..."
sudo systemctl status cafe-bot.service --no-pager

if ! systemctl is-active --quiet cafe-bot.service; then
  echo "Bot failed to start. Showing logs:"
  sudo journalctl -u cafe-bot.service -n 100 --no-pager
  exit 1
fi

echo "=== DEPLOY SUCCESS ==="
