#!/usr/bin/env python3
"""
Multi-Store TCG Monitor
=======================

Watches several Australian game stores for One Piece TCG and Pokemon TCG
products and pings a Discord webhook when:

  * a matching product is newly listed, or
  * a matching product comes back in stock (out-of-stock -> in-stock).

Stores monitored:
  * Good Games          https://www.goodgames.com.au          (Shopify)
  * General Games       https://www.generalgames.com.au       (Shopify)
  * Gaming Grounds      https://www.gaminggrounds.com.au      (Shopify)
  * HanHan Games        https://hanhangames.com               (Shopify)
  * Rhystic Nostalgia   https://rhysticnostalgiagaming.com.au (Shopify)
  * Mind Games          https://www.m-g.com.au                (WooCommerce)

How it reads stock without a headless browser:
  * Shopify stores expose a public JSON catalog at `/products.json`
    (and `/collections/<handle>/products.json`) with a per-variant
    `available` flag.
  * WooCommerce stores expose the public Store API at
    `/wp-json/wc/store/v1/products` with an `is_in_stock` flag per product.
  Both are structured, reliable, and far lighter than scraping HTML — and they
  make restock detection trivial.

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

# Optional: your Discord numeric user ID. If set, you get @-mentioned (phone
# buzz) ONLY when a matching product is IN STOCK — i.e. restocks and any
# new listing that's already buyable. Out-of-stock / preorder listings still
# post to the channel, but without pinging you. Get your ID: Discord →
# User Settings → Advanced → enable Developer Mode, then right-click your name →
# "Copy User ID".
DISCORD_USER_ID = os.environ.get("DISCORD_USER_ID", "")

# How often to poll every store, in seconds.
CHECK_INTERVAL_SECONDS = 180  # 3 minutes

# Polite delay between individual HTTP requests (seconds).
REQUEST_DELAY = 1.0

# Max pages to walk per endpoint (Shopify: 250 items/page, WooCommerce: 100).
MAX_PAGES = 25

STATE_FILE = os.path.join(os.path.dirname(os.path.abspath(__file__)), "monitor_state.json")

# Stores to watch.
#
# Each store needs:
#   name : display name
#   base : site root URL (no trailing slash)
#   type : "shopify" (default) or "woocommerce"
#
# Shopify stores may set:
#   collections : list of Shopify collection handles to narrow the scan
#                 (faster, less noise). Empty/omitted -> scan whole catalog.
#                 Unknown handles are skipped; if all fail we scan the catalog.
#
# WooCommerce stores may set:
#   search_terms : list of search queries used against the Store API
#                  (defaults to ["one piece", "pokemon"] if omitted).
STORES = [
    {
        "name": "Good Games",
        "base": "https://www.goodgames.com.au",
        "type": "shopify",
        "collections": [
            "trading-card-games",
            "coming-soon-trading-card-games",
        ],
    },
    {
        "name": "General Games",
        "base": "https://www.generalgames.com.au",
        "type": "shopify",
        "collections": [],  # full-catalog scan, filtered by keyword
    },
    {
        "name": "Gaming Grounds",
        "base": "https://www.gaminggrounds.com.au",
        "type": "shopify",
        "collections": [
            "one-piece",
            "pokemon",
        ],
    },
    {
        "name": "HanHan Games",
        "base": "https://hanhangames.com",
        "type": "shopify",
        "collections": [],  # full-catalog scan
    },
    {
        "name": "Rhystic Nostalgia Gaming",
        "base": "https://rhysticnostalgiagaming.com.au",
        "type": "shopify",
        "collections": [
            "one-piece-sealed-instock",
            "all-one-piece-preorders",
            "pokemon",
        ],
    },
    {
        "name": "Mind Games",
        "base": "https://www.m-g.com.au",
        "type": "woocommerce",
        "search_terms": ["one piece", "pokemon"],
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
    "premium card collection",
]

# Special-interest sets — louder alert + optional @-mention. Each entry is a
# list of aliases; if ANY alias is found in the product text it matches.
PRIORITY_SETS = {
    "Pokémon — Ascended Heroes": ["ascended heroes"],
    "One Piece — OP-17": ["op-17", "op17", "op 17"],
    "One Piece — OP-18": ["op-18", "op18", "op 18"],
    "One Piece — EB-06": ["eb-06", "eb06", "eb 06"],
}

# Default search terms used for WooCommerce stores when not overridden.
DEFAULT_WOO_SEARCH_TERMS = ["one piece", "pokemon"]

USER_AGENT = (
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
    "AppleWebKit/537.36 (KHTML, like Gecko) Chrome/124.0.0.0 Safari/537.36"
)

# ─────────────────────────────────────────────────────────────────────────────


def classify(norm: dict):
    """
    Decide whether a normalized product is worth alerting on.

    `norm` is a normalized product dict (see normalize_* functions) with at
    least 'text' (lowercase searchable blob) and 'product_type'.

    Returns (matched: bool, franchise: str|None, priority_label: str|None).
    """
    text = norm.get("text", "")

    is_one_piece = any(t in text for t in ONE_PIECE_TERMS)
    is_pokemon = any(t in text for t in POKEMON_TERMS)
    if not (is_one_piece or is_pokemon):
        return False, None, None

    is_tcg = any(t in text for t in TCG_TERMS)
    # Treat a product type/category that literally says "card" as TCG.
    if "card" in norm.get("product_type", "").lower():
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


# ── NORMALIZATION ────────────────────────────────────────────────────────────
# Both platforms get squashed into a common product shape:
#   {id, title, url, in_stock, price, image, product_type, text}

def _blob(*parts) -> str:
    return " ".join(str(p) for p in parts if p).lower()


def normalize_shopify(p: dict, base: str) -> dict:
    variants = p.get("variants", []) or []
    in_stock = any(v.get("available") for v in variants)
    prices = [v.get("price") for v in variants if v.get("price")]
    price = f"${prices[0]}" if prices else "N/A"

    images = p.get("images", []) or []
    image = images[0].get("src", "") if images and isinstance(images[0], dict) else ""

    handle = p.get("handle", "")
    tags = p.get("tags", [])
    tags_str = " ".join(tags) if isinstance(tags, list) else str(tags)
    product_type = p.get("product_type", "")
    title = p.get("title", handle)

    return {
        "id": p.get("id"),
        "title": title,
        "url": f"{base}/products/{handle}",
        "in_stock": in_stock,
        "price": price,
        "image": image,
        "product_type": product_type,
        "text": _blob(title, product_type, p.get("vendor", ""), tags_str, handle),
    }


def normalize_woocommerce(p: dict) -> dict:
    in_stock = bool(p.get("is_in_stock"))

    price = "N/A"
    prices = p.get("prices") or {}
    raw = prices.get("price")
    if raw is not None:
        try:
            minor = int(prices.get("currency_minor_unit", 2))
            symbol = prices.get("currency_symbol", "$")
            price = f"{symbol}{int(raw) / (10 ** minor):.2f}"
        except (ValueError, TypeError):
            price = str(raw)

    images = p.get("images", []) or []
    image = images[0].get("src", "") if images and isinstance(images[0], dict) else ""

    categories = p.get("categories", []) or []
    cat_names = " ".join(c.get("name", "") for c in categories if isinstance(c, dict))
    title = p.get("name", "")

    return {
        "id": p.get("id"),
        "title": title,
        "url": p.get("permalink", ""),
        "in_stock": in_stock,
        "price": price,
        "image": image,
        "product_type": cat_names,
        "text": _blob(title, cat_names, p.get("sku", "")),
    }


# ── FETCHING ─────────────────────────────────────────────────────────────────

def make_session() -> requests.Session:
    s = requests.Session()
    s.headers.update({
        "User-Agent": USER_AGENT,
        "Accept": "application/json,text/plain,*/*",
        "Accept-Language": "en-AU,en;q=0.9",
    })
    return s


def fetch_json(session: requests.Session, url: str, params: dict = None):
    """GET a JSON endpoint, returning parsed JSON or None."""
    try:
        r = session.get(url, params=params, timeout=25)
        if r.status_code == 404:
            return None
        r.raise_for_status()
        return r.json()
    except Exception as e:
        print(f"    [!] fetch failed {url}: {e}")
        return None


def fetch_shopify_path(session, base: str, path: str) -> list:
    """Walk a paginated Shopify products.json endpoint -> list of product dicts."""
    products = []
    for page in range(1, MAX_PAGES + 1):
        data = fetch_json(session, f"{base}{path}", {"limit": 250, "page": page})
        if not data:
            break
        batch = data.get("products", [])
        if not batch:
            break
        products.extend(batch)
        if len(batch) < 250:
            break
        time.sleep(REQUEST_DELAY)
    return products


def gather_shopify(session, store: dict) -> list:
    """Return normalized products for a Shopify store."""
    base = store["base"]
    collections = store.get("collections") or []
    raw = []

    if collections:
        for handle in collections:
            got = fetch_shopify_path(session, base, f"/collections/{handle}/products.json")
            if got:
                print(f"    collection '{handle}': {len(got)} products")
                raw.extend(got)
            time.sleep(REQUEST_DELAY)

    if not raw:
        print("    scanning full catalog (/products.json)...")
        raw = fetch_shopify_path(session, base, "/products.json")

    return [normalize_shopify(p, base) for p in raw]


def gather_woocommerce(session, store: dict) -> list:
    """Return normalized products for a WooCommerce store via the Store API."""
    base = store["base"]
    terms = store.get("search_terms") or DEFAULT_WOO_SEARCH_TERMS
    raw = []

    for term in terms:
        found_for_term = 0
        for page in range(1, MAX_PAGES + 1):
            data = fetch_json(
                session,
                f"{base}/wp-json/wc/store/v1/products",
                {"per_page": 100, "page": page, "search": term},
            )
            # Store API returns a JSON array of products.
            if not isinstance(data, list) or not data:
                break
            raw.extend(data)
            found_for_term += len(data)
            if len(data) < 100:
                break
            time.sleep(REQUEST_DELAY)
        print(f"    search '{term}': {found_for_term} products")
        time.sleep(REQUEST_DELAY)

    return [normalize_woocommerce(p) for p in raw]


def gather_store_products(session, store: dict) -> list:
    """Collect normalized, deduped products for a store (any supported type)."""
    stype = store.get("type", "shopify")
    if stype == "woocommerce":
        normalized = gather_woocommerce(session, store)
    else:
        normalized = gather_shopify(session, store)

    seen = set()
    unique = []
    for n in normalized:
        nid = n.get("id")
        if nid in seen:
            continue
        seen.add(nid)
        unique.append(n)
    return unique


# ── STATE ────────────────────────────────────────────────────────────────────

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


# ── DISCORD ──────────────────────────────────────────────────────────────────

def send_discord(*, store_name, product_name, product_id, product_url, franchise,
                 title, description, color, price, in_stock, image,
                 priority_label=None):
    if not DISCORD_WEBHOOK_URL:
        print("    [discord] (no webhook configured — skipping send)")
        return

    embed = {
        "title": title,
        "description": description,
        "color": color,
        "url": product_url,
        "fields": [
            {"name": "🃏 Product", "value": product_name, "inline": False},
            {"name": "Store", "value": store_name, "inline": True},
            {"name": "Game", "value": franchise or "—", "inline": True},
            {"name": "Product ID", "value": f"`{product_id}`", "inline": True},
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
    # @-mention you ONLY when the product is actually in stock (buyable now), so
    # your phone buzzes for restocks / in-stock listings but not for
    # out-of-stock or preorder listings.
    if in_stock and DISCORD_USER_ID:
        payload["content"] = f"<@{DISCORD_USER_ID}> 🔔 In stock now!"
        payload["allowed_mentions"] = {"users": [DISCORD_USER_ID]}

    try:
        r = requests.post(DISCORD_WEBHOOK_URL, json=payload, timeout=15)
        r.raise_for_status()
        print(f"    [discord] ✓ {title} — {store_name}")
    except Exception as e:
        print(f"    [discord] ✗ failed: {e}")


# ── CORE ─────────────────────────────────────────────────────────────────────

def check_store(session, store: dict, prev_state: dict, first_run: bool) -> dict:
    """Check one store, fire alerts, and return its new state slice."""
    name = store["name"]
    base = store["base"]
    print(f"  → {name} ({store.get('type', 'shopify')})")

    products = gather_store_products(session, store)
    print(f"    {len(products)} products gathered")

    new_state = {}
    matched_count = 0

    for n in products:
        matched, franchise, priority_label = classify(n)
        if not matched:
            continue
        matched_count += 1

        product_id = n.get("id")
        key = f"{base}|{product_id}"
        in_stock = n["in_stock"]
        product_url = n["url"]
        title_name = n["title"]

        new_state[key] = {
            "name": title_name,
            "in_stock": in_stock,
            "price": n["price"],
            "url": product_url,
        }

        prev = prev_state.get(key)

        if first_run:
            continue  # baseline only — never alert on the very first scan

        common = dict(
            store_name=name,
            product_name=title_name,
            product_id=product_id,
            product_url=product_url,
            franchise=franchise,
            price=n["price"],
            in_stock=in_stock,
            image=n["image"],
            priority_label=priority_label,
        )

        if prev is None:
            print(f"    [NEW] {title_name} ({'in stock' if in_stock else 'oos'})")
            send_discord(
                title="🆕 New Listing!" + (f"  {priority_label}" if priority_label else ""),
                description=f"**[{title_name}]({product_url})**\nJust appeared at {name}.",
                color=0x00FF7F if in_stock else 0xFFA500,
                **common,
            )
        elif not prev.get("in_stock") and in_stock:
            print(f"    [RESTOCK] {title_name}")
            send_discord(
                title="✅ Back In Stock!" + (f"  {priority_label}" if priority_label else ""),
                description=f"**[{title_name}]({product_url})**\nJust became available at {name}!",
                color=0x00FF00,
                **common,
            )

    print(f"    {matched_count} matching TCG product(s) tracked")
    return new_state


def run_cycle(prev_state: dict, first_run: bool) -> dict:
    ts = datetime.now().strftime("%H:%M:%S")
    print(f"[{ts}] Scanning {len(STORES)} stores"
          + (" (baseline)" if first_run else "") + "...")

    session = make_session()
    new_state = dict(prev_state)

    for store in STORES:
        store_prev = {k: v for k, v in prev_state.items()
                      if k.startswith(store["base"] + "|")}
        try:
            store_new = check_store(session, store, store_prev, first_run)
        except Exception as e:
            print(f"  [!] {store['name']} errored: {e} — keeping previous state")
            continue

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
