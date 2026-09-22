#!/usr/bin/env bash
# ============================================================
#  رفع/تحديث المشروع على خادم Linux (Ubuntu/Debian)
#  الاستخدام:  bash deploy.sh
#  - يسحب آخر إصدار من GitHub
#  - يثبّت المتطلبات ويعيد تشغيل الخدمة
# ============================================================
set -euo pipefail

APP_DIR="/opt/verifier-bot"
SERVICE="verifier-bot"

echo "==> السحب من GitHub"
sudo git -C "$APP_DIR" pull --ff-only

echo "==> تحميل python-dotenv لبناء البيئة (للمرة الأولى فقط)"
if [ ! -f "$APP_DIR/.env" ]; then
  if [ -f "$APP_DIR/.env.example" ]; then
    sudo cp "$APP_DIR/.env.example" "$APP_DIR/.env"
    echo ""
    echo "!!! أنشأنا ملف .env من القالب — عدّله الآن قبل التشغيل:"
    echo "    sudo nano $APP_DIR/.env"
    echo "    ثم نفّذ:  sudo systemctl restart $SERVICE"
    exit 0
  fi
fi

echo "==> تثبيت المتطلبات"
sudo "$APP_DIR/.venv/bin/pip" install -q -r "$APP_DIR/requirements.txt"

echo "==> إعادة تشغيل الخدمة"
sudo systemctl restart "$SERVICE"

echo "==> الحالة"
sudo systemctl status "$SERVICE" --no-pager | head -n 12
echo "اكتمل ✅"