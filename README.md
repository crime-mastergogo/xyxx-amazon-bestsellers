# Amazon bestseller rank tracker

Daily tracker for XYXX Apparels' rank in Amazon.in's Men's Clothing & Accessories
bestseller categories. Runs on GitHub Actions, logs to a CSV in this repo, and
sends a Slack digest.

## Before you rely on this, please read

- **Amazon's Conditions of Use prohibit automated scraping of their pages.**
  This is built for light, once-a-day monitoring of public bestseller rankings.
  It is not risk-free -- Amazon may block the requests, and there's no formal
  permission being granted here. Use your own judgment.
- **This will break periodically.** Amazon changes its page HTML from time to
  time, and the CSS selectors in `main.py` are written against the layout as
  of today. When the script logs "found 0 items," that almost always means
  either Amazon served a CAPTCHA page, or the selectors are stale and need
  updating -- open the bestseller page in a browser, inspect one product
  card's HTML, and adjust `parse_bestseller_page()` accordingly.
- **Running from GitHub's shared IP ranges makes blocking more likely** than
  scraping from a residential IP would. If you see persistent CAPTCHA blocks,
  the realistic options are: reduce frequency, add a proxy service, or fall
  back to a paid rank-tracking API (Keepa, SellerApp, Helium10, etc.) for
  reliability.

## Setup

1. **Create a Slack incoming webhook**
   In Slack: create an app (or use an existing one) with an Incoming Webhook
   for the channel you want the digest posted to. Copy the webhook URL.

2. **Add it as a repo secret**
   In your GitHub repo: Settings → Secrets and variables → Actions → New
   repository secret.
   - Name: `SLACK_WEBHOOK_URL`
   - Value: the webhook URL from step 1

3. **Edit `config.yaml`**
   - Rename the placeholder `"Category ####"` entries to their real names
     once you've confirmed them.
   - Add your product ASINs under `watch.asins` -- this is far more reliable
     than title keyword matching, since titles can be edited or duplicated
     across sellers.
   - Adjust `alerts` thresholds to match how sensitive you want the Slack
     pings to be. Everything still gets logged to CSV regardless of alerts.

4. **Push this repo to GitHub** and enable Actions if prompted.

5. **Test it manually** before waiting for the schedule: go to the Actions
   tab → "Daily bestseller tracker" → "Run workflow." Check the run logs to
   confirm it found items and posted to Slack, and check `data/rankings.csv`
   for the new rows.

## Files

- `main.py` — scrape, log, diff, notify
- `config.yaml` — categories to track, brand identifiers, alert thresholds
- `data/rankings.csv` — the growing historical log (this is your trend data)
- `.github/workflows/daily-track.yml` — the schedule and CI job

## Weekly trend review

Since every day's data lands in `data/rankings.csv`, you can open it in
Excel/Google Sheets at any time (or pull it into a pivot table / chart) to
see rank trends over the week rather than relying on daily snapshots alone.
