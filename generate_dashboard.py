"""
Generates the weekly BSR dashboard as static HTML pages.

Reads the accumulated data/rankings.csv, takes the most recent 7 days of
snapshots, and builds:
  - index.html: day-by-day movement table for XYXX products per category,
    each row with a sparkline chart of its rank across the week, plus a
    full top-20 competitive grid for the most recent day.
  - competitors.html: for each category, auto-picks the 3-5 rival brands
    whose average rank sits closest to XYXX's average rank that week, and
    charts XYXX vs. those competitors' rank trends across the week.
  - archive.html / archive/<date>.html: permanent copies of past weeks.

Chart.js (via CDN) renders the visuals since this is a static page, not a
sandboxed widget -- no CDN restrictions apply here.

Output goes to docs/, intended to be served via GitHub Pages pointed at
the /docs folder.
"""

import csv
import os
import json
import html as html_escape
from datetime import datetime
from collections import defaultdict

import yaml

BASE_DIR = os.path.dirname(__file__)
CSV_PATH = os.path.join(BASE_DIR, "data", "rankings.csv")
DOCS_DIR = os.path.join(BASE_DIR, "docs")
ARCHIVE_DIR = os.path.join(DOCS_DIR, "archive")

NUM_COMPETITORS_MIN = 3
NUM_COMPETITORS_MAX = 5


def load_config():
    with open(os.path.join(BASE_DIR, "config.yaml")) as f:
        return yaml.safe_load(f)


def load_rows():
    if not os.path.exists(CSV_PATH):
        return []
    with open(CSV_PATH, newline="") as f:
        return list(csv.DictReader(f))


def is_watched(row, watch_cfg):
    if row["asin"] in watch_cfg.get("asins", []):
        return True
    title = (row.get("title") or "").lower()
    return any(kw.lower() in title for kw in watch_cfg.get("brand_title_keywords", []))


def brand_of(title):
    if not title:
        return "Unknown"
    return " ".join(title.split()[:2])


def build_week_data(rows, watch_cfg, num_days=7):
    all_dates = sorted(set(r["date"] for r in rows))
    week_dates = all_dates[-num_days:]
    if not week_dates:
        return None

    latest_date = week_dates[-1]
    by_category = defaultdict(list)
    for r in rows:
        by_category[r["category"]].append(r)

    categories = []
    for cat_name, cat_rows in by_category.items():
        watched_by_asin = defaultdict(dict)
        meta_by_asin = {}
        for r in cat_rows:
            if r["date"] not in week_dates or not is_watched(r, watch_cfg):
                continue
            watched_by_asin[r["asin"]][r["date"]] = int(r["rank"])
            meta_by_asin[r["asin"]] = r

        movement_rows = []
        for asin, day_ranks in watched_by_asin.items():
            meta = meta_by_asin[asin]
            ranks_in_order = [day_ranks.get(d) for d in week_dates]
            first_seen = next((rk for rk in ranks_in_order if rk is not None), None)
            last_seen = next((rk for rk in reversed(ranks_in_order) if rk is not None), None)
            trend = None
            if first_seen is not None and last_seen is not None and first_seen != last_seen:
                trend = first_seen - last_seen
            movement_rows.append({
                "asin": asin,
                "title": meta["title"],
                "image_url": meta.get("image_url", ""),
                "day_ranks": ranks_in_order,
                "latest_rank": last_seen,
                "trend": trend,
            })
        movement_rows.sort(key=lambda x: (x["latest_rank"] is None, x["latest_rank"]))

        latest_rows = [r for r in cat_rows if r["date"] == latest_date]
        latest_rows.sort(key=lambda r: int(r["rank"]))
        top_grid = latest_rows[:20]

        categories.append({
            "name": cat_name,
            "movement_rows": movement_rows,
            "top_grid": top_grid,
            "_cat_rows": cat_rows,
        })

    categories.sort(key=lambda c: c["name"])
    return {
        "week_dates": week_dates,
        "latest_date": latest_date,
        "categories": categories,
    }


def build_competitor_analysis(cat_rows, week_dates, watch_cfg):
    by_date = defaultdict(list)
    for r in cat_rows:
        if r["date"] in week_dates:
            by_date[r["date"]].append(r)

    xyxx_daily_best = {}
    for d in week_dates:
        xyxx_ranks = [int(r["rank"]) for r in by_date.get(d, []) if is_watched(r, watch_cfg)]
        xyxx_daily_best[d] = min(xyxx_ranks) if xyxx_ranks else None

    present_xyxx = [v for v in xyxx_daily_best.values() if v is not None]
    if not present_xyxx:
        return None
    xyxx_avg = sum(present_xyxx) / len(present_xyxx)

    brand_daily_best = defaultdict(dict)
    for d in week_dates:
        for r in by_date.get(d, []):
            if is_watched(r, watch_cfg):
                continue
            brand = brand_of(r.get("title"))
            rank = int(r["rank"])
            if brand not in brand_daily_best[d] or rank < brand_daily_best[d][brand]:
                brand_daily_best[d][brand] = rank

    all_brands = set()
    for d in week_dates:
        all_brands.update(brand_daily_best[d].keys())

    brand_stats = {}
    for brand in all_brands:
        series = [brand_daily_best[d].get(brand) for d in week_dates]
        present = [v for v in series if v is not None]
        if len(present) < 2:
            continue
        avg_rank = sum(present) / len(present)
        brand_stats[brand] = {
            "brand": brand,
            "series": series,
            "avg_rank": avg_rank,
            "days_present": len(present),
            "proximity": abs(avg_rank - xyxx_avg),
        }

    consistent = [b for b in brand_stats.values() if b["days_present"] >= max(2, len(week_dates) // 2)]
    pool = consistent if len(consistent) >= NUM_COMPETITORS_MIN else list(brand_stats.values())
    pool.sort(key=lambda b: b["proximity"])
    competitors = pool[:NUM_COMPETITORS_MAX]

    return {
        "xyxx_series": [xyxx_daily_best[d] for d in week_dates],
        "xyxx_avg": xyxx_avg,
        "competitors": competitors,
    }


def fmt_date_short(d):
    return datetime.strptime(d, "%Y-%m-%d").strftime("%a %d")


def fmt_date_long(d):
    return datetime.strptime(d, "%Y-%m-%d").strftime("%a, %d %b %Y")


def product_url(asin):
    return f"https://www.amazon.in/dp/{asin}"


def slugify(text):
    return "".join(c if c.isalnum() else "-" for c in text.lower()).strip("-")


def render_trend_cell(rank):
    if rank is None:
        return '<span class="cell-empty">&mdash;</span>'
    return f'<span class="cell-rank">#{rank}</span>'


def render_movement_table(category, week_dates):
    if not category["movement_rows"]:
        return "", []

    header_cells = "".join(f"<th>{fmt_date_short(d)}</th>" for d in week_dates)
    body_rows = []
    chart_specs = []

    for i, row in enumerate(category["movement_rows"]):
        cells = "".join(f"<td>{render_trend_cell(r)}</td>" for r in row["day_ranks"])
        if row["trend"] is None:
            trend_html = '<span class="trend-flat">flat</span>'
        elif row["trend"] > 0:
            trend_html = f'<span class="trend-up">&#9650; {row["trend"]}</span>'
        else:
            trend_html = f'<span class="trend-down">&#9660; {abs(row["trend"])}</span>'

        chart_id = f"spark-{slugify(category['name'])}-{i}"
        chart_specs.append({"id": chart_id, "series": row["day_ranks"]})

        title = html_escape.escape(row["title"])
        body_rows.append(
            '<tr><td class="product-cell">'
            f'<img src="{row["image_url"]}" alt="" class="thumb" />'
            f'<a href="{product_url(row["asin"])}" target="_blank" class="product-title">{title}</a>'
            f'</td>{cells}<td class="trend-cell">{trend_html}</td>'
            f'<td class="spark-cell"><canvas id="{chart_id}" width="90" height="28"></canvas></td></tr>'
        )

    table_html = (
        '<table class="movement-table"><thead><tr>'
        f'<th class="product-col">Product</th>{header_cells}<th>Week trend</th><th>Chart</th>'
        f'</tr></thead><tbody>{"".join(body_rows)}</tbody></table>'
    )
    return table_html, chart_specs


def render_top_grid(category, watch_cfg):
    cards = []
    for r in category["top_grid"]:
        is_xyxx = is_watched(r, watch_cfg)
        title = html_escape.escape(r["title"])
        brand = html_escape.escape(brand_of(r["title"]))
        badge = '<span class="you-badge">your brand</span>' if is_xyxx else ""
        card_class = "grid-card xyxx" if is_xyxx else "grid-card"
        cards.append(
            f'<div class="{card_class}">'
            f'<img src="{r.get("image_url", "")}" alt="" class="grid-thumb" />'
            '<div class="grid-body">'
            f'<div class="grid-top-row"><span class="grid-rank">#{r["rank"]}</span>'
            f'<span class="grid-brand">{brand}</span>{badge}</div>'
            f'<a href="{product_url(r["asin"])}" target="_blank" class="grid-title">{title}</a>'
            f'<div class="grid-meta">{html_escape.escape(r.get("price") or "")} &middot; '
            f'{html_escape.escape(r.get("rating") or "")} ({html_escape.escape(r.get("review_count") or "0")})</div>'
            '</div></div>'
        )
    return f'<div class="grid">{"".join(cards)}</div>'


PAGE_CSS = """
* { box-sizing: border-box; }
body {
  margin: 0;
  font-family: 'Inter', -apple-system, BlinkMacSystemFont, 'Segoe UI', sans-serif;
  background: #fafaf9;
  color: #1a1a18;
}
.wrap { max-width: 1080px; margin: 0 auto; padding: 40px 24px 80px; }
.top-nav { display: flex; justify-content: space-between; align-items: baseline; margin-bottom: 32px; }
.brand { font-size: 15px; font-weight: 600; letter-spacing: -0.01em; }
.nav-links a { color: #6b6b66; text-decoration: none; font-size: 14px; margin-left: 20px; }
.nav-links a.active { color: #1a1a18; font-weight: 600; }
.nav-links a:hover { color: #1a1a18; }
.hero-label { font-size: 12px; letter-spacing: 0.08em; text-transform: uppercase; color: #8a8a84; margin-bottom: 6px; }
.hero-date { font-size: 30px; font-weight: 600; letter-spacing: -0.02em; margin: 0 0 6px; }
.hero-sub { color: #6b6b66; font-size: 14px; margin-bottom: 40px; }
.category-section { margin-bottom: 48px; }
.category-header { display: flex; justify-content: space-between; align-items: baseline; margin-bottom: 14px; border-bottom: 1px solid #e5e4e0; padding-bottom: 10px; }
.category-title { font-size: 18px; font-weight: 600; letter-spacing: -0.01em; }
.movement-table { width: 100%; border-collapse: collapse; margin-bottom: 20px; font-size: 13px; }
.movement-table th { text-align: left; font-weight: 500; color: #8a8a84; font-size: 11px; text-transform: uppercase; letter-spacing: 0.04em; padding: 6px 8px; border-bottom: 1px solid #e5e4e0; }
.movement-table td { padding: 8px; border-bottom: 1px solid #f0efec; vertical-align: middle; }
.product-col { min-width: 220px; }
.product-cell { display: flex; align-items: center; gap: 10px; }
.thumb { width: 32px; height: 32px; object-fit: contain; border-radius: 4px; flex-shrink: 0; background: #fff; border: 1px solid #eee; }
.product-title { color: #1a1a18; text-decoration: none; font-size: 13px; line-height: 1.3; }
.product-title:hover { color: #3d5afe; }
.cell-rank { font-weight: 500; }
.cell-empty { color: #c4c3be; }
.trend-up { color: #1a7f4b; font-weight: 500; font-size: 12px; }
.trend-down { color: #c23b3b; font-weight: 500; font-size: 12px; }
.trend-flat { color: #8a8a84; font-size: 12px; }
.empty-note { color: #8a8a84; font-size: 13px; font-style: italic; margin: 8px 0 20px; }
.grid { display: grid; grid-template-columns: repeat(auto-fill, minmax(220px, 1fr)); gap: 12px; }
.grid-card { background: #fff; border: 1px solid #e5e4e0; border-radius: 10px; padding: 10px; display: flex; gap: 10px; }
.grid-card.xyxx { border: 1.5px solid #3d5afe; }
.grid-thumb { width: 48px; height: 48px; object-fit: contain; border-radius: 6px; flex-shrink: 0; }
.grid-body { min-width: 0; }
.grid-top-row { display: flex; align-items: center; gap: 6px; margin-bottom: 3px; flex-wrap: wrap; }
.grid-rank { font-size: 11px; color: #8a8a84; }
.grid-brand { font-size: 12px; font-weight: 600; }
.you-badge { font-size: 10px; background: #e8ebff; color: #3d5afe; padding: 1px 6px; border-radius: 4px; }
.grid-title { display: block; font-size: 12px; color: #1a1a18; text-decoration: none; line-height: 1.35; margin-bottom: 4px; }
.grid-title:hover { color: #3d5afe; }
.grid-meta { font-size: 11px; color: #6b6b66; }
.footer-note { color: #8a8a84; font-size: 12px; margin-top: 60px; border-top: 1px solid #e5e4e0; padding-top: 16px; }
.comp-chart-wrap { background: #fff; border: 1px solid #e5e4e0; border-radius: 10px; padding: 16px; margin-bottom: 16px; }
.comp-legend-note { font-size: 12px; color: #6b6b66; margin-bottom: 10px; }
.comp-takeaway { font-size: 13px; color: #1a1a18; margin-top: 10px; padding-top: 10px; border-top: 1px solid #f0efec; }
"""

NAV_HTML = (
    '<div class="top-nav"><span class="brand">XYXX &middot; Amazon.in BSR Tracker</span>'
    '<span class="nav-links">'
    '<a href="index.html" class="{latest_cls}">Latest</a>'
    '<a href="competitors.html" class="{comp_cls}">Competitors</a>'
    '<a href="archive.html" class="{arch_cls}">Archive</a>'
    '</span></div>'
)


def nav(active):
    return NAV_HTML.format(
        latest_cls="active" if active == "latest" else "",
        comp_cls="active" if active == "competitors" else "",
        arch_cls="active" if active == "archive" else "",
    )


def build_sparkline_script(chart_specs):
    lines = []
    for spec in chart_specs:
        series = spec["series"]
        data_json = json.dumps(series)
        lines.append(
            "new Chart(document.getElementById('" + spec["id"] + "'), {"
            "type: 'line', data: { labels: " + data_json + ".map((_, i) => i), "
            "datasets: [{ data: " + data_json + ", borderColor: '#3d5afe', "
            "backgroundColor: 'transparent', borderWidth: 1.5, "
            "pointRadius: " + data_json + ".map((v, i) => i === " + data_json + ".length - 1 ? 2.5 : 0), "
            "pointBackgroundColor: '#3d5afe', tension: 0.3, spanGaps: true }] }, "
            "options: { responsive: false, animation: false, "
            "scales: { x: { display: false }, y: { display: false, reverse: true } }, "
            "plugins: { legend: { display: false }, tooltip: { enabled: false } } } });"
        )
    return "\n".join(lines)


def render_page(week_data, categories_rendered):
    date_range = f"{fmt_date_short(week_data['week_dates'][0])} &ndash; {fmt_date_long(week_data['latest_date'])}"
    sections = []
    all_chart_specs = []
    for cat, movement_html, grid_html, chart_specs in categories_rendered:
        all_chart_specs.extend(chart_specs)
        sections.append(
            '<div class="category-section">'
            '<div class="category-header">'
            f'<span class="category-title">{html_escape.escape(cat["name"])}</span>'
            f'</div>{movement_html}{grid_html}</div>'
        )

    chart_script = build_sparkline_script(all_chart_specs)

    return (
        '<!doctype html><html lang="en"><head><meta charset="utf-8" />'
        '<meta name="viewport" content="width=device-width, initial-scale=1" />'
        '<title>XYXX Amazon.in BSR Tracker</title>'
        '<link rel="preconnect" href="https://fonts.googleapis.com">'
        '<link href="https://fonts.googleapis.com/css2?family=Inter:wght@400;500;600&display=swap" rel="stylesheet">'
        f'<style>{PAGE_CSS}</style></head><body><div class="wrap">'
        f'{nav("latest")}'
        '<div class="hero-label">Weekly snapshot</div>'
        f'<h1 class="hero-date">{date_range}</h1>'
        f'<div class="hero-sub">{len(week_data["categories"])} categories tracked &middot; '
        'day-by-day movement for XYXX products this week</div>'
        f'{"".join(sections)}'
        '<div class="footer-note">Generated automatically each day from Amazon.in bestseller data. '
        'Rank data only, prices and ratings may lag slightly behind the live listing.</div>'
        '</div>'
        '<script src="https://cdn.jsdelivr.net/npm/chart.js@4"></script>'
        f'<script>{chart_script}</script>'
        '</body></html>'
    )


def build_competitor_chart_script(category_name, week_dates, comp_data):
    chart_id = f"comp-{slugify(category_name)}"
    labels = json.dumps([fmt_date_short(d) for d in week_dates])
    datasets = [{
        "label": "XYXX",
        "data": comp_data["xyxx_series"],
        "borderColor": "#3d5afe",
        "backgroundColor": "transparent",
        "borderWidth": 2.5,
        "tension": 0.25,
        "spanGaps": True,
    }]
    palette = ["#c23b3b", "#e08a2b", "#1a7f4b", "#8a5cf6", "#6b6b66"]
    for i, comp in enumerate(comp_data["competitors"]):
        datasets.append({
            "label": comp["brand"],
            "data": comp["series"],
            "borderColor": palette[i % len(palette)],
            "backgroundColor": "transparent",
            "borderWidth": 1.5,
            "tension": 0.25,
            "spanGaps": True,
        })

    datasets_json = json.dumps(datasets)
    script = (
        "new Chart(document.getElementById('" + chart_id + "'), {"
        "type: 'line', data: { labels: " + labels + ", datasets: " + datasets_json + " }, "
        "options: { responsive: true, maintainAspectRatio: false, animation: false, "
        "scales: { x: { grid: { display: false } }, "
        "y: { reverse: true, title: { display: true, text: 'Rank (lower is better)' }, ticks: { precision: 0 } } }, "
        "plugins: { legend: { position: 'bottom', labels: { boxWidth: 10, font: { size: 11 } } } } } });"
    )
    return chart_id, script


def render_competitors_page(week_data, watch_cfg):
    sections = []
    scripts = []
    for cat in week_data["categories"]:
        comp_data = build_competitor_analysis(cat["_cat_rows"], week_data["week_dates"], watch_cfg)
        if comp_data is None:
            sections.append(
                f'<div class="category-section"><div class="category-header">'
                f'<span class="category-title">{html_escape.escape(cat["name"])}</span></div>'
                '<p class="empty-note">No XYXX products found in this category this week.</p></div>'
            )
            continue

        chart_id, script = build_competitor_chart_script(cat["name"], week_data["week_dates"], comp_data)
        scripts.append(script)

        comp_names = ", ".join(c["brand"] for c in comp_data["competitors"])
        closest = comp_data["competitors"][0] if comp_data["competitors"] else None
        takeaway = ""
        if closest:
            gap = closest["avg_rank"] - comp_data["xyxx_avg"]
            if gap > 0:
                takeaway = (
                    f'XYXX averaged rank {comp_data["xyxx_avg"]:.1f} this week, '
                    f'{gap:.1f} spots ahead of {closest["brand"]} '
                    f'(avg rank {closest["avg_rank"]:.1f}), the closest rival.'
                )
            else:
                takeaway = (
                    f'XYXX averaged rank {comp_data["xyxx_avg"]:.1f} this week, '
                    f'{abs(gap):.1f} spots behind {closest["brand"]} '
                    f'(avg rank {closest["avg_rank"]:.1f}), the closest rival.'
                )

        sections.append(
            '<div class="category-section">'
            '<div class="category-header">'
            f'<span class="category-title">{html_escape.escape(cat["name"])}</span>'
            '</div>'
            f'<div class="comp-legend-note">XYXX vs. closest competitors by average rank: {html_escape.escape(comp_names)}</div>'
            '<div class="comp-chart-wrap">'
            f'<div style="height:260px;"><canvas id="{chart_id}"></canvas></div>'
            f'<div class="comp-takeaway">{html_escape.escape(takeaway)}</div>'
            '</div></div>'
        )

    return (
        '<!doctype html><html lang="en"><head><meta charset="utf-8" />'
        '<meta name="viewport" content="width=device-width, initial-scale=1" />'
        '<title>XYXX Amazon.in BSR Tracker - Competitors</title>'
        '<link href="https://fonts.googleapis.com/css2?family=Inter:wght@400;500;600&display=swap" rel="stylesheet">'
        f'<style>{PAGE_CSS}</style></head><body><div class="wrap">'
        f'{nav("competitors")}'
        '<div class="hero-label">Competitor analysis</div>'
        '<h1 class="hero-date">XYXX vs. closest rivals</h1>'
        '<div class="hero-sub">Auto-picked competitors whose average rank sits closest to XYXX this week</div>'
        f'{"".join(sections)}'
        '</div>'
        '<script src="https://cdn.jsdelivr.net/npm/chart.js@4"></script>'
        f'<script>{"".join(scripts)}</script>'
        '</body></html>'
    )


def render_archive_index(archive_dates):
    items = "".join(
        f'<li><a href="archive/{d}.html">{fmt_date_long(d)}</a></li>' for d in reversed(archive_dates)
    )
    extra_css = (
        "ul { list-style: none; padding: 0; } "
        "li { padding: 10px 0; border-bottom: 1px solid #e5e4e0; } "
        "li a { color: #1a1a18; text-decoration: none; font-size: 14px; } "
        "li a:hover { color: #3d5afe; }"
    )
    return (
        '<!doctype html><html lang="en"><head><meta charset="utf-8" />'
        '<title>XYXX BSR Tracker - Archive</title>'
        '<link href="https://fonts.googleapis.com/css2?family=Inter:wght@400;500;600&display=swap" rel="stylesheet">'
        f'<style>{PAGE_CSS}{extra_css}</style></head><body><div class="wrap">'
        f'{nav("archive")}'
        f'<h1 class="hero-date">Archive</h1><ul>{items}</ul></div></body></html>'
    )


def main():
    config = load_config()
    rows = load_rows()
    week_data = build_week_data(rows, config["watch"])

    if week_data is None:
        print("No data available yet -- skipping dashboard generation.")
        return

    categories_rendered = []
    for cat in week_data["categories"]:
        movement_html, chart_specs = render_movement_table(cat, week_data["week_dates"])
        grid_html = render_top_grid(cat, config["watch"])
        categories_rendered.append((cat, movement_html, grid_html, chart_specs))

    index_html = render_page(week_data, categories_rendered)
    competitors_html = render_competitors_page(week_data, config["watch"])

    os.makedirs(DOCS_DIR, exist_ok=True)
    os.makedirs(ARCHIVE_DIR, exist_ok=True)

    with open(os.path.join(DOCS_DIR, "index.html"), "w", encoding="utf-8") as f:
        f.write(index_html)
    with open(os.path.join(DOCS_DIR, "competitors.html"), "w", encoding="utf-8") as f:
        f.write(competitors_html)

    archive_path = os.path.join(ARCHIVE_DIR, f"{week_data['latest_date']}.html")
    with open(archive_path, "w", encoding="utf-8") as f:
        f.write(index_html)

    archive_dates = sorted(
        fn[:-5] for fn in os.listdir(ARCHIVE_DIR) if fn.endswith(".html")
    )
    with open(os.path.join(DOCS_DIR, "archive.html"), "w", encoding="utf-8") as f:
        f.write(render_archive_index(archive_dates))

    print(f"Dashboard generated for week ending {week_data['latest_date']}")


if __name__ == "__main__":
    main()
