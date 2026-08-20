"""
Daily Amazon bestseller rank tracker.

Fetches configured bestseller category pages, parses the ranked list,
logs everything to data/rankings.csv, diffs today vs. the most recent
prior snapshot for the watched brand, and posts a Slack digest.

IMPORTANT — read before relying on this:
- Amazon actively rate-limits and CAPTCHA-gates automated requests, and
  its page structure changes periodically. This script WILL need
  maintenance over time. If it starts returning zero items for a
  category, that almost always means either (a) Amazon served a CAPTCHA
  page instead of real content, or (b) the CSS selectors below are out
  of date and need to be re-checked against the live page.
- Scraping Amazon's pages is against their Conditions of Use. This
  script is written for light, once-daily personal/business monitoring
  of public bestseller rankings -- not for aggressive polling or resale
  of the data. Use at your own judgment and risk.
"""

import csv
import os
import sys
import time
import yaml
import requests
from datetime import datetime, timezone
from bs4 import BeautifulSoup

HEADERS = {
    "User-Agent": (
        "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
        "(KHTML, like Gecko) Chrome/124.0.0.0 Safari/537.36"
    ),
    "Accept-Language": "en-IN,en;q=0.9",
}

CSV_PATH = os.path.join(os.path.dirname(__file__), "data", "rankings.csv")
CSV_FIELDS = ["date", "category", "rank", "asin", "title", "price", "rating", "review_count"]


def load_config():
    config_path = os.path.join(os.path.dirname(__file__), "config.yaml")
    with open(config_path, "r") as f:
        return yaml.safe_load(f)


DEBUG_DIR = os.path.join(os.path.dirname(__file__), "debug_html")


def fetch_page(url, page_num=1):
    full_url = url if page_num == 1 else f"{url.rstrip('/')}/?pg={page_num}"
    resp = requests.get(full_url, headers=HEADERS, timeout=20)
    print(f"  -> {full_url} responded with status {resp.status_code}, {len(resp.text)} bytes")
    resp.raise_for_status()
    return resp.text


def save_debug_html(category_name, page_num, html):
    os.makedirs(DEBUG_DIR, exist_ok=True)
    safe_name = "".join(c if c.isalnum() else "_" for c in category_name)
    path = os.path.join(DEBUG_DIR, f"{safe_name}_page{page_num}.html")
    with open(path, "w", encoding="utf-8") as f:
        f.write(html)


def looks_like_captcha(html):
    lowered = html.lower()
    return "captcha" in lowered or "enter the characters you see below" in lowered


def parse_bestseller_page(html):
    """
    Parses a bestseller listing page into a list of dicts.

    Each product sits inside a wrapper div carrying a `data-asin` attribute,
    which is the most reliable way to get the ASIN (no URL-parsing needed).
    The rank badge is a sibling of the product card within that same wrapper.
    Confirmed against real Amazon.in bestseller pages on 2026-08-20 -- if
    this ever returns 0 items again, Amazon has likely changed the layout
    and these selectors need re-checking the same way (inspect a real page's
    HTML and find the new class names).
    """
    soup = BeautifulSoup(html, "html.parser")
    items = []

    wrappers = soup.select("div[data-asin]")
    for wrapper in wrappers:
        try:
            asin = wrapper.get("data-asin")
            if not asin:
                continue

            rank_el = wrapper.select_one("span.zg-bdg-text")
            rank = None
            if rank_el:
                rank = int("".join(ch for ch in rank_el.get_text() if ch.isdigit()) or 0)

            title_el = wrapper.select_one(
                "div._cDEzb_p13n-sc-css-line-clamp-3_g3dy1, .p13n-sc-truncate"
            )
            price_el = wrapper.select_one("span.a-color-price")
            rating_el = wrapper.select_one("span.a-icon-alt")
            review_el = wrapper.select_one("div.a-icon-row span.a-size-small")

            title = title_el.get_text(strip=True) if title_el else None

            if not (rank and asin and title):
                continue

            items.append({
                "rank": rank,
                "asin": asin,
                "title": title,
                "price": price_el.get_text(strip=True) if price_el else "",
                "rating": rating_el.get_text(strip=True) if rating_el else "",
                "review_count": review_el.get_text(strip=True) if review_el else "",
            })
        except Exception as e:
            print(f"  ! skipped one card due to parse error: {e}", file=sys.stderr)
            continue

    return items


def scrape_category(name, url):
    all_items = []
    for page in (1, 2):  # bestseller lists are typically top 100 across 2 pages
        try:
            html = fetch_page(url, page)
        except requests.RequestException as e:
            print(f"  ! request failed for {name} page {page}: {e}", file=sys.stderr)
            break

        save_debug_html(name, page, html)

        if looks_like_captcha(html):
            print(f"  ! got a CAPTCHA/block page for {name} page {page} -- skipping", file=sys.stderr)
            break

        items = parse_bestseller_page(html)
        if not items:
            print(f"  ! selectors matched 0 items for {name} page {page} -- "
                  f"see debug_html/ artifact to inspect the raw page", file=sys.stderr)
            break
        all_items.extend(items)
        time.sleep(2)  # be gentle between requests

    return all_items


def append_to_csv(rows):
    file_exists = os.path.exists(CSV_PATH)
    with open(CSV_PATH, "a", newline="") as f:
        writer = csv.DictWriter(f, fieldnames=CSV_FIELDS)
        if not file_exists:
            writer.writeheader()
        writer.writerows(rows)


def read_all_csv():
    if not os.path.exists(CSV_PATH):
        return []
    with open(CSV_PATH, "r", newline="") as f:
        return list(csv.DictReader(f))


def is_watched(item, watch_cfg):
    if item["asin"] in watch_cfg.get("asins", []):
        return True
    title_lower = item["title"].lower()
    return any(kw.lower() in title_lower for kw in watch_cfg.get("brand_title_keywords", []))


def build_digest(today_str, categories, today_rows, all_rows, watch_cfg, alert_cfg):
    """Builds a Slack message summarizing today's watched-brand ranks and notable moves."""
    lines = [f"*Amazon bestseller check-in — {today_str}*"]
    any_alert = False

    prior_by_key = {}
    for row in all_rows:
        if row["date"] < today_str:
            key = (row["category"], row["asin"])
            if key not in prior_by_key or row["date"] > prior_by_key[key]["date"]:
                prior_by_key[key] = row

    for cat in categories:
        cat_name = cat["name"]
        watched_today = [r for r in today_rows if r["category"] == cat_name and is_watched(r, watch_cfg)]

        if not watched_today:
            lines.append(f"\n*{cat_name}*: not found in today's top listings")
            continue

        for row in watched_today:
            rank = int(row["rank"])
            prior = prior_by_key.get((cat_name, row["asin"]))
            change_note = ""
            if prior:
                prior_rank = int(prior["rank"])
                delta = prior_rank - rank  # positive = moved up
                if abs(delta) >= alert_cfg["rank_move_threshold"]:
                    any_alert = True
                    direction = "up" if delta > 0 else "down"
                    change_note = f" — moved {direction} {abs(delta)} spots (was #{prior_rank})"
                elif delta != 0:
                    change_note = f" (was #{prior_rank})"
            else:
                change_note = " (first time logged)"

            for tier in alert_cfg.get("notify_on_top_n_exit", []):
                if prior and int(prior["rank"]) <= tier < rank:
                    any_alert = True
                    change_note += f" ⚠️ dropped out of top {tier}"

            for tier in alert_cfg.get("notify_on_new_top_n_entry", []):
                if rank <= tier and (not prior or int(prior["rank"]) > tier):
                    any_alert = True
                    change_note += f" 🎉 entered top {tier}"

            lines.append(f"\n*{cat_name}*: #{rank} — {row['title'][:60]}{change_note}")

    lines.append(f"\n_Logged {len(today_rows)} listings across {len(categories)} categories._")
    return "\n".join(lines), any_alert


def post_to_slack(message):
    webhook_url = os.environ.get("SLACK_WEBHOOK_URL")
    if not webhook_url:
        print("SLACK_WEBHOOK_URL not set -- skipping Slack post, printing digest instead:\n")
        print(message)
        return
    resp = requests.post(webhook_url, json={"text": message}, timeout=10)
    if resp.status_code != 200:
        print(f"Slack post failed: {resp.status_code} {resp.text}", file=sys.stderr)


def main():
    config = load_config()
    today_str = datetime.now(timezone.utc).strftime("%Y-%m-%d")

    today_rows = []
    for cat in config["categories"]:
        print(f"Scraping: {cat['name']}")
        items = scrape_category(cat["name"], cat["url"])
        print(f"  found {len(items)} items")
        for item in items:
            today_rows.append({
                "date": today_str,
                "category": cat["name"],
                "rank": item["rank"],
                "asin": item["asin"],
                "title": item["title"],
                "price": item["price"],
                "rating": item["rating"],
                "review_count": item["review_count"],
            })

    if today_rows:
        append_to_csv(today_rows)
    else:
        print("No data scraped today -- likely blocked or selectors need updating.", file=sys.stderr)

    all_rows = read_all_csv()
    digest, had_alert = build_digest(
        today_str, config["categories"], today_rows, all_rows, config["watch"], config["alerts"]
    )
    post_to_slack(digest)
    print("\n--- Digest sent ---\n" + digest)


if __name__ == "__main__":
    main()
