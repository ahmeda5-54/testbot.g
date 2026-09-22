#!/usr/bin/env bash
# ============================================================
#  إعداد السيرفر لأول مرة (Ubuntu/Debian) — ينفَّذ مرة واحدة
#  الاستخدام بصلاحيات root:  bash setup-server.sh
# ------------------------------------------------------------
#  ثم عدّل .env و شغّل الخدمة (انظر التعليمات في الأسفل)
# ============================================================
set -euo pipefail

REPO_URL="https://github.com/ahmeda5-54/testbot.g.git"
APP_DIR="/opt/verifier-bot"
SERVICE="verifier-bot"
PY_BIN="/opt/verifier-bot/.venv/bin/python"

echo "==> 1/6 تحديث الحزم وتثبيت الأدوات"
apt-get update -y
apt-get install -y python3-venv python3-pip git curl

echo "==> 2/6 جلب الكود من GitHub"
git clone "$REPO_URL" "$APP_DIR"

echo "==> 3/6 بيئة بايثون معزولة"
cd "$APP_DIR"
python3 -m venv .venv
"$PY_BIN" -m pip install -q -r requirements.txt

echo "==> 4/6 ملف الإعدادات"
cp .env.example .env

echo "==> 5/6 خدمة systemd"
cp deploy/verifier-bot.service /etc/systemd/system/"$SERVICE".service
systemctl daemon-reload
systemctl enable "$SERVICE"

echo "==> 6/6 تم"
echo ""
echo "!!!!!!!!!!  خطوة أخيرة مطلوبة  !!!!!!!!!!"
echo "عدّل مفاتيحك في ملف الإعدادات:"
echo "    nano $APP_DIR/.env"
echo ""
echo "  المفاتيح اللازمة: BOT_TOKEN  BOT_USERNAME  ADMIN_IDS  CHANNEL_ID"
echo "                     DASHBOARD_PASSWORD  SECRET_KEY"
echo "  مفتاح اختياري للوحدة: TB_API_ID  TB_API_HASH  TB_PHONE  TB_SESSION_NAME"
echo ""
echo "ثم شغّل الخدمة:"
echo "    systemctl start $SERVICE"
echo ""
echo "لوحة التحكم:  http://SERVER_IP:5000"
echo "فحص الأخطاء:  journalctl -u $SERVICE -f"