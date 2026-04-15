#!/usr/bin/env python3
"""
Kleinanzeigen Samsung Galaxy Fold Scraper
Scrapes listings, detects new entries, and sends an HTML email report.
"""

import requests
from bs4 import BeautifulSoup
import json
import os
import re
import smtplib
import hashlib
import time
from email.mime.text import MIMEText
from email.mime.multipart import MIMEMultipart
from datetime import datetime, timezone

# Optional: cloudscraper bypasses Cloudflare/bot-protection automatically.
# Install with: pip install cloudscraper
# Set env var USE_CLOUDSCRAPER=1 to enable.
try:
    import cloudscraper as _cloudscraper  # type: ignore
    _CLOUDSCRAPER_AVAILABLE = True
except ImportError:
    _CLOUDSCRAPER_AVAILABLE = False

USE_CLOUDSCRAPER = os.environ.get("USE_CLOUDSCRAPER", "0") == "1"

# ---------------------------------------------------------------------------
# Configuration (all sensitive values come from environment variables)
# ---------------------------------------------------------------------------
BASE_URL = "https://www.kleinanzeigen.de/s-galaxy-fold/k0l1965r100"
PAGES_TO_SCRAPE = 10  # ~25 listings/page → up to 250 results
DATA_FILE = "data/previous_results.json"

GMAIL_USER              = os.environ.get("GMAIL_USER", "")
GMAIL_APP_PASSWORD      = os.environ.get("GMAIL_APP_PASSWORD", "")
RECIPIENT_EMAIL         = os.environ.get("RECIPIENT_EMAIL", "")
SEND_ALWAYS_HOURS       = int(os.environ.get("SEND_ALWAYS_INTERVAL_HOURS", "6"))

HEADERS = {
    "User-Agent": (
        "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
        "AppleWebKit/537.36 (KHTML, like Gecko) "
        "Chrome/124.0.0.0 Safari/537.36"
    ),
    "Accept-Language": "de-DE,de;q=0.9,en-US;q=0.8",
    "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8",
}

# Model priority order (highest first)
MODELS_ORDER = [
    "Z Fold 7", "Z Fold 6", "Z Fold 5", "Z Fold 4",
    "Z Fold 3", "Z Fold 2", "Z Fold", "Other",
]

# Keywords that indicate a pure accessory, not a phone
ACCESSORY_KEYWORDS = {
    "CASE", "HÜLLE", "COVER", "FOLIE", "SCHUTZFOLIE", "SCHUTZGLAS",
    "KABEL", "LADEKABEL", "CHARGER", "HALTER", "HALTERUNG",
    "STAND", "TASCHE", "WALLET", "BUMPER", "SKIN",
}


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def get_fold_model(title: str) -> str:
    t = title.upper()
    for num in ("7", "6", "5", "4", "3", "2"):
        if f"FOLD {num}" in t or f"FOLD{num}" in t:
            return f"Z Fold {num}"
    if "FOLD" in t:
        return "Z Fold"
    return "Other"


def extract_speicher(text: str) -> str:
    m = re.search(r"(\d+)\s*(TB|GB)", text, re.I)
    if m:
        return f"{m.group(1)} {m.group(2).upper()}"
    return "k.A."


def extract_zustand(text: str) -> str:
    t = text.lower()
    if any(w in t for w in ("ungeöffnet", "originalverp", "versiegelt")):
        return "Neu (OVP)"
    if re.search(r"\bneu\b", t):
        return "Neu"
    if any(w in t for w in ("wie neu", "wie-neu", "neuwertig", "makellos")):
        return "Wie neu"
    if any(w in t for w in ("sehr gut", "sehr-gut")):
        return "Sehr gut"
    if re.search(r"\bgut\b", t):
        return "Gut"
    if any(w in t for w in ("akzeptabel", "gebraucht", "defekt")):
        return "Gebraucht"
    return "k.A."


def is_device(title: str) -> bool:
    """Return False for pure accessories (case, cable, foil, etc.)."""
    upper = title.upper()
    if "FOLD" not in upper:
        return False
    words = set(re.findall(r"[A-ZÄÖÜ]{4,}", upper))
    hits = words & ACCESSORY_KEYWORDS
    # If accessory keyword found AND "SAMSUNG"/"GALAXY" absent → skip
    if hits and "SAMSUNG" not in upper and "GALAXY" not in upper:
        return False
    return True


def listing_id(url: str) -> str:
    return hashlib.md5(url.encode()).hexdigest()


# ---------------------------------------------------------------------------
# Scraping
# ---------------------------------------------------------------------------

def _get_session():
    """Return a requests session (or cloudscraper session if enabled)."""
    if USE_CLOUDSCRAPER and _CLOUDSCRAPER_AVAILABLE:
        print("  Using cloudscraper session (bot-bypass mode)")
        return _cloudscraper.create_scraper(browser={"browser": "chrome", "platform": "windows"})
    return requests.Session()


def scrape_page(page_num: int) -> list[dict]:
    url = BASE_URL if page_num == 1 else f"{BASE_URL}/seite:{page_num}"
    session = _get_session()
    try:
        r = session.get(url, headers=HEADERS, timeout=30)
        r.raise_for_status()
    except requests.RequestException as exc:
        print(f"[WARN] Could not fetch page {page_num}: {exc}")
        return []

    soup = BeautifulSoup(r.text, "lxml")
    results = []

    for article in soup.find_all("article", class_="aditem"):
        try:
            # --- Skip Gesuche ---
            type_tag = article.find(class_=re.compile(r"badge|aditem-addon|label", re.I))
            if type_tag and "gesuch" in type_tag.get_text().lower():
                continue

            # --- Title + URL ---
            a_tag = article.find("a", class_="ellipsis") or (
                article.find("h2") and article.find("h2").find("a")
            )
            if not a_tag:
                continue
            title = a_tag.get_text(strip=True)
            href = a_tag.get("href", "")
            full_url = ("https://www.kleinanzeigen.de" + href) if href.startswith("/") else href
            if not full_url:
                continue

            if not is_device(title):
                continue

            # --- Price ---
            price_tag = article.find(class_=re.compile(r"price", re.I))
            price = re.sub(r"\s+", " ", price_tag.get_text(strip=True)) if price_tag else "k.A."

            # --- Location / Distance ---
            loc_tag = article.find(class_="aditem-main--top--left")
            ort = distance = ""
            if loc_tag:
                raw = loc_tag.get_text(" ", strip=True)
                m = re.search(r"(\d+)\s*km", raw)
                distance = f"{m.group(1)} km" if m else ""
                ort = re.sub(r"\d+\s*km", "", raw).strip(" ,·")

            # --- Description snippet ---
            desc_tag = article.find(class_="aditem-main--middle--description")
            desc = desc_tag.get_text(" ", strip=True) if desc_tag else ""

            combined = title + " " + desc

            results.append({
                "id":          listing_id(full_url),
                "title":       title,
                "price":       price,
                "speicher":    extract_speicher(combined),
                "ort":         ort,
                "entfernung":  distance,
                "zustand":     extract_zustand(combined),
                "model":       get_fold_model(title),
                "url":         full_url,
                "first_seen":  datetime.now(timezone.utc).isoformat(),
            })

        except Exception as exc:
            print(f"[WARN] Skipping malformed article: {exc}")
            continue

    return results


def scrape_all() -> list[dict]:
    seen_urls: set[str] = set()
    all_listings: list[dict] = []

    for page in range(1, PAGES_TO_SCRAPE + 1):
        print(f"  Scraping page {page}…")
        for listing in scrape_page(page):
            if listing["url"] not in seen_urls:
                seen_urls.add(listing["url"])
                all_listings.append(listing)
        if page < PAGES_TO_SCRAPE:
            time.sleep(2)   # polite delay

    print(f"  Total unique listings: {len(all_listings)}")
    return all_listings


# ---------------------------------------------------------------------------
# Persistence
# ---------------------------------------------------------------------------

def load_state() -> dict:
    if not os.path.exists(DATA_FILE):
        return {"listings": {}, "last_full_send": None, "last_run": None}
    try:
        with open(DATA_FILE, encoding="utf-8") as f:
            return json.load(f)
    except Exception as exc:
        print(f"[WARN] Could not read state file: {exc}")
        return {"listings": {}, "last_full_send": None, "last_run": None}


def save_state(state: dict) -> None:
    os.makedirs(os.path.dirname(DATA_FILE), exist_ok=True)
    with open(DATA_FILE, "w", encoding="utf-8") as f:
        json.dump(state, f, ensure_ascii=False, indent=2)


# ---------------------------------------------------------------------------
# Email building
# ---------------------------------------------------------------------------

def price_key(listing: dict) -> float:
    raw = re.sub(r"[^\d,.]", "", listing.get("price", "0")).replace(",", ".")
    try:
        return float(raw)
    except ValueError:
        return 99_999.0


CSS = """
body{font-family:Arial,sans-serif;font-size:14px;color:#333;max-width:1000px;margin:auto}
h1{color:#2c3e50;font-size:20px}
h2{color:#2980b9;font-size:16px;margin-top:24px}
h3{color:#c0392b;font-size:15px}
table{border-collapse:collapse;width:100%;margin-bottom:16px}
th{background:#2980b9;color:#fff;padding:8px 10px;text-align:left;white-space:nowrap}
td{padding:7px 10px;border-bottom:1px solid #ddd;vertical-align:top}
tr:nth-child(even){background:#f9f9f9}
.new{background:#ffeaa7!important}
.badge{background:#e74c3c;color:#fff;padding:2px 6px;border-radius:3px;
       font-size:11px;font-weight:bold;margin-right:4px}
a{color:#2980b9;text-decoration:none}
a:hover{text-decoration:underline}
.meta{background:#ecf0f1;padding:10px 14px;border-radius:5px;margin-bottom:20px;line-height:1.7}
.none{color:#999;font-style:italic}
"""

def build_table(listings: list[dict], new_ids: set[str] | None = None) -> str:
    if not listings:
        return '<p class="none">Keine Angebote in dieser Kategorie.</p>'

    rows = []
    for l in sorted(listings, key=price_key):
        is_new = new_ids and l["id"] in new_ids
        row_cls = ' class="new"' if is_new else ""
        badge    = '<span class="badge">NEU</span>' if is_new else ""
        rows.append(
            f'<tr{row_cls}>'
            f'<td><strong>{l["price"]}</strong></td>'
            f'<td>{badge}{l["title"]}</td>'
            f'<td>{l["speicher"]}</td>'
            f'<td>{l["ort"]}</td>'
            f'<td>{l["entfernung"]}</td>'
            f'<td>{l["zustand"]}</td>'
            f'<td><a href="{l["url"]}" target="_blank">Anzeige&nbsp;→</a></td>'
            f'</tr>'
        )

    header = (
        "<table>"
        "<tr>"
        "<th>Preis</th><th>Titel</th><th>Speicher</th>"
        "<th>Ort</th><th>Entfernung</th><th>Zustand</th><th>Link</th>"
        "</tr>"
    )
    return header + "".join(rows) + "</table>"


def build_email_html(
    current: list[dict],
    new_listings: list[dict],
    is_full: bool,
) -> str:
    now_str = datetime.now().strftime("%d.%m.%Y %H:%M")
    new_ids  = {l["id"] for l in new_listings}

    # Group by model
    grouped: dict[str, list[dict]] = {m: [] for m in MODELS_ORDER}
    for l in current:
        grouped.get(l["model"], grouped["Other"]).append(l)

    html = f"""<!DOCTYPE html>
<html lang="de">
<head><meta charset="utf-8"><style>{CSS}</style></head>
<body>
<h1>Samsung Galaxy Fold &ndash; Kleinanzeigen Preisalarm</h1>
<div class="meta">
  <b>Stand:</b> {now_str} Uhr &nbsp;|&nbsp;
  <b>Gefunden:</b> {len(current)} Angebote &nbsp;|&nbsp;
  <b>Neu:</b> {len(new_listings)} &nbsp;|&nbsp;
  <b>Radius:</b> 100&nbsp;km um Mönchengladbach (41061)
</div>
"""

    # ── New listings block ──────────────────────────────────────────────────
    if new_listings:
        html += f"<h3>&#x1F514; Neue Angebote ({len(new_listings)})</h3>\n"
        html += build_table(new_listings)
        html += "<hr>\n"

    # ── Full summary by model ───────────────────────────────────────────────
    if is_full or new_listings:
        html += "<h2>Alle Angebote nach Modell</h2>\n"
        for model in MODELS_ORDER:
            listings = grouped[model]
            if not listings:
                continue
            html += f"<h2>{model} &nbsp;<small>({len(listings)})</small></h2>\n"
            html += build_table(listings, new_ids=new_ids)

    html += """<hr>
<p style="color:#999;font-size:12px">
  Automatisch generiert &ndash; Samsung Galaxy Fold Preisalarm<br>
  Quelle: kleinanzeigen.de &nbsp;|&nbsp; Radius: 100&nbsp;km um 41061 Mönchengladbach
</p>
</body></html>"""

    return html


# ---------------------------------------------------------------------------
# Email sending
# ---------------------------------------------------------------------------

def send_email(subject: str, html: str) -> bool:
    if not all([GMAIL_USER, GMAIL_APP_PASSWORD, RECIPIENT_EMAIL]):
        print("[WARN] Email credentials missing – skipping send.")
        return False

    msg = MIMEMultipart("alternative")
    msg["Subject"] = subject
    msg["From"]    = GMAIL_USER
    msg["To"]      = RECIPIENT_EMAIL
    msg.attach(MIMEText(html, "html", "utf-8"))

    try:
        with smtplib.SMTP_SSL("smtp.gmail.com", 465) as s:
            s.login(GMAIL_USER, GMAIL_APP_PASSWORD)
            s.sendmail(GMAIL_USER, RECIPIENT_EMAIL, msg.as_string())
        print(f"  Email sent to {RECIPIENT_EMAIL}")
        return True
    except smtplib.SMTPException as exc:
        print(f"[ERROR] SMTP error: {exc}")
        return False


# ---------------------------------------------------------------------------
# Orchestration
# ---------------------------------------------------------------------------

def due_for_full_summary(state: dict) -> bool:
    last = state.get("last_full_send")
    if not last:
        return True
    dt = datetime.fromisoformat(last)
    if dt.tzinfo is None:
        dt = dt.replace(tzinfo=timezone.utc)
    elapsed_h = (datetime.now(timezone.utc) - dt).total_seconds() / 3600
    return elapsed_h >= SEND_ALWAYS_HOURS


def main() -> None:
    print(f"=== Scraper started {datetime.now().isoformat()} ===")

    state = load_state()

    print("Fetching listings…")
    current = scrape_all()

    if not current:
        print("[WARN] No listings scraped – possible anti-bot block. Aborting.")
        return

    # Detect new listings
    prev_ids = set(state.get("listings", {}).keys())
    new_listings = [l for l in current if l["id"] not in prev_ids]
    print(f"  New since last run: {len(new_listings)}")

    is_full = due_for_full_summary(state)
    should_send = bool(new_listings) or is_full

    if should_send:
        subject = (
            f"\U0001F514 {len(new_listings)} neue Galaxy Fold Angebote – Kleinanzeigen"
            if new_listings
            else f"\U0001F4CA Galaxy Fold Übersicht – {len(current)} Angebote"
        )
        html = build_email_html(current, new_listings, is_full)
        if send_email(subject, html) and is_full:
            state["last_full_send"] = datetime.now(timezone.utc).isoformat()
    else:
        print("  No new listings and full summary not due – email skipped.")

    # Merge: keep first_seen timestamps from previous run
    current_by_id = {l["id"]: l for l in current}
    for lid, listing in current_by_id.items():
        if lid in state.get("listings", {}):
            listing["first_seen"] = state["listings"][lid].get(
                "first_seen", listing["first_seen"]
            )

    state["listings"] = current_by_id
    state["last_run"] = datetime.now(timezone.utc).isoformat()
    save_state(state)
    print("=== Done ===")


if __name__ == "__main__":
    main()
