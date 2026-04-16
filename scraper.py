#!/usr/bin/env python3
"""
Kleinanzeigen Samsung Galaxy Fold Scraper
Scrapes listings, visits each detail page, uses Groq LLM for assessment,
detects new entries, and sends an HTML email report.
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
try:
    import cloudscraper as _cloudscraper  # type: ignore
    _CLOUDSCRAPER_AVAILABLE = True
except ImportError:
    _CLOUDSCRAPER_AVAILABLE = False

USE_CLOUDSCRAPER = os.environ.get("USE_CLOUDSCRAPER", "0") == "1"

# ---------------------------------------------------------------------------
# Groq AI Assessment
# ---------------------------------------------------------------------------
GROQ_API_KEY = os.environ.get("GROQ_API_KEY", "")
GROQ_MODEL   = "llama-3.1-8b-instant"
GROQ_URL     = "https://api.groq.com/openai/v1/chat/completions"

GROQ_SYSTEM_PROMPT = """Du bist ein Experte für gebrauchte Samsung Galaxy Fold Smartphones.
Du analysierst Kleinanzeigen-Inserate und gibst eine strukturierte Einschätzung zurück.
Antworte NUR mit einem JSON-Objekt, kein weiterer Text.

JSON-Format:
{
  "sterne": <1-5>,
  "empfehlung": "<Sehr empfehlenswert|Empfehlenswert|Neutral|Vorsicht|Meiden>",
  "preis_bewertung": "<Sehr günstig|Günstig|Fair|Teuer|Überteuert>",
  "warnsignale": ["<signal1>", ...],
  "positives": ["<positiv1>", ...],
  "zusammenfassung": "<max 120 Zeichen>"
}"""


def groq_assess(listing: dict) -> dict | None:
    """Call Groq API to assess a listing. Returns parsed JSON or None on error."""
    if not GROQ_API_KEY:
        return None

    model   = listing.get("model", "")
    title   = listing.get("title", "")
    price   = listing.get("price", "k.A.")
    zustand = listing.get("zustand", "k.A.")
    speicher = listing.get("speicher", "k.A.")
    ort     = listing.get("ort", "")
    desc    = (listing.get("full_description") or listing.get("description", ""))[:1200]

    user_msg = f"""Modell: {model}
Titel: {title}
Preis: {price}
Zustand: {zustand}
Speicher: {speicher}
Ort: {ort}

Beschreibung:
{desc}"""

    payload = {
        "model": GROQ_MODEL,
        "messages": [
            {"role": "system", "content": GROQ_SYSTEM_PROMPT},
            {"role": "user",   "content": user_msg},
        ],
        "temperature": 0.2,
        "max_tokens":  400,
        "response_format": {"type": "json_object"},
    }
    headers = {
        "Authorization": f"Bearer {GROQ_API_KEY}",
        "Content-Type": "application/json",
    }

    for attempt in range(3):
        try:
            r = requests.post(GROQ_URL, json=payload, headers=headers, timeout=20)
            if r.status_code == 429:
                wait = 10 * (attempt + 1)
                print(f"    [WARN] Groq rate limit – waiting {wait}s…")
                time.sleep(wait)
                continue
            r.raise_for_status()
            content = r.json()["choices"][0]["message"]["content"]
            result  = json.loads(content)
            return {
                "stars":       int(result.get("sterne", 3)),
                "label":       result.get("empfehlung", "Neutral"),
                "price_note":  result.get("preis_bewertung", ""),
                "flags":       result.get("warnsignale", []),
                "positives":   result.get("positives", []),
                "summary":     result.get("zusammenfassung", ""),
                "source":      "groq",
            }
        except Exception as exc:
            print(f"    [WARN] Groq API error: {exc}")
            return None
    print("    [WARN] Groq failed after 3 attempts – using rule-based fallback")
    return None

# ---------------------------------------------------------------------------
# Configuration
# ---------------------------------------------------------------------------
BASE_URL    = "https://www.kleinanzeigen.de/s-moenchengladbach/galaxy-fold/k0l1957r100"
PAGES_TO_SCRAPE = 10   # ~25 listings/page → up to 250 results
DATA_FILE   = "data/previous_results.json"

GMAIL_USER         = os.environ.get("GMAIL_USER", "")
GMAIL_APP_PASSWORD = os.environ.get("GMAIL_APP_PASSWORD", "")
RECIPIENT_EMAIL    = os.environ.get("RECIPIENT_EMAIL", "")
SEND_ALWAYS_HOURS  = int(os.environ.get("SEND_ALWAYS_INTERVAL_HOURS", "6"))

HEADERS = {
    "User-Agent": (
        "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
        "AppleWebKit/537.36 (KHTML, like Gecko) "
        "Chrome/124.0.0.0 Safari/537.36"
    ),
    "Accept-Language": "de-DE,de;q=0.9,en-US;q=0.8",
    "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8",
}

MODELS_ORDER = [
    "Z Fold 7", "Z Fold 6", "Z Fold 5", "Z Fold 4",
    "Z Fold 3", "Z Fold 2", "Z Fold", "Other",
]

ACCESSORY_KEYWORDS = {
    "CASE", "HÜLLE", "COVER", "FOLIE", "SCHUTZFOLIE", "SCHUTZGLAS",
    "KABEL", "LADEKABEL", "CHARGER", "HALTER", "HALTERUNG",
    "STAND", "TASCHE", "WALLET", "BUMPER", "SKIN",
}

# ---------------------------------------------------------------------------
# Assessment engine
# ---------------------------------------------------------------------------

RED_FLAGS = [
    ("defekt",           "⚠️ Als defekt beschrieben"),
    ("kaputt",           "⚠️ Gerät kaputt"),
    ("beschädigt",       "⚠️ Beschädigungen erwähnt"),
    ("riss",             "⚠️ Riss vorhanden"),
    ("gesprungen",       "⚠️ Display gesprungen"),
    ("display.*schaden", "⚠️ Displayschaden"),
    ("akku.*schwach",    "⚠️ Akku schwach"),
    ("lädt nicht",       "⚠️ Ladeprobleme"),
    ("überhitzt",        "⚠️ Überhitzung"),
    ("wasserschaden",    "⚠️ Wasserschaden"),
    ("icloud.*lock",     "⚠️ iCloud-Lock"),
    ("google.*lock",     "⚠️ Google-Lock gesperrt"),
    ("ohne zubehör",     "ℹ️ Ohne Zubehör"),
    ("ohne ladekabel",   "ℹ️ Ohne Ladekabel"),
    ("kratzer",          "ℹ️ Kratzer vorhanden"),
    ("gebrauchsspuren",  "ℹ️ Gebrauchsspuren"),
]

POSITIVES = [
    ("ovp|originalverpack|versiegelt", "✅ Originalverpackung"),
    ("garantie",                       "✅ Garantie vorhanden"),
    ("rechnung|kaufbeleg",             "✅ Rechnung/Beleg vorhanden"),
    ("neuwertig|makellos|einwandfrei", "✅ Makellos"),
    ("zubehör.*enthalten|komplett",    "✅ Zubehör dabei"),
    ("non-eu|snapdragon",              "✅ Snapdragon-Version (non-EU)"),
    ("sim-frei|simlockfrei",           "✅ SIM-frei"),
]

# Typical price ranges per model (EUR) for scoring
MODEL_PRICE_REFS = {
    "Z Fold 7": 1400,
    "Z Fold 6": 900,
    "Z Fold 5": 650,
    "Z Fold 4": 450,
    "Z Fold 3": 300,
    "Z Fold 2": 200,
    "Z Fold":   180,
    "Other":    350,
}

CONDITION_SCORE = {
    "Neu (OVP)": 5,
    "Neu":       5,
    "Wie neu":   4,
    "Sehr gut":  3,
    "Gut":       2,
    "Gebraucht": 1,
    "k.A.":      2,
}


def assess_listing(listing: dict) -> dict:
    """
    Analyse title + full description and return an assessment dict:
      stars       : 1-5
      label       : Sehr empfehlenswert / Empfehlenswert / Neutral / Vorsicht / Meiden
      price_note  : comment on the price vs model average
      flags       : list of red-flag strings
      positives   : list of positive-signal strings
      summary     : one-liner
    """
    text = (listing.get("title", "") + " " +
            listing.get("description", "") + " " +
            listing.get("full_description", "")).lower()

    model   = listing.get("model", "Other")
    zustand = listing.get("zustand", "k.A.")

    # --- Detect red flags & positives ---
    flags     = [msg for pattern, msg in RED_FLAGS if re.search(pattern, text)]
    positives = [msg for pattern, msg in POSITIVES if re.search(pattern, text)]

    # --- Price scoring ---
    raw_price = re.sub(r"[^\d,.]", "", listing.get("price", "0")).replace(",", ".")
    try:
        price = float(raw_price)
    except ValueError:
        price = 0.0

    ref   = MODEL_PRICE_REFS.get(model, 400)
    ratio = price / ref if ref and price > 0 else 1.0

    if price == 0:
        price_note = "ℹ️ Preis nicht angegeben"
        price_pts  = 2
    elif ratio < 0.65:
        price_note = f"🟢 Sehr günstig (Ref. ~{ref} €)"
        price_pts  = 5
    elif ratio < 0.85:
        price_note = f"🟢 Günstiger Preis (Ref. ~{ref} €)"
        price_pts  = 4
    elif ratio < 1.10:
        price_note = f"🟡 Fairer Preis (Ref. ~{ref} €)"
        price_pts  = 3
    elif ratio < 1.30:
        price_note = f"🟠 Etwas teuer (Ref. ~{ref} €)"
        price_pts  = 2
    else:
        price_note = f"🔴 Deutlich überteuert (Ref. ~{ref} €)"
        price_pts  = 1

    # --- Condition scoring ---
    cond_pts = CONDITION_SCORE.get(zustand, 2)

    # --- Composite score (0-10 scale → map to 1-5 stars) ---
    flag_penalty = min(len(flags) * 0.8, 3)
    pos_bonus    = min(len(positives) * 0.4, 1.5)
    raw_score    = (price_pts * 0.45 + cond_pts * 0.40 + pos_bonus - flag_penalty)
    raw_score    = max(0.5, min(5.0, raw_score))
    stars        = round(raw_score)

    labels = {5: "Sehr empfehlenswert", 4: "Empfehlenswert",
              3: "Neutral", 2: "Vorsicht", 1: "Meiden"}
    label  = labels.get(stars, "Neutral")

    # --- One-liner summary ---
    parts = []
    if flags:
        parts.append(flags[0])
    elif positives:
        parts.append(positives[0])
    parts.append(price_note)
    summary = " · ".join(parts[:2])

    return {
        "stars":      stars,
        "label":      label,
        "price_note": price_note,
        "flags":      flags,
        "positives":  positives,
        "summary":    summary,
    }


# ---------------------------------------------------------------------------
# Detail page fetcher
# ---------------------------------------------------------------------------

def fetch_listing_details(url: str, session) -> str:
    """Visit a listing detail page and return the full description text."""
    try:
        r = session.get(url, headers=HEADERS, timeout=20)
        r.raise_for_status()
        soup = BeautifulSoup(r.text, "lxml")

        # Main description section
        desc = soup.find(id="viewad-description-text") or \
               soup.find(class_=re.compile(r"description|text-body", re.I))
        if desc:
            return desc.get_text(" ", strip=True)[:2000]
    except Exception as exc:
        print(f"    [WARN] Could not fetch detail page: {exc}")
    return ""


# ---------------------------------------------------------------------------
# Core helpers (unchanged)
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
    upper = title.upper()
    if "FOLD" not in upper:
        return False
    words = set(re.findall(r"[A-ZÄÖÜ]{4,}", upper))
    if words & ACCESSORY_KEYWORDS and "SAMSUNG" not in upper and "GALAXY" not in upper:
        return False
    return True


def listing_id(url: str) -> str:
    return hashlib.md5(url.encode()).hexdigest()


def _get_session():
    if USE_CLOUDSCRAPER and _CLOUDSCRAPER_AVAILABLE:
        print("  Using cloudscraper session")
        return _cloudscraper.create_scraper(browser={"browser": "chrome", "platform": "windows"})
    return requests.Session()


# ---------------------------------------------------------------------------
# Scraping
# ---------------------------------------------------------------------------

def scrape_page(page_num: int, session) -> list[dict]:
    # Kleinanzeigen pagination: seite:N goes AFTER the city segment, not at the end
    # e.g. /s-moenchengladbach/seite:2/galaxy-fold/k0l1957r100
    if page_num == 1:
        url = BASE_URL
    else:
        url = re.sub(r'(s-[^/]+/)', rf'\1seite:{page_num}/', BASE_URL, count=1)
    try:
        r = session.get(url, headers=HEADERS, timeout=30)
        r.raise_for_status()
    except requests.RequestException as exc:
        print(f"  [WARN] Could not fetch page {page_num}: {exc}")
        return []

    soup = BeautifulSoup(r.text, "lxml")
    results = []
    total_articles = 0
    skipped_gesuch = 0
    skipped_no_link = 0
    skipped_filter = 0
    filtered_titles = []

    for article in soup.find_all("article", class_="aditem"):
        total_articles += 1
        try:
            type_tag = article.find(class_=re.compile(r"badge|aditem-addon|label", re.I))
            if type_tag and "gesuch" in type_tag.get_text().lower():
                skipped_gesuch += 1
                continue

            a_tag = article.find("a", class_="ellipsis") or (
                article.find("h2") and article.find("h2").find("a")
            )
            if not a_tag:
                skipped_no_link += 1
                continue
            title = a_tag.get_text(strip=True)
            href  = a_tag.get("href", "")
            full_url = ("https://www.kleinanzeigen.de" + href) if href.startswith("/") else href
            if not full_url or not is_device(title):
                skipped_filter += 1
                filtered_titles.append(title[:80])
                continue

            price_tag = article.find(class_=re.compile(r"price", re.I))
            price = re.sub(r"\s+", " ", price_tag.get_text(strip=True)) if price_tag else "k.A."

            loc_tag = article.find(class_="aditem-main--top--left")
            ort = distance = ""
            if loc_tag:
                raw = loc_tag.get_text(" ", strip=True)
                m   = re.search(r"(\d+)\s*km", raw)
                distance = f"{m.group(1)} km" if m else ""
                ort = re.sub(r"\d+\s*km", "", raw).strip(" ,·")

            desc_tag = article.find(class_="aditem-main--middle--description")
            desc = desc_tag.get_text(" ", strip=True) if desc_tag else ""
            combined = title + " " + desc

            results.append({
                "id":         listing_id(full_url),
                "title":      title,
                "price":      price,
                "speicher":   extract_speicher(combined),
                "ort":        ort,
                "entfernung": distance,
                "zustand":    extract_zustand(combined),
                "model":      get_fold_model(title),
                "url":        full_url,
                "description": desc,
                "full_description": "",   # filled later
                "assessment": None,       # filled later
                "first_seen": datetime.now(timezone.utc).isoformat(),
            })
        except Exception as exc:
            print(f"  [WARN] Skipping article: {exc}")
            continue

    if page_num == 1:
        print(f"    Page {page_num}: {total_articles} articles | "
              f"{skipped_gesuch} Gesuch | {skipped_no_link} no-link | "
              f"{skipped_filter} filtered-out | {len(results)} kept")
        if filtered_titles:
            print(f"    Filtered titles (first 5): {filtered_titles[:5]}")
    return results


def scrape_all() -> list[dict]:
    session   = _get_session()
    seen_urls: set[str] = set()
    all_listings: list[dict] = []

    for page in range(1, PAGES_TO_SCRAPE + 1):
        print(f"  Scraping page {page}…")
        for listing in scrape_page(page, session):
            if listing["url"] not in seen_urls:
                seen_urls.add(listing["url"])
                all_listings.append(listing)
        if page < PAGES_TO_SCRAPE:
            time.sleep(2)

    print(f"  Total unique listings: {len(all_listings)}")
    return all_listings, session


def enrich_with_details(listings: list[dict], known: dict, session) -> list[dict]:
    """
    Visit detail pages only for listings not yet analyzed.
    Reuses cached full_description + assessment from `known` dict.
    """
    need_fetch = [l for l in listings if l["id"] not in known or
                  not known[l["id"]].get("assessment")]
    cached     = [l for l in listings if l["id"] in known and
                  known[l["id"]].get("assessment")]

    print(f"  Detail pages to fetch: {len(need_fetch)}  (cached: {len(cached)})")

    for i, listing in enumerate(need_fetch, 1):
        print(f"    [{i}/{len(need_fetch)}] {listing['title'][:60]}")
        full_desc = fetch_listing_details(listing["url"], session)
        listing["full_description"] = full_desc
        # Re-extract condition & storage from full description if not found
        combined = listing["title"] + " " + listing["description"] + " " + full_desc
        if listing["speicher"] == "k.A.":
            listing["speicher"] = extract_speicher(combined)
        if listing["zustand"] == "k.A.":
            listing["zustand"] = extract_zustand(combined)
        # Try Groq first, fall back to rule-based
        ai = groq_assess(listing) if GROQ_API_KEY else None
        listing["assessment"] = ai if ai else assess_listing(listing)
        if ai:
            print(f"      → Groq: {ai['label']} ({ai['stars']}⭐) – {ai['summary'][:60]}")
            time.sleep(2.5)  # Groq free-tier: 30 req/min → need ~2s between calls
        else:
            time.sleep(1.5)  # polite delay for detail page fetching

    # Restore cached data
    for listing in cached:
        prev = known[listing["id"]]
        listing["full_description"] = prev.get("full_description", "")
        listing["assessment"]       = prev.get("assessment")
        # Recalculate assessment if price changed (e.g. seller updated it)
        if listing["price"] != prev.get("price"):
            listing["assessment"] = assess_listing(listing)

    return listings


# ---------------------------------------------------------------------------
# Persistence
# ---------------------------------------------------------------------------

def load_state() -> dict:
    if not os.path.exists(DATA_FILE):
        return {"listings": {}, "last_full_send": None, "last_run": None}
    try:
        with open(DATA_FILE, encoding="utf-8") as f:
            return json.load(f)
    except Exception:
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


def stars_html(n: int) -> str:
    return "⭐" * n + "☆" * (5 - n)


def assessment_cell(a: dict | None) -> str:
    if not a:
        return "–"
    label_color = {
        "Sehr empfehlenswert": "#27ae60",
        "Empfehlenswert":      "#2ecc71",
        "Neutral":             "#f39c12",
        "Vorsicht":            "#e67e22",
        "Meiden":              "#e74c3c",
    }
    color = label_color.get(a["label"], "#888")
    flags_html = "".join(f'<li style="color:#c0392b">{f}</li>' for f in a["flags"])
    pos_html   = "".join(f'<li style="color:#27ae60">{p}</li>' for p in a["positives"])
    details = ""
    if flags_html or pos_html:
        details = f'<ul style="margin:3px 0 0 0;padding-left:14px;font-size:11px">{flags_html}{pos_html}</ul>'
    return (
        f'<span style="font-size:15px">{stars_html(a["stars"])}</span><br>'
        f'<span style="color:{color};font-weight:bold;font-size:11px">{a["label"]}</span><br>'
        f'<span style="font-size:11px;color:#555">{a["price_note"]}</span>'
        f'{details}'
    )


CSS = """
body{font-family:Arial,sans-serif;font-size:14px;color:#333;max-width:1100px;margin:auto}
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
        is_new   = new_ids and l["id"] in new_ids
        row_cls  = ' class="new"' if is_new else ""
        badge    = '<span class="badge">NEU</span>' if is_new else ""
        a_html   = assessment_cell(l.get("assessment"))

        rows.append(
            f'<tr{row_cls}>'
            f'<td><strong>{l["price"]}</strong></td>'
            f'<td>{badge}<a href="{l["url"]}" target="_blank">{l["title"]}</a></td>'
            f'<td>{l["speicher"]}</td>'
            f'<td>{l["ort"]}</td>'
            f'<td>{l["entfernung"]}</td>'
            f'<td>{l["zustand"]}</td>'
            f'<td>{a_html}</td>'
            f'</tr>'
        )

    header = (
        "<table><tr>"
        "<th>Preis</th><th>Titel</th><th>Speicher</th>"
        "<th>Ort</th><th>Entfernung</th><th>Zustand</th>"
        "<th>Einschätzung</th>"
        "</tr>"
    )
    return header + "".join(rows) + "</table>"


def build_email_html(current: list[dict], new_listings: list[dict], is_full: bool) -> str:
    now_str  = datetime.now().strftime("%d.%m.%Y %H:%M")
    new_ids  = {l["id"] for l in new_listings}

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
    if new_listings:
        html += f"<h3>&#x1F514; Neue Angebote ({len(new_listings)})</h3>\n"
        html += build_table(new_listings)
        html += "<hr>\n"

    if is_full or new_listings:
        html += "<h2>Alle Angebote nach Modell</h2>\n"
        for model in MODELS_ORDER:
            items = grouped[model]
            if not items:
                continue
            html += f"<h2>{model} &nbsp;<small>({len(items)})</small></h2>\n"
            html += build_table(items, new_ids=new_ids)

    html += """<hr>
<p style="color:#999;font-size:12px">
  Automatisch generiert &ndash; Samsung Galaxy Fold Preisalarm<br>
  Einschätzung basiert auf Beschreibung, Zustand und Preis-Referenzwerten.<br>
  Quelle: kleinanzeigen.de &nbsp;|&nbsp; 100&nbsp;km um 41061 Mönchengladbach
</p></body></html>"""
    return html


# ---------------------------------------------------------------------------
# Email sending
# ---------------------------------------------------------------------------

def send_email(subject: str, html: str) -> bool:
    if not all([GMAIL_USER, GMAIL_APP_PASSWORD, RECIPIENT_EMAIL]):
        print("  [WARN] Email credentials missing.")
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
        print(f"  [ERROR] SMTP: {exc}")
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
    return (datetime.now(timezone.utc) - dt).total_seconds() / 3600 >= SEND_ALWAYS_HOURS


def main() -> None:
    print(f"=== Scraper started {datetime.now().isoformat()} ===")
    state = load_state()

    print("Fetching search results…")
    current, session = scrape_all()

    if not current:
        print("[WARN] No listings found – possible anti-bot block.")
        return

    print("Enriching listings with detail pages + assessments…")
    current = enrich_with_details(current, state.get("listings", {}), session)

    prev_ids      = set(state.get("listings", {}).keys())
    new_listings  = [l for l in current if l["id"] not in prev_ids]
    print(f"  New since last run: {len(new_listings)}")

    is_full     = due_for_full_summary(state)
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

    # Merge: keep first_seen + assessment for known listings
    current_by_id = {l["id"]: l for l in current}
    for lid, listing in current_by_id.items():
        if lid in state.get("listings", {}):
            listing["first_seen"] = state["listings"][lid].get("first_seen", listing["first_seen"])

    state["listings"] = current_by_id
    state["last_run"] = datetime.now(timezone.utc).isoformat()
    save_state(state)
    print("=== Done ===")


if __name__ == "__main__":
    main()
