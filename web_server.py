#!/usr/bin/env python3
# -*- coding: utf-8 -*-

"""
Rocket Sniper Bot - Web Server (Render)
- Flask server only (no other scripts started here)
- Port 5000 by default
- /early-pool endpoint (CLMM listener -> Telegram alerts + optional auto-buy trigger)
"""

from dotenv import load_dotenv
load_dotenv()

import os
import time
import asyncio
import threading
import logging
from typing import Optional, Tuple

import requests
from flask import Flask, jsonify, request

# =========================================
# Logging
# =========================================
logging.basicConfig(level=logging.INFO, format="%(asctime)s - %(levelname)s - %(message)s")
logger = logging.getLogger("web_server")

# =========================================
# Import de ton bot (rocket_sniper.py)
# =========================================
BOT_AVAILABLE = True
try:
    from rocket_sniper import RealSniperBot, Config
except Exception as e:
    logger.error(f"❌ Impossible d'importer RealSniperBot: {e}")
    BOT_AVAILABLE = False

    class RealSniperBot:
        def __init__(self):
            self.running = False
            self.engine = None
            self.wallet = None

        async def start(self):
            self.running = True

        async def stop(self):
            self.running = False

    class Config:
        AUTO_BUY_AMOUNT = 0.009
        MIN_AGE_MINUTES = 1
        MAX_AGE_MINUTES = 60
        MIN_LIQUIDITY_USD = 50000
        MIN_MARKET_CAP_USD = 1000000
        MIN_VOLUME_24H_USD = 100000

# =========================================
# Flask
# =========================================
app = Flask(__name__)

# =========================================
# Telegram
# =========================================
def send_telegram(msg: str):
    tok = os.getenv("TELEGRAM_BOT_TOKEN", "").strip()
    chat = os.getenv("TELEGRAM_CHAT_ID", "").strip()
    if not tok or not chat:
        logger.warning("⚠️ TELEGRAM_BOT_TOKEN / TELEGRAM_CHAT_ID manquants")
        return

    url = f"https://api.telegram.org/bot{tok}/sendMessage"
    payload = {
        "chat_id": chat,
        "text": msg,
        "parse_mode": "HTML",
        "disable_web_page_preview": True
    }
    try:
        requests.post(url, json=payload, timeout=10)
    except Exception as e:
        logger.warning(f"Telegram send error: {e}")

# =========================================
# Bot runtime globals
# =========================================
bot_instance: Optional[RealSniperBot] = None
bot_thread: Optional[threading.Thread] = None
bot_loop: Optional[asyncio.AbstractEventLoop] = None
state_lock = threading.Lock()

def _loop_thread_target():
    global bot_loop
    bot_loop = asyncio.new_event_loop()
    asyncio.set_event_loop(bot_loop)
    logger.info("✅ Event loop background démarrée")
    bot_loop.run_forever()
    logger.info("🛑 Event loop background stoppée")

def ensure_loop_running() -> bool:
    global bot_thread, bot_loop
    with state_lock:
        if bot_thread and bot_thread.is_alive() and bot_loop and not bot_loop.is_closed():
            return True

        bot_thread = threading.Thread(target=_loop_thread_target, daemon=True)
        bot_thread.start()

    for _ in range(50):
        if bot_loop is not None:
            return True
        time.sleep(0.1)
    return False

def run_async(coro, timeout: float = 30.0):
    if not ensure_loop_running() or bot_loop is None:
        raise RuntimeError("Event loop background indisponible")
    fut = asyncio.run_coroutine_threadsafe(coro, bot_loop)
    return fut.result(timeout=timeout)

def start_bot() -> Tuple[bool, str]:
    global bot_instance
    if not BOT_AVAILABLE:
        return False, "Bot non disponible (import rocket_sniper échoué)"

    with state_lock:
        if bot_instance and getattr(bot_instance, "running", False):
            return False, "Bot déjà en cours d'exécution"
        bot_instance = RealSniperBot()

    try:
        run_async(bot_instance.start(), timeout=5.0)
        return True, "Bot démarré (initialisation en cours)"
    except asyncio.TimeoutError:
        return True, "Bot démarré (initialisation en cours)"
    except Exception as e:
        logger.exception("❌ Erreur start_bot")
        return False, f"Erreur démarrage: {str(e)[:180]}"

def stop_bot() -> Tuple[bool, str]:
    global bot_instance
    with state_lock:
        if not bot_instance:
            return False, "Bot non démarré"
        if not getattr(bot_instance, "running", False):
            return False, "Bot déjà arrêté"

    try:
        run_async(bot_instance.stop(), timeout=15.0)
        return True, "Bot arrêté"
    except Exception as e:
        logger.exception("❌ Erreur stop_bot")
        return False, f"Erreur arrêt: {str(e)[:180]}"

# =========================================
# ✅ SINGLE /early-pool endpoint (NO DUPLICATE)
# =========================================
@app.route("/early-pool", methods=["POST"])
def early_pool():
    data = request.json or {}

    mint = data.get("mint", "")
    sig = data.get("signature", "")
    age = data.get("age_minutes", None)
    liq = data.get("liquidity_usd", None)
    fdv = data.get("fdv_usd", None)
    price = data.get("price_usd", None)

    if not mint:
        return jsonify({"ok": False, "error": "missing mint"}), 400

    # Telegram message
    lines = ["🚀 <b>NEW RAYDIUM CLMM EARLY POOL</b>", ""]
    lines.append(f"🧬 Mint: <code>{mint}</code>")

    if isinstance(age, (int, float)):
        lines.append(f"⏱ Age: {age:.1f} min")
    else:
        lines.append("⏱ Age: ?")

    if isinstance(liq, (int, float)):
        lines.append(f"💧 Liquidity: ${liq:,.0f}")
    if isinstance(fdv, (int, float)):
        lines.append(f"📊 FDV: ${fdv:,.0f}")
    if isinstance(price, (int, float)):
        lines.append(f"💵 Price: ${price:.10f}")

    if sig:
        lines.append("")
        lines.append(f"🔗 https://solscan.io/tx/{sig}")

    send_telegram("\n".join(lines))

    # ---------------- OPTIONAL: Auto-buy trigger (B)
    AUTO_BUY_ON_EARLY_POOL = True
    BUY_AMOUNT_SOL = float(getattr(Config, "AUTO_BUY_AMOUNT", 0.009) or 0.009)

    MAX_FDV_BUY = 500_000
    MAX_LIQ_BUY = 150_000
    MIN_LIQ_BUY = 2_000
    MAX_AGE_BUY_MIN = 30

    # ⚠️ Ici on NE FAIT PAS de buy réel tant que ton endpoint /buy n’est pas confirmé.
    # On envoie juste une alerte "trigger".
    if AUTO_BUY_ON_EARLY_POOL and isinstance(age, (int, float)) and isinstance(liq, (int, float)) and isinstance(fdv, (int, float)):
        if (fdv <= MAX_FDV_BUY) and (MIN_LIQ_BUY <= liq <= MAX_LIQ_BUY) and (age <= MAX_AGE_BUY_MIN):
            send_telegram(
                "🟢 <b>AUTO BUY TRIGGER</b>\n"
                f"Mint: <code>{mint}</code>\n"
                f"Age: {age:.1f}m | Liq: ${liq:,.0f} | FDV: ${fdv:,.0f}\n"
                f"Amount: {BUY_AMOUNT_SOL} SOL"
            )

    return jsonify({"ok": True})

# =========================================
# Routes principales
# =========================================
@app.route("/", methods=["GET"])
def home():
    return jsonify({
        "status": "online",
        "service": "Rocket Sniper Bot API",
        "bot_available": BOT_AVAILABLE,
        "endpoints": [
            "/",
            "/health",
            "/start (GET/POST)",
            "/stop (GET/POST)",
            "/status (GET)",
            "/toggle-auto (GET/POST)",
            "/scan (GET)",
            "/early-pool (POST)"
        ]
    })

@app.route("/health", methods=["GET"])
def health():
    return jsonify({"status": "healthy", "timestamp": time.time()}), 200

@app.route("/start", methods=["GET", "POST"])
def start_route():
    ok, msg = start_bot()
    return jsonify({
        "success": ok,
        "message": msg,
        "bot_running": bool(bot_instance and getattr(bot_instance, "running", False))
    }), (200 if ok else 400)

@app.route("/stop", methods=["GET", "POST"])
def stop_route():
    ok, msg = stop_bot()
    return jsonify({
        "success": ok,
        "message": msg,
        "bot_running": bool(bot_instance and getattr(bot_instance, "running", False))
    }), (200 if ok else 400)

@app.route("/status", methods=["GET"])
def status_route():
    running = bool(bot_instance and getattr(bot_instance, "running", False))
    engine = getattr(bot_instance, "engine", None) if bot_instance else None
    return jsonify({
        "server": "online",
        "timestamp": time.time(),
        "bot": {
            "available": BOT_AVAILABLE,
            "running": running,
            "auto_trading": bool(getattr(engine, "auto_trading", False)) if engine else False,
            "stats": getattr(engine, "stats", {}) if engine else {},
        },
        "config": {
            "auto_buy_amount": getattr(Config, "AUTO_BUY_AMOUNT", None),
            "min_age": getattr(Config, "MIN_AGE_MINUTES", None),
            "max_age": getattr(Config, "MAX_AGE_MINUTES", None),
            "min_liquidity": getattr(Config, "MIN_LIQUIDITY_USD", None),
            "min_volume_24h": getattr(Config, "MIN_VOLUME_24H_USD", None),
            "min_mcap": getattr(Config, "MIN_MARKET_CAP_USD", None),
        }
    })

@app.route("/toggle-auto", methods=["GET", "POST"])
def toggle_auto():
    if not bot_instance or not getattr(bot_instance, "running", False):
        return jsonify({"success": False, "message": "Bot non démarré / engine pas prêt"}), 400

    engine = getattr(bot_instance, "engine", None)
    if not engine:
        return jsonify({"success": False, "message": "Engine non prêt"}), 400

    engine.auto_trading = not bool(engine.auto_trading)
    return jsonify({
        "success": True,
        "auto_trading": bool(engine.auto_trading),
        "message": f"Auto-trading {'activé' if engine.auto_trading else 'désactivé'}"
    })

@app.route("/scan", methods=["GET"])
def scan_route():
    """DexScreener scan (fallback)"""
    import urllib.request
    import json as json_module

    try:
        url = "https://api.dexscreener.com/latest/dex/search?q=raydium&limit=100"
        req = urllib.request.Request(url, headers={"User-Agent": "Mozilla/5.0"})

        with urllib.request.urlopen(req, timeout=15) as response:
            data = json_module.loads(response.read().decode('utf-8'))
            all_pairs = data.get("pairs", [])

        current_time = time.time()
        filtered_tokens = []

        min_liquidity = getattr(Config, 'MIN_LIQUIDITY_USD', 10000)
        min_volume = getattr(Config, 'MIN_VOLUME_24H_USD', 10000)
        min_mcap = getattr(Config, 'MIN_MARKET_CAP_USD', 0)
        min_age = getattr(Config, 'MIN_AGE_MINUTES', 1)
        max_age = getattr(Config, 'MAX_AGE_MINUTES', 180)

        for pair in all_pairs:
            if pair.get('dexId') != 'raydium':
                continue

            created_at = pair.get('pairCreatedAt', 0)
            if not created_at:
                continue

            age_seconds = current_time - (created_at / 1000)
            age_minutes = age_seconds / 60

            liquidity = pair.get('liquidity', {}).get('usd', 0)
            volume_24h = pair.get('volume', {}).get('h24', 0)
            price = pair.get('priceUsd', 0)

            base_token = pair.get('baseToken', {})
            supply = base_token.get('totalSupply', 0)
            market_cap = price * supply if supply and price else 0

            if (age_minutes >= min_age and
                age_minutes <= max_age and
                liquidity >= min_liquidity and
                volume_24h >= min_volume and
                market_cap >= min_mcap):

                filtered_tokens.append({
                    'address': base_token.get('address', ''),
                    'symbol': base_token.get('symbol', ''),
                    'name': base_token.get('name', ''),
                    'liquidity': liquidity,
                    'price': price,
                    'volume_24h': volume_24h,
                    'market_cap': market_cap,
                    'age_minutes': round(age_minutes, 2),
                    'pair_address': pair.get('pairAddress', ''),
                    'url': pair.get('url', ''),
                    'source': 'DexScreener'
                })

        filtered_tokens.sort(key=lambda x: x['age_minutes'])

        return jsonify({
            "success": True,
            "tokens": filtered_tokens[:20],
            "tokens_found": len(filtered_tokens),
            "debug": {
                "source": "DexScreener",
                "total_pairs": len(all_pairs),
                "config": {
                    "min_age": min_age,
                    "max_age": max_age,
                    "min_liquidity": min_liquidity,
                    "min_volume": min_volume,
                    "min_mcap": min_mcap
                }
            }
        })

    except Exception as e:
        return jsonify({"success": False, "error": str(e), "tokens_found": 0})

# =========================================
# Main
# =========================================
if __name__ == "__main__":
    port = int(os.environ.get("PORT", "5000"))
    app.run(host="0.0.0.0", port=port, debug=False)

