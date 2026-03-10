# Mega-Fun

Aplikacja webowa do scrapowania danych z Google Maps, zbierania emaili i wysyłania kampanii emailowych.

## Stack

- Python + Flask + SQLite
- Nginx + systemd (VPS)
- SMTP: Purelymail

## Uruchomienie lokalnie

```bash
python -m venv venv
source venv/bin/activate   # Windows: venv\Scripts\activate
pip install -r requirements.txt
python init_db.py
python run.py
```

Aplikacja dostępna na `http://localhost:5000`.

## Deploy

Push na branch `main` automatycznie deployuje na VPS przez GitHub Actions (`.github/workflows/deploy.yml`).

Wymagane sekrety w repozytorium:
- `VPS_HOST` — adres IP serwera
- `VPS_USER` — użytkownik SSH
- `VPS_PASSWORD` — hasło SSH

## Wysyłka kampanii — Cron

Wysyłka emaili odbywa się przez skrypt `scripts/send_campaign.py`, uruchamiany cyklicznie przez cron.

### Konfiguracja harmonogramu

Harmonogram crona znajduje się w pliku **`deploy/crontab`**. Edytuj go lokalnie i zrób push — deploy automatycznie zaktualizuje cron na VPS.

**Co minutę (domyślnie):**
```
* * * * * cd /opt/mega-fun && /opt/mega-fun/venv/bin/python scripts/send_campaign.py >> /var/log/mega-fun-cron.log 2>&1
```

**Co 30 sekund** (cron nie obsługuje interwałów poniżej minuty, używamy trick z `sleep`):
```
* * * * * cd /opt/mega-fun && /opt/mega-fun/venv/bin/python scripts/send_campaign.py >> /var/log/mega-fun-cron.log 2>&1
* * * * * sleep 30 && cd /opt/mega-fun && /opt/mega-fun/venv/bin/python scripts/send_campaign.py >> /var/log/mega-fun-cron.log 2>&1
```

Gotowe wersje są już jako komentarze w `deploy/crontab` — wystarczy odkomentować odpowiednie linie.

### Logi crona

Na VPS logi wysyłki dostępne w:
```bash
tail -f /var/log/mega-fun-cron.log
```

## Backup bazy danych

Baza danych (`mega_fun.db`) jest automatycznie backupowana co noc o **3:00** przez skrypt `scripts/backup_db.sh`.

Skrypt robi dwie rzeczy:
1. Lokalny backup na VPS (ostatnie 7 dni)
2. Upload na Backblaze B2 przez rclone (jeśli skonfigurowany)

**Lokalizacja lokalnych backupów na VPS:**
```
/opt/mega-fun/backups/
```

Pliki nazywają się `mega_fun_YYYYMMDD_HHMMSS.db`. Backupy starsze niż 7 dni są automatycznie usuwane.

### Konfiguracja Backblaze B2 (jednorazowo na VPS)

> Bez tego backupy są tylko lokalne — jeśli serwer padnie, dane przepadają.

**1. Utwórz konto i bucket na backblaze.com**
- Zarejestruj się na [backblaze.com](https://www.backblaze.com) (free 10GB)
- Przejdź do **B2 Cloud Storage → Buckets → Create a Bucket**
- Nazwa bucketu: `mega-fun-backups`, ustawienia prywatne

**2. Wygeneruj klucz API**
- Przejdź do **Account → App Keys → Add a New Application Key**
- Nazwa klucza: `mega-fun-vps`
- Uprawnienia: `Read and Write` dla bucketu `mega-fun-backups`
- Zapisz `keyID` i `applicationKey` — zobaczysz je tylko raz!

**3. Skonfiguruj rclone na VPS** (`setup_vps.sh` instaluje rclone automatycznie)

```bash
rclone config create b2 b2 account YOUR_KEY_ID key YOUR_APPLICATION_KEY
```

```bash
# Sprawdz czy dziala
rclone lsd b2:

# Przetestuj backup z uploadem
bash /opt/mega-fun/scripts/backup_db.sh
```

Po konfiguracji każdy nowy backup jest automatycznie wysyłany do bucketu `mega-fun-backups` na Backblaze.

**Ręczne sprawdzenie backupów:**
```bash
# Lokalnie na VPS
ls -lh /opt/mega-fun/backups/

# Na Backblaze B2
rclone ls b2:mega-fun-backups/
```

### Jak działa skrypt

Każde wywołanie `send_campaign.py`:
1. Znajduje aktywną kampanię (najstarsza pierwsza)
2. Wysyła jeden email z każdej aktywnej skrzynki mailowej
3. Gdy kampania skończy się, oznacza ją jako `done` i aktywuje następną z kolejki

### Limit skrzynek mailowych

Skrypt czeka **1-2 sekundy** między wysyłkami z kolejnych skrzynek (ochrona przed spamem).
Przy cronie co minutę (60 sek) bezpieczny limit to **max 20 aktywnych skrzynek**.

Jeśli potrzebujesz więcej skrzynek, zmień cron na rzadszy interwał:
| Skrzynek | Minimalny interwał crona |
|---|---|
| 20 | `* * * * *` (co minutę) |
| 50 | `*/3 * * * *` (co 3 minuty) |
| 100 | `*/7 * * * *` (co 7 minut) |
