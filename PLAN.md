# Mega-Fun — Plan projektu

## Stack
- Python + Flask + SQLite + Nginx
- VPS $5, systemd, crontab
- SMTP: Purelymail

## Faza 1 — Fundament

### Krok 1: Struktura projektu + zależności
- Katalogi: `app/`, `templates/`, `static/`, `scripts/`
- `requirements.txt`
- `config.py`

### Krok 2: Baza danych SQLite
- Tabele: `businesses`, `emails`, `campaigns`, `mailboxes`, `settings`, `operations_log`
- Skrypt `init_db.py`

### Krok 3: Flask app — szkielet
- 5 zakładek (routing + szablony)
- Layout base template z nawigacją
- Uruchomienie na localhost:5000

## Faza 2 — Zakładka 1: Scraping Google Maps

### Krok 4: Backend scrapera Google Maps
- Endpoint przyjmujący query
- Scraper jako subprocess
- Zapis do tabeli `businesses`

### Krok 5: UI zakładki Google Maps
- Dolna część: tabela biznesów (paginacja, filtrowanie)
- Górna część: placeholder
- AJAX

## Faza 3 — Zakładka 5: Ustawienia

### Krok 6: Ustawienia + skrzynki mailowe
- Formularz na API key
- CRUD skrzynek mailowych (Purelymail SMTP)
- Tabela `mailboxes`

## Faza 4 — Zakładka 2: Scraping maili

### Krok 7: Scraper maili
- Wyciąganie emaili ze stron www
- Subprocess
- Zapis do tabeli `emails`
- UI placeholder

## Faza 5 — Zakładka 3: Kampanie

### Krok 8: Operacje kampanii
- Tworzenie kampanii
- Powiązanie z emailami
- UI

## Faza 6 — Wysyłka maili (Cron)

### Krok 9: Skrypt wysyłki maili
- `scripts/send_emails.py` — 1 mail z każdej skrzynki na wywołanie
- Rotacja skrzynek, tracking statusu
- Crontab

## Faza 7 — Zakładka 4 + operacje

### Krok 10: Historia operacji
- Log operacji w `operations_log`
- UI z listą operacji

## Faza 8 — Deploy na VPS

### Krok 11: Konfiguracja serwera
- systemd unit file
- Nginx reverse proxy
- Crontab setup
- Firewall (ufw)
