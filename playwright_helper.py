#!/usr/bin/env python3
"""
Playwright Helper für kleinanzeigen.de
=======================================
Stellt eine Playwright-Browser-Instanz bereit, bei der die
Consent-Cookies (TCF2) bereits vorgesetzt sind, so dass das
Einwilligungs-Popup niemals erscheint.

Verwendung (als Modul):
    from playwright_helper import new_page
    with sync_playwright() as p:
        page = new_page(p)
        page.goto("https://www.kleinanzeigen.de/...")

Oder direkt ausführen für einen manuellen Test:
    python playwright_helper.py
"""

import json
import pathlib
from playwright.sync_api import sync_playwright, Page, BrowserContext

COOKIES_FILE = pathlib.Path(__file__).parent / "data" / "kleinanzeigen_consent_cookies.json"

CONSENT_COOKIES: list[dict] = json.loads(COOKIES_FILE.read_text(encoding="utf-8"))


def apply_consent_cookies(context: BrowserContext) -> None:
    """Setzt die Consent-Cookies in einen bestehenden Playwright-Kontext."""
    context.add_cookies(CONSENT_COOKIES)


def new_page(playwright, headless: bool = True) -> tuple:
    """
    Erstellt Browser + Kontext + Page mit vorgesetzten Consent-Cookies.
    Gibt (browser, context, page) zurück.

    Beispiel:
        with sync_playwright() as p:
            browser, ctx, page = new_page(p)
            page.goto("https://www.kleinanzeigen.de/...")
            # ... deine Arbeit ...
            browser.close()
    """
    browser = playwright.chromium.launch(headless=headless)
    context = browser.new_context(
        user_agent=(
            "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
            "AppleWebKit/537.36 (KHTML, like Gecko) "
            "Chrome/124.0.0.0 Safari/537.36"
        ),
        locale="de-DE",
        extra_http_headers={
            "Accept-Language": "de-DE,de;q=0.9,en-US;q=0.8",
        },
    )
    # Consent-Cookies VOR der ersten Navigation setzen
    apply_consent_cookies(context)
    page = context.new_page()
    return browser, context, page


# ---------------------------------------------------------------------------
# Direkttest
# ---------------------------------------------------------------------------
if __name__ == "__main__":
    TEST_URL = "https://www.kleinanzeigen.de/s-moenchengladbach/galaxy-fold/k0l1957r100"

    with sync_playwright() as p:
        browser, ctx, page = new_page(p, headless=False)
        print(f"Navigiere zu: {TEST_URL}")
        page.goto(TEST_URL, wait_until="domcontentloaded")

        # Prüfe ob Consent-Popup sichtbar ist
        popup_visible = page.locator("#gdpr-banner-container, [data-testid='gdpr-banner']").count() > 0
        print(f"Consent-Popup sichtbar: {popup_visible}")

        title = page.title()
        print(f"Seitentitel: {title}")

        # Kurz anzeigen, dann schließen
        page.wait_for_timeout(3000)
        browser.close()
        print("Test abgeschlossen – kein Popup erschienen!")
