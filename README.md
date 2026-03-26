# OLX Tracker

Aplikacja webowa do automatycznego śledzenia nowych ogłoszeń na OLX.pl i wysyłania powiadomień na Discord.

## Funkcje

- **Web UI** - dodawanie, edycja, usuwanie linków do śledzenia
- **Automatyczny scraping** - uruchamiany co X minut (konfigurowalne)
- **Discord webhook** - natychmiastowe powiadomienia o nowych ogłoszeniach
- **Deduplikacja** - nie wysyła powtórzeń tego samego ogłoszenia
- **Dashboard logów** - historia wszystkich operacji
- **Manualne uruchamianie** - przycisk "Run Now" dla każdego linku

## Struktura projektu

```
├── main.py              # Entry point FastAPI
├── models.py            # SQLAlchemy modele (SQLite)
├── scraper.py           # Logika scrapera OLX
├── scheduler.py         # APScheduler konfiguracja
├── requirements.txt     # Zależności
├── templates/           # Jinja2 templates
│   ├── base.html
│   ├── index.html
│   └── logs.html
└── olx_tracker.db       # Baza SQLite (auto-created)
```

## Lokalne uruchomienie

```bash
# Instalacja zależności
pip install -r requirements.txt

# Uruchomienie serwera
python main.py

# Aplikacja dostępna pod http://localhost:8000
```

## Użycie

1. Wejdź na stronę aplikacji
2. Wypełnij formularz:
   - **Name** - nazwa dla własnej orientacji
   - **OLX Search URL** - link do wyszukiwania (upewnij się że jest posortowane po najnowszych)
   - **Discord Webhook URL** - URL webhooka z Discord
   - **Items** - ile najnowszych ogłoszeń sprawdzać (domyślnie 4)
   - **Interval** - co ile minut uruchamiać scraper (domyślnie 5)
3. Kliknij "Add Tracking"

## Zarządzanie linkami

| Przycisk | Funkcja |
|----------|---------|
| ⏸/▶ | Włącz/wyłącz śledzenie |
| ⚡ | Uruchom scraping natychmiast |
| 🗑 | Usuń link |

## Troubleshooting

**Brak powiadomień na Discord:**
- Sprawdź czy webhook URL jest poprawny
- Sprawdź logi w zakładce "Logs"

**OLX blokuje scraper:**
- Zwiększ interval_minutes (np. na 10-15 min)
- Użyk proxy/VPN na serwerze

**Scheduler nie uruchamia jobów:**
- Sprawdź czy aplikacja działa 24/7 (nie usypia się)
- Sprawdź logi startupu w zakładce "Logs"

## Technologie

- **Backend**: FastAPI, SQLAlchemy, APScheduler
- **Frontend**: Jinja2, Tailwind CSS (CDN)
- **Baza**: SQLite
- **Scraping**: requests, BeautifulSoup4