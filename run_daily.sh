#!/usr/bin/env bash
# Linux 서버용. crontab 예시(매일 브라질 06:00 = 한국 18:00):
#   CRON_TZ=America/Sao_Paulo
#   0 6 * * * /opt/brazil-promo-monitor/run_daily.sh >> /opt/brazil-promo-monitor/run.log 2>&1
set -euo pipefail
cd "$(dirname "$0")"
set -a; [ -f .env ] && . ./.env; set +a
[ -d .venv ] && . .venv/bin/activate
python run_daily.py "$@"
