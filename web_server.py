#!/usr/bin/env python3
"""
web_server.py - Rocket Sniper Bot (Render Web Service)
✅ Endpoints stables + exécution async correcte en background
✅ /toggle-auto en GET/POST
✅ /test-telegram
✅ Pas de new_event_loop() dans les routes Flask (utilise run_coroutine_threadsafe)
"""

from dotenv import load_dotenv
load_dotenv()

import os
import time
import json
import asyncio
import logging
import threading
from typing import Any, Dict, Optional, Tuple

from flask import Flask, jsonify, request

# ============================================================
# LOGS
# ============================================================
logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s - %(levelname)s - %(message)s"
)
logger = logging.getLogger("web_server")

# ============================================================
# IMPORT DU BOT (ton fichier contient tout)
# -> Assure-toi que RealSniperBot / Config existent bien dans web_server.py
# Ici, comme tu m'as montré que tout est DANS web_server.py,
# on ne fait pas d'import externe.
# ============================================================

# ============================================================
# CONFIG / APP
# ============================================================
app = Flask(__name__)

BOT_AVAILABLE = True

bot_instance = None          # RealSniperBot()
bot_thread = None            # Thread background
bot_loop: Optional[asyncio.AbstractEventLoop] = None  # Loop async du bot
bot_lock = threading.Lock()  # Eviter les doubles start/stop


# ============================================================
# OUTILS THREADSAFE ASYNC
# ============================================================
def _loop_ready() -> bool:
    return bot_loop is not None and not bot_loop.is_closed()

def run_coro_threadsafe(coro, timeout: float = 25.0):
    """
    Exécute une coroutine dans la loop du bot (threadsafe).
    """
    if not _loop_ready():
        raise RuntimeError("Bot loop not ready")

    fut = asyncio.run_coroutine_threadsafe(coro, bot_loop)
    return fut.result(timeout=timeout)


# ============================================================
# BACKGROUND RUNNER
# ============================================================
def run_bot_in_background():
    """
    Thread target: crée sa propre event loop, démarre le bot.
    """
    global bot_instance, bot_loop

    try:
        logger.info("🔄 Background thread starting... creating event loop")
        bot_loop = asyncio.new_event_loop()
        asyncio.set_event_loop(bot_loop)

        # ⚠️ RealSniperBot est défini plus bas dans ton fichier (comme tu l'as collé)
        # On l'instancie ici.
        bot_instance_local = RealSniperBot()
        bot_instance = bot_instance_local

        logger.info("🚀 Starting bot (async)...")
        bot_loop.run_until_complete(bot_instance_local.start())

    except Exception as e:
        logger.exception(f"❌ Bot background crashed: {e}")

    finally:
        try:
            if bot_loop and not bot_loop.is_closed():
                bot_loop.stop()
                bot_loop.close()
        except Exception:
            pass
        logger.warning("🛑 Background thread stopped")


def start_bot() -> Tuple[bool, str]:
    """
    Démarre le thread si pas déjà vivant.
    """
    global bot_thread

    with bot_lock:
        if bot_thread and bot_thread.is_alive():
            return False, "Bot déjà en cours d'exécution"

        if not BOT_AVAILABLE:
            return False, "Bot non disponible"

        bot_thread = threading.Thread(target=run_bot_in_background, daemon=True)
        bot_thread.start()

    # petit délai pour laisser la loop se créer
    time.sleep(1.5)
    return True, "Bot démarré avec succès"


def stop_bot() -> Tuple[bool, str]:
    """
    Demande l'arrêt propre.
    """
    global bot_instance

    with bot_lock:
        if not bot_instance:
            return False, "Bot non démarré"

        try:
            # On demande l'arrêt via flag
            if getattr(bot_instance, "running", False):
                bot_instance.running = False
                return True, "Arrêt demandé (running=False)"
            else:
                return False, "Bot déjà arrêté"
        except Exception as e:
            return False, f"Erreur stop: {e}"


# ============================================================
# ROUTES
# ============================================================
@app.route("/", methods=["GET"])
def home():
    return jsonify({
        "status": "online",
        "service": "Rocket Sniper Bot API",
        "bot_available": BOT_AVAILABLE,
        "bot_running": bool(bot_instance and getattr(bot_instance, "running", False)),
        "endpoints": {
            "GET /": "Cette page",
            "POST/GET /start": "Démarrer le bot",
            "POST/GET /stop": "Arrêter le bot",
            "GET /status": "Statut complet",
            "GET /scan": "Scanner manuellement (nécessite bot running)",
            "GET/POST /toggle-auto": "Activer/désactiver auto-trading (nécessite bot running)",
            "POST /buy": "Acheter un token (JSON: token_address, sol_amount optionnel)",
            "GET/POST /test-telegram": "Envoyer un message test Telegram",
            "GET /health": "Healthcheck"
        }
    })


@app.route("/health", methods=["GET"])
def health():
    return jsonify({
        "status": "healthy",
        "timestamp": time.time()
    }), 200


@app.route("/start", methods=["POST", "GET"])
def start_route():
    ok, msg = start_bot()
    code = 200 if ok else 400
    return jsonify({
        "success": ok,
        "message": msg,
        "bot_running": bool(bot_instance and getattr(bot_instance, "running", False))
    }), code


@app.route("/stop", methods=["POST", "GET"])
def stop_route():
    ok, msg = stop_bot()
    code = 200 if ok else 400
    return jsonify({
        "success": ok,
        "message": msg,
        "bot_running": bool(bot_instance and getattr(bot_instance, "running", False))
    }), code


@app.route("/status", methods=["GET"])
def status_route():
    running = bool(bot_instance and getattr(bot_instance, "running", False))
    auto_trading = None
    stats = {}
    wallet_info = {}

    try:
        if bot_instance and getattr(bot_instance, "engine", None):
            auto_trading = bool(getattr(bot_instance.engine, "auto_trading", False))
            stats = getattr(bot_instance.engine, "stats", {}) or {}

        # Wallet balance via loop du bot (si dispo)
        if running and bot_instance and getattr(bot_instance, "wallet", None) and _loop_ready():
            bal = run_coro_threadsafe(bot_instance.wallet.get_balance(), timeout=12.0)
            addr = getattr(bot_instance.wallet, "address", None)
            wallet_info = {
                "address": (addr[:16] + "...") if addr else None,
                "balance_sol": bal
            }
        elif bot_instance and getattr(bot_instance, "wallet", None):
            addr = getattr(bot_instance.wallet, "address", None)
            wallet_info = {
                "address": (addr[:16] + "...") if addr else None,
                "balance_sol": None
            }
    except Exception as e:
        wallet_info = {"error": str(e)[:200]}

    return jsonify({
        "server": "online",
        "timestamp": time.time(),
        "bot": {
            "available": BOT_AVAILABLE,
            "running": running,
            "auto_trading": auto_trading,
            "stats": stats
        },
        "wallet": wallet_info,
        "config": {
            "auto_buy_amount": float(os.getenv("AUTO_BUY_AMOUNT", "0.01")),
            "min_age": 1,
            "min_liquidity": int(os.getenv("MIN_LIQUIDITY_USD", "50000")) if os.getenv("MIN_LIQUIDITY_USD") else 50000,
            "min_mcap": int(os.getenv("MIN_FDV_USD", "1000000")) if os.getenv("MIN_FDV_USD") else 1000000
        }
    })


@app.route("/toggle-auto", methods=["GET", "POST"])
def toggle_auto():
    if not bot_instance or not getattr(bot_instance, "running", False):
        return jsonify({"success": False, "error": "Bot non démarré"}), 400
    if not getattr(bot_instance, "engine", None):
        return jsonify({"success": False, "error": "Engine non disponible"}), 400

    try:
        bot_instance.engine.auto_trading = not bool(bot_instance.engine.auto_trading)
        return jsonify({
            "success": True,
            "auto_trading": bool(bot_instance.engine.auto_trading),
            "message": f"Auto-trading {'activé' if bot_instance.engine.auto_trading else 'désactivé'}"
        })
    except Exception as e:
        return jsonify({"success": False, "error": str(e)[:200]}), 500


@app.route("/scan", methods=["GET"])
def scan_route():
    if not bot_instance or not getattr(bot_instance, "running", False):
        return jsonify({"success": False, "error": "Bot non démarré"}), 400
    if not getattr(bot_instance, "engine", None):
        return jsonify({"success": False, "error": "Engine non disponible"}), 400
    if not _loop_ready():
        return jsonify({"success": False, "error": "Loop non prête"}), 500

    try:
        tokens = run_coro_threadsafe(bot_instance.engine.scan_dexscreener(), timeout=20.0)
        return jsonify({
            "success": True,
            "tokens_found": len(tokens),
            "tokens": tokens[:10]
        })
    except Exception as e:
        return jsonify({"success": False, "error": str(e)[:200]}), 500


@app.route("/buy", methods=["POST"])
def buy_route():
    if not bot_instance or not getattr(bot_instance, "running", False):
        return jsonify({"success": False, "error": "Bot non démarré"}), 400
    if not getattr(bot_instance, "engine", None):
        return jsonify({"success": False, "error": "Engine non disponible"}), 400
    if not _loop_ready():
        return jsonify({"success": False, "error": "Loop non prête"}), 500

    data = request.get_json(silent=True) or {}
    token_address = (data.get("token_address") or "").strip()
    if not token_address:
        return jsonify({"success": False, "error": "token_address requis"}), 400

    sol_amount = float(data.get("sol_amount") or float(os.getenv("AUTO_BUY_AMOUNT", "0.01")))

    try:
        ok, res = run_coro_threadsafe(
            bot_instance.engine.buy_token_real(token_address, sol_amount, source="api"),
            timeout=60.0
        )
        if ok:
            return jsonify({"success": True, "tx_hash": res, "message": "Achat réussi"})
        return jsonify({"success": False, "error": res}), 400
    except Exception as e:
        return jsonify({"success": False, "error": str(e)[:200]}), 500


@app.route("/test-telegram", methods=["GET", "POST"])
def test_telegram():
    """
    Envoie un message Telegram test (si telegram est initialisé).
    """
    if not bot_instance or not getattr(bot_instance, "running", False):
        return jsonify({"success": False, "error": "Bot non démarré"}), 400
    if not getattr(bot_instance, "telegram", None):
        return jsonify({"success": False, "error": "Telegram non initialisé"}), 400
    if not _loop_ready():
        return jsonify({"success": False, "error": "Loop non prête"}), 500

    # message custom optionnel
    if request.method == "POST":
        data = request.get_json(silent=True) or {}
        msg = (data.get("message") or "✅ Test Telegram: message envoyé depuis Render").strip()
    else:
        msg = "✅ Test Telegram: message envoyé depuis Render"

    try:
        ok = run_coro_threadsafe(bot_instance.telegram.send_message(msg), timeout=15.0)
        return jsonify({"success": True, "sent": bool(ok), "message": "Test Telegram exécuté"})
    except Exception as e:
        return jsonify({"success": False, "error": str(e)[:200]}), 500


# ============================================================
# DÉMARRAGE (Render utilise gunicorn => pas besoin de app.run)
# ============================================================
if __name__ == "__main__":
    port = int(os.environ.get("PORT", "5000"))
    logger.info(f"Starting Flask dev server on :{port}")
    app.run(host="0.0.0.0", port=port, debug=False)
