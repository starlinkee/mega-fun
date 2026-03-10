#!/bin/bash
# Backup bazy danych SQLite + upload na Backblaze B2 przez rclone
# Bezpieczne kopiowanie (atomowe) nawet gdy aplikacja dziala

DB_PATH="/opt/mega-fun/mega_fun.db"
BACKUP_DIR="/opt/mega-fun/backups"
DATE=$(date +%Y%m%d_%H%M%S)
BACKUP_FILE="$BACKUP_DIR/mega_fun_$DATE.db"
LOG="/var/log/mega-fun-cron.log"

mkdir -p "$BACKUP_DIR"

# --- Lokalny backup ---
sqlite3 "$DB_PATH" ".backup '$BACKUP_FILE'"

if [ $? -eq 0 ]; then
    echo "[$(date '+%Y-%m-%d %H:%M:%S')] Backup lokalny OK: $BACKUP_FILE" >> "$LOG"
else
    echo "[$(date '+%Y-%m-%d %H:%M:%S')] Backup FAILED!" >> "$LOG"
    exit 1
fi

# --- Upload na Backblaze B2 (tylko jesli rclone jest skonfigurowany) ---
if command -v rclone &> /dev/null && rclone listremotes | grep -q "b2:"; then
    rclone copy "$BACKUP_FILE" b2:mega-fun-backups/ --log-file="$LOG" --log-level INFO
    if [ $? -eq 0 ]; then
        echo "[$(date '+%Y-%m-%d %H:%M:%S')] Upload Backblaze B2 OK" >> "$LOG"
    else
        echo "[$(date '+%Y-%m-%d %H:%M:%S')] Upload Backblaze B2 FAILED!" >> "$LOG"
    fi
fi

# --- Usun lokalne backupy starsze niz 7 dni ---
find "$BACKUP_DIR" -name "*.db" -mtime +7 -delete
