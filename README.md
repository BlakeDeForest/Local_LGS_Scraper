# Local LGS Scraper — Multi-Store TCG Monitor

Watches several Australian game stores for **One Piece TCG** and **Pokémon TCG**
products and pings a **Discord webhook** when something is **newly listed** or
**comes back in stock** (out-of-stock → in-stock).

## Stores watched

| Store | URL | Platform |
|-------|-----|----------|
| Good Games | https://www.goodgames.com.au | Shopify |
| General Games | https://www.generalgames.com.au | Shopify |
| Gaming Grounds | https://www.gaminggrounds.com.au | Shopify |
| HanHan Games | https://hanhangames.com | Shopify |
| Rhystic Nostalgia Gaming | https://rhysticnostalgiagaming.com.au | Shopify |
| Mind Games | https://www.m-g.com.au | WooCommerce |

The monitor reads each store's **public product feed** rather than scraping HTML:
Shopify stores via `/products.json`, and WooCommerce stores (Mind Games) via the
WooCommerce **Store API** (`/wp-json/wc/store/v1/products`). Both give structured
data — title, price, image, categories and a real **in-stock flag** — which makes
restock detection reliable and means no slow headless browser is needed.

## What it alerts on

* **Anything One Piece TCG** (booster boxes, extra boosters, starter decks,
  displays, premium collections, double packs, etc.).
* **Anything Pokémon TCG** (booster boxes, ETBs, tins, collections, etc.).
* Non-TCG items (plushies, figures, video games) and other card games
  (Magic, Yu-Gi-Oh!) are filtered out.

### Discord alert contents

Every alert is a rich Discord embed showing **what the product is**, the
**store**, the **game** (One Piece / Pokémon), the **product ID**, the
**price**, the **stock status**, a product image, and a **Buy Now** link.

### When you get @-pinged

If you set your Discord user ID (Step 6 below), you get an `@`-mention (phone
buzz: *"🔔 In stock now!"*) **only when a product is in stock** — i.e. restocks
and any new listing that's already buyable. Out-of-stock / preorder listings
still post to the channel, just **without** pinging you.

### Special-interest sets (highlighted)

These get an extra **🎯 Watched Set** highlight on the alert:

* **Pokémon — Ascended Heroes**
* **One Piece — OP-17**
* **One Piece — OP-18**
* **One Piece — EB-06**

### Two kinds of alert

1. **🆕 New Listing** — a matching product appears in a store's catalog.
2. **✅ Back In Stock** — a tracked product flips from out-of-stock to in-stock.

The **first run is silent**: it records a baseline so you aren't flooded with an
alert for every product that already exists. You only get pinged on *changes*
after that.

---

# Step-by-step setup

Follow these once. After that, you just run one command whenever you want it on.

## Step 1 — Install Python

1. Go to <https://www.python.org/downloads/> and download the latest Python 3.
2. **Windows:** run the installer and **tick the box "Add Python to PATH"** on
   the first screen (this matters), then click Install Now.
   **Mac:** run the downloaded installer, or `brew install python3`.
3. Confirm it worked. Open a terminal:
   * **Windows:** press Start, type `cmd`, open **Command Prompt**.
   * **Mac:** open **Terminal**.
   Then run:
   ```
   python --version
   ```
   You should see `Python 3.x.x`. (On Mac it may be `python3 --version`.)

## Step 2 — Download this code

**Easiest (no git needed):**
1. Open
   <https://github.com/BlakeDeForest/Local_LGS_Scraper/tree/claude/tcg-price-monitor-scraper-uxzqpg>
2. Click the green **`< > Code`** button → **Download ZIP**.
3. Unzip it somewhere easy to find, e.g. `Documents\Local_LGS_Scraper`.

**Or with git:**
```
git clone https://github.com/BlakeDeForest/Local_LGS_Scraper.git
cd Local_LGS_Scraper
git checkout claude/tcg-price-monitor-scraper-uxzqpg
```

## Step 3 — Open a terminal *in that folder*

* **Windows:** open the unzipped folder in File Explorer, click the address bar
  at the top, type `cmd`, and press Enter. A black window opens already pointed
  at the folder.
* **Mac:** in Terminal, `cd` into the folder, e.g.
  ```
  cd ~/Documents/Local_LGS_Scraper
  ```

## Step 4 — Install the dependency

```
pip install -r requirements.txt
```
If `pip` isn't found, try:
```
python -m pip install -r requirements.txt
```
(On Mac use `pip3` / `python3` if needed.) The only dependency is `requests`.

## Step 5 — Create a Discord webhook

You need this or you'll only see alerts in the console, not in Discord.

1. Open Discord and go to the **server** you want alerts in. (No server? Click
   the **+** on the left to create one — it's free — then make a text channel
   like `#tcg-alerts`.)
2. **Server Settings → Integrations → Webhooks → New Webhook.**
3. Choose the channel, then click **Copy Webhook URL**.

## Step 6 — Tell the monitor your webhook

Open `multi_store_monitor.py` in a text editor (on Windows, right-click →
**Open with → Notepad**). Near the top find this line:

```python
DISCORD_WEBHOOK_URL = os.environ.get("DISCORD_WEBHOOK_URL", "")
```

Paste your URL between the last quotes:

```python
DISCORD_WEBHOOK_URL = os.environ.get("DISCORD_WEBHOOK_URL", "https://discord.com/api/webhooks/....your-url....")
```

Save the file.

**(Optional) get pinged personally when something goes in stock:** in Discord
enable **User Settings → Advanced → Developer Mode**, then right-click your name
→ **Copy User ID**, and paste it into the `DISCORD_USER_ID` line the same way.
You'll then be `@`-mentioned whenever a matching product is **in stock** (and not
for out-of-stock listings).

## Step 7 — Run it

```
python multi_store_monitor.py
```

What you'll see:
* A "monitor started" message posts to your Discord channel.
* It does one **silent baseline scan** of all stores (prints how many products
  each one returned).
* From then on it re-checks every **3 minutes** and pings Discord on changes.

**Leave the window open** — closing it stops the monitor. To stop it yourself,
press **Ctrl + C**.

---

## Keeping it running 24/7

The monitor only runs while that terminal window is open. To have it run all the
time (and survive reboots), either:

* **Windows:** use **Task Scheduler** to run `python multi_store_monitor.py` at
  log-on, or
* run it on an always-on machine / cheap VPS / Raspberry Pi.

Ask if you want help setting either of these up.

## Tuning

All knobs live at the top of `multi_store_monitor.py`:

| Setting | What it does |
|---------|--------------|
| `CHECK_INTERVAL_SECONDS` | How often to poll (default 180s = 3 min). |
| `STORES` | Add/remove stores. Each has `name`, `base`, `type` (`shopify` or `woocommerce`), and either `collections` (Shopify) or `search_terms` (WooCommerce). |
| `PRIORITY_SETS` | Sets that get the louder @-mention alert (add OP-19, future sets, etc.). |
| `TCG_TERMS` / `ONE_PIECE_TERMS` / `POKEMON_TERMS` | Keyword filters that decide what counts as a match. |
| `MAX_PAGES` | Cap on pages walked per store. |

### Adding another store

* **Shopify store:**
  ```python
  {"name": "My Store", "base": "https://example.com", "type": "shopify", "collections": []},
  ```
  Leave `collections` empty to scan the whole catalog, or list collection
  handles (the part after `/collections/` in a URL) to scan faster.

* **WooCommerce store:**
  ```python
  {"name": "My Store", "base": "https://example.com", "type": "woocommerce", "search_terms": ["one piece", "pokemon"]},
  ```

## Notes & limitations

* Supports **Shopify** (`/products.json`) and **WooCommerce** (Store API) stores.
  A store on another platform would need a new fetcher.
* The WooCommerce Store API must be enabled on the target store (it is on Mind
  Games and standard on modern WooCommerce). If a WooCommerce store ever returns
  0 products, that's the thing to check.
* This is a polling monitor — alerts arrive within one poll interval of a change,
  not instantly.
* `monitor_state.json` (created on first run) remembers stock status between
  restarts so you don't get re-alerted. Deleting it resets to a fresh baseline.
