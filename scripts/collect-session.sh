#!/usr/bin/env bash
# Collect one full trading session of Shioaji ticks. Started by cron at 08:58 so
# the subscription is live before the 09:00 open; runs until 13:31 (past the
# closing auction). Quote data only — no order gate is ever enabled here.
set -euo pipefail
cd /home/hom/services/stock/tw-day-trading-lab

# Self-locking so the cron line stays short enough to paste without wrapping,
# and so a catch-up run exits quietly while the real collector still holds it.
exec 9>/tmp/twdtl_collect.lock
flock -n 9 || exit 0
exec >>logs/collect-session.log 2>&1
echo "--- $(date +%F' '%T) start ---"

DATE=$(date +%F)
SYMBOLS=${SYMBOLS:-2330,2317,2454,2360,6805,3481,3189,6239,3037,3450,3081,2615}
DURATION=$(( $(date -d 13:31 +%s) - $(date +%s) ))
[ "$DURATION" -gt 0 ] || { echo "$DATE past 13:31, nothing to collect"; exit 0; }

exec env PYTHONPATH=src /usr/bin/python3 -m tw_day_trading_lab.cli \
  simulate shioaji-tick-smoke \
  --date "$DATE" --symbols "$SYMBOLS" \
  --enable-tick-stream --duration-seconds "$DURATION" \
  --output "reports/$DATE-shioaji-tick-session.json"
