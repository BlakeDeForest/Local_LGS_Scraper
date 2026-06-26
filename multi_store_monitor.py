#!/usr/bin/env python3
"""
Multi-Store TCG Monitor
=======================

Watches several Australian Shopify-based game stores for One Piece TCG and
Pokemon TCG products and pings a Discord webhook when:

  * a matching product is newly listed, or
  * a matching product comes back in stock (out-of-stock -> in-stock).

Stores monitored (all run on Shopify):
  * Good Games          https://www.goodgames.com.au
  * General Games       https://www.generalgames.com.au
  * Gaming Grounds      https://www.gaminggrounds.com.au
  * HanHan Games        https://hanhangames.com
  * Rhystic Nostalgia   https://rhysticnostalgiagaming.com.au

Why Shopify JSON instead of HTML scraping?
  Every Shopify store exposes a public, structured JSON catalog at
  `/products.json` (and `/collections/<handle>/products.json`). It gives us
  the title, vendor, product type, tags, images, price and — crucially — the
  `available` flag for every variant. That is far more reliable and far
  lighter than rendering pages with a headless browser, and it makes
  restock detection trivial.

Special-interest sets (these trigger a louder, @-mention alert):
  * Pokemon "Ascended Heroes"
  * One Piece OP-17, OP-18
  * One Piece EB-06

Everything else that is One Piece TCG or Pokemon TCG also alerts (normal).

Run it:
  pip install -r requirements.txt
  python multi_store_monitor.py

Configure the Discord webhook below (or via the DISCORD_WEBHOOK_URL env var).
"""

import json
import os
import time
import random
from datetime import datetime

import requests

# ── CONFIG ────────────────────────────────────────────────────────────────────

# Paste your Discord webhook URL here, or set the DISCORD_WEBHOOK_URL env var.
# How to get one: Discord → Server Settings → Integrations → Webhooks →
# "New Webhook" → Copy Webhook URL.  (See README for a step-by-step.)
DISCORD_WEBHOOK_URL = os.environ.get("DISCORD_WEBHOOK_URL", "")

# Optional: your Discord numeric user ID. If set, "special-interest set" alerts
# (Ascended Heroes / OP-17 / OP-18 / EB-06) will @-mention you so your phone
# actually buzzes. Get it: Discord → User Settings → Advanced → enable
# Developer Mode, then right-click your name → "Copy User ID".
DISCORD_USER_ID = os.environ.get("DISCORD_USER_ID", "")

# How often to poll every store, in seconds.
CHECK_INTERVAL_SECONDS = 180  # 3 minutes

# Polite delay between individual HTTP requests (seconds).
REQUEST_DELAY = 1.0

# Max pages of /products.json to walk per collection / catalog (250 items/page).
MAX_PAGES = 25

STATE_FILE = os.path.join(os.path.dirname(os.path.abspath(__file__)), "monitor_state.json")

# Stores to watch. `collections` narrows the scan to specific Shopify collection
# handles (faster, less noise). Leave `collections` empty/None to scan the whole
# catalog via /products.json. Unknown/renamed handles are skipped automatically,
# and if every configured collection fails we fall back to the full catalog.
STORES = [
    {
        "name": "Good Games",
        "base": "https://www.goodgames.com.au",
        "collections": [
            "trading-card-games",
            "coming-soon-trading-card-games",
        ],
    },
    {
        "name": "General Games",
        "base": "https://www.generalgames.com.au",
        "collections": [],  # full-catalog scan, filtered by keyword
    },
    {
        "name": "Gaming Grounds",
        "base": "https://www.gaminggrounds.com.au",
        "collections": [
            "one-piece",
            "pokemon",
        ],
    },
    {
        "name": "HanHan Games",
        "base": "https://hanhangames.com",
        "collections": [],  # full-catalog scan
    },
    {
        "name": "Rhystic Nostalgia Gaming",
        "base": "https://rhysticnostalgiagaming.com.au",
        "collections": [
            "one-piece-sealed-instock",
            "all-one-piece-preorders",
            "pokemon",
        ],
    },
]

# ── MATCHING RULES ──────────────────────────────────────────────────────────

# A product must belong to one of these franchises...
ONE_PIECE_TERMS = ["one piece", "one-piece"]
POKEMON_TERMS = ["pokemon", "pokémon", "pokemon tcg"]

# ...AND look like a trading-card product (not a plushie, figure, video game...).
TCG_TERMS = [
    "tcg", "trading card", "card game", "ccg",
    "booster", "booster box", "booster pack", "booster case",
    "elite trainer", "etb", "trainer box",
    "display", "starter deck", "structure deck", "starter pack",
    "premium collection", "collection box", "ex box", "tin",
    "build & battle", "build and battle", "blister", "sleeved booster",
    "double pack", "gift collection", "extra booster", "ultra deck",
    "surprise box", "scene set", "deluxe pack", "championship pack",
]

# Special-interest sets — louder alert + optional @-mention. Each entry is a
# list of aliases; if ANY alias is found in the product text it matches.
PRIORITY_SETS = {
    "Pokémon — Ascended Heroes": ["ascended heroes"],
    "One Piece — OP-17": ["op-17", "op17", "op 17"],
    "One Piece — OP-18": ["op-18", "op18", "op 18"],
    "One Piece — EB-06": ["eb-06", "eb06", "eb 06"],
}

USER_AGENT = (
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
    "AppleWebKit/537.36 (KHTML, like Gecko) Chrome/124.0.0.0 Safari/537.36"
)

# ─────────────────────────────────────────────────────────────────────────────


def _product_text(product: dict) -> str:
    """Flatten the searchable text of a Shopify product into one lowercase blob."""
    parts = [
        product.get("title", ""),
        product.get("product_type", ""),
        product.get("vendor", ""),
        " ".join(product.get("tags", []) if isinstance(product.get("tags"), list) else [str(product.get("tags", ""))]),
        product.get("handle", ""),
    ]
    return " ".join(p for p in parts if p).lower()


def classify(product: dict):
    """
    Decide whether a product is worth alerting on.

    Returns (matched: bool, franchise: str|None, priority_label: str|None).
    """
    text = _product_text(product)

    is_one_piece = any(t in text for t in ONE_PIECE_TERMS)
    is_pokemon = any(t in text for t in POKEMON_TERMS)
    if not (is_one_piece or is_pokemon):
        return False, None, None

    is_tcg = any(t in text for t in TCG_TERMS)
    # Treat a Shopify product_type that literally says "card game" as TCG.
    ptype = product.get("product_type", "").lower()
    if "card" in ptype:
        is_tcg = True
    if not is_tcg:
        return False, None, None

    franchise = "One Piece" if is_one_piece else "Pokémon"

    priority_label = None
    for label, aliases in PRIORITY_SETS.items():
        if any(a in text for a in aliases):
            priority_label = label
            break

    return True, franchise, priority_label


def product_availability(product: dict):
    """Return (in_stock: bool, price: str) for a Shopify product."""
    variants = product.get("variants", []) or []
    in_stock = any(v.get("available") for v in variants)
    prices = [v.get("price") for v in variants if v.get("price")]
    price = f"${prices[0]}" if prices else "N/A"
    return in_stock, price


def product_image(product: dict) -> str:
    images = product.get("images", []) or []
    if images and isinstance(images[0], dict):
        return images[0].get("src", "")
    return ""


def make_session() -> requests.Session:
    s = requests.Session()
    s.headers.update({
        "User-Agent": USER_AGENT,
        "Accept": "application/json,text/plain,*/*",
        "Accept-Language": "en-AU,en;q=0.9",
    })
    return s


def fetch_json(session: requests.Session, url: str):
    """GET a Shopify *.json endpoint, returning parsed JSON or None."""
    try:
        r = session.get(url, timeout=25)
        if r.status_code == 404:
            return None
        r.raise_for_status()
        return r.json()
    except Exception as e:
        print(f"    [!] fetch failed {url}: {e}")
        return None


def fetch_products_from(session: requests.Session, base: str, path: str) -> list:
    """
    Walk a paginated Shopify products.json endpoint.

    `path` is either "/products.json" or "/collections/<handle>/products.json".
    Returns the full list of product dicts (possibly empty).
    """
    products = []
    for page in range(1, MAX_PAGES + 1):
        url = f"{base}{path}?limit=250&page={page}"
        data = fetch_json(session, url)
        if not data:
            break
        batch = data.get("products", [])
        if not batch:
            break
        products.extend(batch)
        if len(batch) < 250:
            break  # last page
        time.sleep(REQUEST_DELAY)
    return products


def gather_store_products(session: requests.Session, store: dict) -> list:
    """Collect all candidate products for a store, deduped by product id."""
    base = store["base"]
    collections = store.get("collections") or []
    products = []

    if collections:
        for handle in collections:
            path = f"/collections/{handle}/products.json"
            got = fetch_products_from(session, base, path)
            if got:
                print(f"    collection '{handle}': {len(got)} products")
                products.extend(got)
            time.sleep(REQUEST_DELAY)

    # Fall back to (or default to) the full catalog if no collection yielded data.
    if not products:
        print("    scanning full catalog (/products.json)...")
        products = fetch_products_from(session, base, "/products.json")

    # Dedupe by product id.
    seen = set()
    unique = []
    for p in products:
        pid = p.get("id")
        if pid in seen:
            continue
        seen.add(pid)
        unique.append(p)
    return unique


def load_state() -> dict:
    if os.path.exists(STATE_FILE):
        try:
            with open(STATE_FILE) as f:
                return json.load(f)
        except Exception:
            return {}
    return {}


def save_state(state: dict):
    tmp = STATE_FILE + ".tmp"
    with open(tmp, "w") as f:
        json.dump(state, f, indent=2)
    os.replace(tmp, STATE_FILE)


def send_discord(*, store_name, product_url, title, description, color,
                 price, in_stock, image, priority_label=None):
    if not DISCORD_WEBHOOK_URL:
        print("    [discord] (no webhook configured — skipping send)")
        return

    embed = {
        "title": title,
        "description": description,
        "color": color,
        "url": product_url,
        "fields": [
            {"name": "Store", "value": store_name, "inline": True},
            {"name": "Price", "value": price or "N/A", "inline": True},
            {"name": "Status",
             "value": "✅ In Stock" if in_stock else "📦 Out of Stock",
             "inline": True},
            {"name": "🛒 Buy Now", "value": f"[View Product]({product_url})", "inline": False},
        ],
        "footer": {"text": f"Multi-Store TCG Monitor • {datetime.now():%Y-%m-%d %H:%M:%S}"},
    }
    if priority_label:
        embed["fields"].insert(0, {"name": "🎯 Watched Set", "value": priority_label, "inline": False})
    if image:
        embed["thumbnail"] = {"url": image}

    payload = {"embeds": [embed]}
    # @-mention for special-interest sets so it actually pings the phone.
    if priority_label and DISCORD_USER_ID:
        payload["content"] = f"<@{DISCORD_USER_ID}>"
        payload["allowed_mentions"] = {"users": [DISCORD_USER_ID]}

    try:
        r = requests.post(DISCORD_WEBHOOK_URL, json=payload, timeout=15)
        r.raise_for_status()
        print(f"    [discord] ✓ {title} — {store_name}")
    except Exception as e:
        print(f"    [discord] ✗ failed: {e}")


def check_store(session: requests.Session, store: dict, prev_state: dict,
                first_run: bool) -> dict:
    """Check one store, fire alerts, and return its new state slice."""
    name = store["name"]
    base = store["base"]
    print(f"  → {name}")

    products = gather_store_products(session, store)
    print(f"    {len(products)} products gathered")

    new_state = {}
    matched_count = 0

    for p in products:
        matched, franchise, priority_label = classify(p)
        if not matched:
            continue
        matched_count += 1

        pid = p.get("id")
        key = f"{base}|{pid}"
        handle = p.get("handle", "")
        product_url = f"{base}/products/{handle}"
        in_stock, price = product_availability(p)
        image = product_image(p)
        title_name = p.get("title", handle)

        new_state[key] = {
            "name": title_name,
            "in_stock": in_stock,
            "price": price,
            "url": product_url,
        }

        prev = prev_state.get(key)

        if first_run:
            continue  # baseline only — never alert on the very first scan

        if prev is None:
            # Newly listed product matching our filters.
            print(f"    [NEW] {title_name} ({'in stock' if in_stock else 'oos'})")
            send_discord(
                store_name=name,
                product_url=product_url,
                title="🆕 New Listing!" + (f"  {priority_label}" if priority_label else ""),
                description=f"**[{title_name}]({product_url})**\nJust appeared at {name}.",
                color=0x00FF7F if in_stock else 0xFFA500,
                price=price, in_stock=in_stock, image=image,
                priority_label=priority_label,
            )
        elif not prev.get("in_stock") and in_stock:
            # Restock: was out of stock, now available.
            print(f"    [RESTOCK] {title_name}")
            send_discord(
                store_name=name,
                product_url=product_url,
                title="✅ Back In Stock!" + (f"  {priority_label}" if priority_label else ""),
                description=f"**[{title_name}]({product_url})**\nJust became available at {name}!",
                color=0x00FF00,
                price=price, in_stock=in_stock, image=image,
                priority_label=priority_label,
            )

    print(f"    {matched_count} matching TCG product(s) tracked")
    return new_state


def run_cycle(prev_state: dict, first_run: bool) -> dict:
    ts = datetime.now().strftime("%H:%M:%S")
    print(f"[{ts}] Scanning {len(STORES)} stores"
          + (" (baseline)" if first_run else "") + "...")

    session = make_session()
    new_state = dict(prev_state)  # keep keys from stores we might skip this run

    for store in STORES:
        # Previous state slice for just this store (keys start with base url).
        store_prev = {k: v for k, v in prev_state.items()
                      if k.startswith(store["base"] + "|")}
        try:
            store_new = check_store(session, store, store_prev, first_run)
        except Exception as e:
            print(f"  [!] {store['name']} errored: {e} — keeping previous state")
            continue

        # Replace this store's slice with the freshly scanned one.
        for k in list(new_state.keys()):
            if k.startswith(store["base"] + "|"):
                del new_state[k]
        new_state.update(store_new)
        time.sleep(REQUEST_DELAY)

    return new_state


def main():
    print("=" * 60)
    print("  Multi-Store TCG Monitor")
    print(f"  Stores   : {', '.join(s['name'] for s in STORES)}")
    print(f"  Interval : {CHECK_INTERVAL_SECONDS}s")
    print(f"  Webhook  : {'configured' if DISCORD_WEBHOOK_URL else 'NOT SET (alerts will only print)'}")
    print(f"  State    : {STATE_FILE}")
    print("  Press Ctrl+C to stop.")
    print("=" * 60)
    print()

    if DISCORD_WEBHOOK_URL:
        try:
            requests.post(DISCORD_WEBHOOK_URL, json={
                "content": ("👀 **Multi-Store TCG Monitor started** — watching "
                            "One Piece & Pokémon TCG across "
                            f"{len(STORES)} stores every "
                            f"{CHECK_INTERVAL_SECONDS // 60} min.")
            }, timeout=15)
        except Exception as e:
            print(f"  [!] startup ping failed: {e}")

    state = load_state()

    first_run = not state
    if first_run:
        print("First run — building a silent baseline (no alerts this pass).\n")
        try:
            state = run_cycle({}, first_run=True)
            save_state(state)
            print(f"\nBaseline saved: {len(state)} products tracked.\n")
        except Exception as e:
            print(f"Baseline failed: {e}")
        time.sleep(CHECK_INTERVAL_SECONDS)

    while True:
        try:
            state = run_cycle(state, first_run=False)
            save_state(state)
        except KeyboardInterrupt:
            print("\nStopped.")
            break
        except Exception as e:
            print(f"  [!] unexpected cycle error: {e}")
        # small jitter so we don't hit every store on an exact cadence
        time.sleep(CHECK_INTERVAL_SECONDS + random.randint(0, 20))


if __name__ == "__main__":
    try:
        main()
    except KeyboardInterrupt:
        print("\nStopped.")
    except Exception as e:
        print(f"\n[FATAL ERROR] {e}")
        import traceback
        traceback.print_exc()
    finally:
        try:
            input("\nPress Enter to close...")
        except EOFError:
            pass
