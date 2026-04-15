# Samsung Galaxy Fold – Kleinanzeigen Preisalarm

Hourly scraper for Samsung Galaxy Fold listings on kleinanzeigen.de
(100 km radius around Mönchengladbach / PLZ 41061).

Runs as a **GitHub Actions cron job** – no local machine required.

---

## Setup guide (step-by-step)

### Step 1 – Create a GitHub repository

1. Go to https://github.com/new
2. Create a **private** repository (e.g. `fold-alert`)
3. Clone it locally:
   ```
   git clone https://github.com/YOUR_USER/fold-alert.git
   cd fold-alert
   ```
4. Copy all files from this project into the cloned folder.
5. Push:
   ```
   git add .
   git commit -m "Initial commit"
   git push
   ```

---

### Step 2 – Create a Gmail App Password

> You need a **Gmail App Password** (not your regular password).
> This only works if 2-Step Verification is enabled on your Google account.

1. Open https://myaccount.google.com/apppasswords
2. Click **"Select app"** → choose "Mail"
3. Click **"Select device"** → choose "Other" → type "Kleinanzeigen Scraper"
4. Click **Generate**
5. Copy the 16-character password shown (you won't see it again)

---

### Step 3 – Add Secrets to GitHub

Go to your repository → **Settings → Secrets and variables → Actions → New repository secret**

Add these three secrets:

| Secret name               | Value                                    |
|---------------------------|------------------------------------------|
| `GMAIL_USER`              | your Gmail address (e.g. you@gmail.com)  |
| `GMAIL_APP_PASSWORD`      | the 16-char App Password from Step 2     |
| `RECIPIENT_EMAIL`         | address that should receive the alerts   |

---

### Step 4 – Enable GitHub Actions

1. In your repository, click the **Actions** tab
2. If prompted, click **"I understand my workflows, go ahead and enable them"**
3. The workflow runs automatically every hour (at :05 past).

To test immediately:
- Actions tab → **"Galaxy Fold Kleinanzeigen Scraper"** → **"Run workflow"**

---

### Step 5 – Verify the first run

1. Watch the Actions tab for a green checkmark
2. Check your inbox for the first email
3. After the run, `data/previous_results.json` will be committed to the repo

---

## Configuration

Edit `.github/workflows/scrape.yml` to adjust:

| Variable                   | Default | Meaning                                              |
|----------------------------|---------|------------------------------------------------------|
| `SEND_ALWAYS_INTERVAL_HOURS` | `6`   | Send full summary every N hours even without new ads |
| `cron: '5 * * * *'`        | hourly  | Change to e.g. `'5 */2 * * *'` for every 2 hours   |

Edit `scraper.py` to adjust:

| Constant          | Default | Meaning                        |
|-------------------|---------|--------------------------------|
| `PAGES_TO_SCRAPE` | `2`     | Number of result pages to read |

---

## Email format

Each email contains:

- **Summary bar** – total listings, new listings, timestamp
- **"Neue Angebote" block** – only shown when new ads were found (highlighted yellow)
- **Per-model tables** – Z Fold 7 → 6 → 5 → 4 → 3 → …, sorted by price ascending
  - Columns: Preis | Titel | Speicher | Ort | Entfernung | Zustand | Link

---

## Troubleshooting

| Symptom | Likely cause | Fix |
|---------|-------------|-----|
| No listings scraped | Cloudflare / bot protection | See *Anti-bot note* below |
| SMTP error 535 | Wrong App Password | Regenerate in Google account |
| Workflow not running | Actions not enabled | Check Actions tab |
| No push after run | Token permissions | See *Permissions* note below |

### Anti-bot note

kleinanzeigen.de may return a CAPTCHA or empty page if the `requests`-based
scraper is blocked. If you see `No listings scraped` in the logs:

1. Open `scraper.py`
2. Install `cloudscraper`:  add `cloudscraper>=1.2.71` to `requirements.txt`
3. Replace the `requests.get(...)` call in `scrape_page()` with:
   ```python
   import cloudscraper
   scraper_session = cloudscraper.create_scraper()
   r = scraper_session.get(url, timeout=30)
   ```

### Permissions note

If the git push step fails with a 403, you may need a Personal Access Token:

1. GitHub → Settings → Developer settings → Personal access tokens → Fine-grained tokens
2. Grant **Contents: read & write** for your repository
3. Add it as a repo secret named `GH_PAT`
4. In `scrape.yml`, change `token: ${{ secrets.GITHUB_TOKEN }}` to `token: ${{ secrets.GH_PAT }}`

---

## File structure

```
.
├── scraper.py                        ← main script (scrape + email)
├── requirements.txt
├── .gitignore
├── .github/
│   └── workflows/
│       └── scrape.yml                ← hourly cron schedule
└── data/
    ├── .gitkeep                      ← ensures data/ is tracked
    └── previous_results.json         ← auto-generated; persists state
```

---

## Credentials required from you

To get started you only need to provide (as GitHub Secrets):

1. **`GMAIL_USER`** – sender Gmail address
2. **`GMAIL_APP_PASSWORD`** – 16-char App Password (NOT your Gmail login)
3. **`RECIPIENT_EMAIL`** – where alerts should go (can be the same address)

Kleinanzeigen.de credentials are **not** needed – results are publicly visible.
A cloud platform account is GitHub (free tier is sufficient).
