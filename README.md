# Local LGS Scraper — Multi-Store TCG Monitor

Watches several Australian Shopify game stores for **One Piece TCG** and
**Pokémon TCG** products and pings a **Discord webhook** when something is
newly listed or comes back in stock.

## Stores watched

| Store | URL |
|-------|-----|
| Good Games | https://www.goodgames.com.au |
| General Games | https://www.generalgames.com.au |
| Gaming Grounds | https://www.gaminggrounds.com.au |
| HanHan Games | https://hanhangames.com |
| Rhystic Nostalgia Gaming | https://rhysticnostalgiagaming.com.au |

All five run on **Shopify**, so instead of fragile HTML scraping the monitor
reads each store's public `/products.json` catalog. That gives structured
data — title, price, images, tags and a real per-variant `available` flag —
which makes restock detection reliable and avoids needing a headless browser.

## What it alerts on

* **Anything One Piece TCG** (booster boxes, extra boosters, starter decks,
  displays, double packs, etc.).
* **Anything Pokémon TCG** (booster boxes, ETBs, tins, collections, etc.).
* Non-TCG items (plushies, figures, video games) and other card games
  (Magic, Yu-Gi-Oh!) are filtered out.

### Special-interest sets (louder, @-mention alert)

These trigger an extra-prominent alert and — if you set your Discord user ID —
will `@`-mention you so your phone actually buzzes:

* **Pokémon — Ascended Heroes**
* **One Piece — OP-17**
* **One Piece — OP-18**
* **One Piece — EB-06**

Two kinds of alert are sent:

1. **🆕 New Listing** — a matching product appears in a store's catalog.
2. **✅ Back In Stock** — a tracked product flips from out-of-stock to in-stock.

The very first run is silent: it records a baseline so you aren't flooded with
an alert for every existing product.

## Setup

1. **Install Python 3.9+** and the one dependency:

   ```bash
   pip install -r requirements.txt
   ```

2. **Create a Discord webhook:**
   - Open Discord → the server you want alerts in.
   - **Server Settings → Integrations → Webhooks → New Webhook**.
   - Pick the channel, click **Copy Webhook URL**.
   - (No server? Create one — it's free — then make a channel like `#tcg-alerts`.)

3. **Tell the monitor about it.** Either paste the URL into the top of
   `multi_store_monitor.py`:

   ```python
   DISCORD_WEBHOOK_URL = "https://discord.com/api/webhooks/...."
   ```

   …or set it as an environment variable (recommended, keeps it out of the code):

   ```bash
   export DISCORD_WEBHOOK_URL="https://discord.com/api/webhooks/...."
   ```

4. **(Optional) Get pinged personally** for the special-interest sets. Enable
   Developer Mode (Discord → User Settings → Advanced), right-click your name →
   **Copy User ID**, then:

   ```bash
   export DISCORD_USER_ID="123456789012345678"
   ```

## Run it

```bash
python multi_store_monitor.py
```

Leave it running. It scans every store every 3 minutes (configurable). State is
saved to `monitor_state.json` so restarts don't re-alert on everything.

## Tuning

All knobs live at the top of `multi_store_monitor.py`:

| Setting | What it does |
|---------|--------------|
| `CHECK_INTERVAL_SECONDS` | How often to poll (default 180s). |
| `STORES` | Add/remove stores or narrow each to specific Shopify `collections`. |
| `PRIORITY_SETS` | Sets that get the louder @-mention alert (add OP-19, future sets, etc.). |
| `TCG_TERMS` / `ONE_PIECE_TERMS` / `POKEMON_TERMS` | Keyword filters used to decide what counts. |
| `MAX_PAGES` | Cap on catalog pages walked per store. |

### Adding a store

Any Shopify store works. Add an entry to `STORES`:

```python
{"name": "My Store", "base": "https://example.com", "collections": []},
```

Leave `collections` empty to scan the whole catalog, or list collection handles
(the bit after `/collections/` in a URL) to scan faster and with less noise.

## Notes & limitations

* Built for **Shopify** stores (it relies on `/products.json`). A non-Shopify
  store would need a different fetcher.
* If a store ever blocks plain requests behind Cloudflare, you'd need to add a
  headless-browser fallback (Playwright) — not currently required for these five.
* This is a polling monitor; alerts arrive within one poll interval of a change,
  not instantly.
