# INSTALL PLAYBOOK (Ubuntu, 15–30 min) ⚙️

## Target structure
- Repo: `/opt/bots/<bot-name>`
- Venv: `/opt/bots/<bot-name>/.venv`
- Env: `/opt/bots/<bot-name>/.env`
- Service: `telegram-bot@<bot-name>.service`

## 1) Clone / update code
```bash
sudo mkdir -p /opt/bots
cd /opt/bots
sudo git clone <REPO_URL> <bot-name>
# or update existing
cd /opt/bots/<bot-name>
sudo git pull
```

## 2) Create virtualenv + install
```bash
cd /opt/bots/<bot-name>
python3 -m venv .venv
source .venv/bin/activate
pip install --upgrade pip
pip install -r requirements.txt
```

## 3) Configure environment
```bash
cd /opt/bots/<bot-name>
cp .env.save .env
nano .env
```

Минимум заполнить:
- `BOT_TOKEN`
- `PROTECTED_DEV_TG_ID`
- `DB_PATH=/opt/bots/<bot-name>/app/db.sqlite3`
- `BUSINESS_NAME`, `SUPPORT_CONTACT`, `BUSINESS_ADDRESS`, `BUSINESS_PHONE`

## 4) systemd template
Создайте `/etc/systemd/system/telegram-bot@.service`:
```ini
[Unit]
Description=Telegram Bot (%i)
After=network.target

[Service]
Type=simple
WorkingDirectory=/opt/bots/%i
EnvironmentFile=/opt/bots/%i/.env
ExecStart=/opt/bots/%i/.venv/bin/python -m app.main
Restart=always
RestartSec=3
User=www-data
Group=www-data

[Install]
WantedBy=multi-user.target
```

## 5) Restart service
```bash
sudo systemctl daemon-reload
sudo systemctl enable telegram-bot@<bot-name>
sudo systemctl restart telegram-bot@<bot-name>
```

## 6) Check status
```bash
sudo systemctl status telegram-bot@<bot-name> --no-pager
```

## 7) Check logs
```bash
sudo journalctl -u telegram-bot@<bot-name>.service -f --no-pager
sudo journalctl -u telegram-bot@<bot-name>.service -n 200 --no-pager
```

## Common mistakes ❗
1. **Wrong path**: repo не в `/opt/bots/<bot-name>`.
2. **Wrong .env format**: `KEY=value` only, без пробелов вокруг `=`.
3. **Wrong startup module**: нужно только `python -m app.main`.
4. **Forgot pip install** после `git pull`.
5. **Wrong service name**: путают `telegram-bot@<bot-name>.service`.
