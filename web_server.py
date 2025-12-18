#!/usr/bin/env python3
# -*- coding: utf-8 -*-

"""
Rocket Sniper Bot - Web Server (Render)
Corrections:
- toggle-auto accepte GET+POST
- scan debug: renvoie raisons de rejet
- scan raw: renvoie paires brutes
- Event loop unique en background + run_coroutine_threadsafe
- status reflète engine.auto_trading réel
"""

from dotenv import load_dotenv
load_dotenv()

import os
import sys
import time
import json
import asyncio
import threading
import logging
from typing import Any, Dict, List, Optional, Tuple

from flask import Flask, jsonify, request

# =========================================
# Logging
# =========================================
logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s - %(levelname)s - %(message)s",
)
logger = logging.getLogger("web_server")

# =========================================
# Import de ton bot (le gros fichier)
# IMPORTANT: adapte si ton bot est dans un autre module
# =========================================
try:
    # Si tout est dans ce fichier (comme dans ton paste), laisse comme ça
    # Sinon, fais: from rocket_sniper import RealSniperBot, Config
    from web_server import RealSniperBot, Config  # <-- À SUPPRIMER si circular import
except Exception:
    # Dans ton cas actuel, RealSniperBot & Config sont DANS le même fichier original.
    # Donc ici, on ne peut pas les importer depuis web_server lui-même.
    # Solution: tu dois avoir ton bot dans un autre fichier (ex: rocket_sniper.py)
    # et web_server.py ne contient QUE l'API.
    #
    # => On fait un import dynamique plus clair :
    try:
        from rocket_sniper import RealSniperBot, Config  # recommandé
    except Exception as e:
        logger.error("❌ Impossible d'importer RealSniperBot/Config. "
                     "Mets ton bot dans rocket_sniper.py et ajuste l'import.")
        raise

# =========================================
# Flask
# =========================================
app = Flask(__name__)

# =========================================
# Globals Bot runtime
# =========================================
BOT_AVAILABLE = True

bot_instance: Optional[RealSniperBot] = None

bot_thread: Optional[threading.Thread] = None
bot_loop: Optional[asyncio.AbstractEventLoop] = None

# Lock simple pour éviter doubles start/stop simultanés
state_lock = threading.Lock()


def _loop_thread_target():
    """Thread target: crée un event loop et le garde vivant."""
    global bot_loop
    bot_loop = asyncio.new_event_loop()
    asyncio.set_event_loop(bot_loop)
    logger.info("✅ Event loop background démarrée")
    bot_loop.run_forever()
    logger.info("🛑 Event loop background stoppée")


def ensure_loop_running() -> bool:
    """S'assure que la loop background existe et tourne."""
    global bot_thread, bot_loop
    with state_lock:
        if bot_thread and bot_thread.is_alive() and bot_loop and not bot_loop.is_closed():
            return True

        bot_thread = threading.Thread(target=_loop_thread_target, daemon=True)
        bot_thread.start()

        # Attendre que la loop soit prête
        for _ in range(50):
            if bot_loop is not None:
                return True
            time.sleep(0.1)

        return False


def run_async(coro, timeout: float = 30.0):
    """Exécute un coroutine dans la loop background."""
    if not ensure_loop_running() or bot_loop is None:
        raise RuntimeError("Event loop background indisponible")

    fut = asyncio.run_coroutine_threadsafe(coro, bot_loop)
    return fut.result(timeout=timeout)


def start_bot() -> Tuple[bool, str]:
    """Démarre le bot (si pas déjà running)."""
    global bot_instance

    with state_lock:
        if bot_instance and getattr(bot_instance, "running", False):
            return False, "Bot déjà en cours d'exécution"

        # Créer instance
        bot_instance = RealSniperBot()

    # Lancer bot.start() dans la loop background
    try:
        run_async(bot_instance.start(), timeout=5.0)  # start() lance ensuite sa boucle run()
        return True, "Bot démarré"
    except asyncio.TimeoutError:
        # start() ne doit pas bloquer le thread Flask; timeout court = ok
        return True, "Bot démarré (initialisation en cours)"
    except Exception as e:
        logger.exception("❌ Erreur start_bot")
        return False, f"Erreur démarrage: {str(e)[:180]}"


def stop_bot() -> Tuple[bool, str]:
    """Stop propre."""
    global bot_instance
    with state_lock:
        if not bot_instance:
            return False, "Bot non démarré"
        if not getattr(bot_instance, "running", False):
            return False, "Bot déjà arrêté"

    try:
        # stop() existe dans ton code
        run_async(bot_instance.stop(), timeout=15.0)
        return True, "Bot arrêté"
    except Exception as e:
        logger.exception("❌ Erreur stop_bot")
        return False, f"Erreur arrêt: {str(e)[:180]}"


# =========================================
# Routes
# =========================================
@app.route("/", methods=["GET"])
def home():
    return jsonify({
        "status": "online",
        "service": "Rocket Sniper Bot API",
        "bot_available": BOT_AVAILABLE,
        "endpoints": [
            "/",
            "/start (GET/POST)",
            "/stop (GET/POST)",
            "/status (GET)",
            "/scan (GET)  + ?debug=1 ou ?raw=1",
            "/toggle-auto (GET/POST)",
            "/health (GET)",
        ]
    })

@app.route("/health", methods=["GET"])
def health():
    return jsonify({"status": "healthy", "timestamp": time.time()}), 200
    
@app.route('/scan')
def scan():
    """Endpoint de scan temporaire"""
    try:
        # Vérifie si le bot est démarré (adapte à ta variable)
        # if not bot_running:
        #     return jsonify({"success": False, "message": "Bot non démarré"})
        
        # Test simple - retourne des tokens factices pour confirmer que l'endpoint marche
        test_tokens = [
            {
                "address": "So11111111111111111111111111111111111111112",
                "symbol": "TEST1",
                "liquidity": 150000,
                "market_cap": 2000000,
                "volume_24h": 120000,
                "age_minutes": 45
            },
            {
                "address": "EPjFWdd5AufqSSqeM2qN1xzybapC8G4wEGGkZwyTDt1v",
                "symbol": "TEST2",
                "liquidity": 80000,
                "market_cap": 1500000,
                "volume_24h": 90000,
                "age_minutes": 30
            }
        ]
        
        return jsonify({
            "success": True,
            "tokens": test_tokens,
            "tokens_found": len(test_tokens),
            "debug": {
                "mode": "test_data",
                "next_step": "Implement real data source"
            }
        })
        
    except Exception as e:
        return jsonify({
            "success": False,
            "error": str(e),
            "traceback": "Check Render logs for details"
        }), 500
@app.route('/debug_fetch')
def debug_fetch():
    """Test endpoint - version corrigée avec sources alternatives"""
    import urllib.request
    import json
    import time
    
    # Liste des sources à tester
    sources = [
        {
            "name": "Helius API (newest tokens)",
            "url": "https://api.helius.xyz/v0/token-metadata?api-key=DEMO_KEY",
            "headers": {"User-Agent": "Mozilla/5.0"},
            "enabled": False  # Mettre True si tu as une clé
        },
        {
            "name": "DexScreener (popular pairs)",
            "url": "https://api.dexscreener.com/latest/dex/pairs/solana?limit=20",
            "headers": {
                "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36",
                "Accept": "application/json"
            },
            "enabled": True
        },
        {
            "name": "Birdeye (trending)",
            "url": "https://public-api.birdeye.so/defi/v3/tokenlist?sort_by=v24hUSD&sort_type=desc",
            "headers": {
                "User-Agent": "Mozilla/5.0",
                "X-API-KEY": ""  # À remplir si tu as une clé
            },
            "enabled": False
        },
        {
            "name": "Pump.fun (new tokens)",
            "url": "https://frontend-api.pump.fun/coins",
            "headers": {"User-Agent": "Mozilla/5.0"},
            "enabled": True
        }
    ]
    
    for source in sources:
        if not source["enabled"]:
            continue
            
        try:
            print(f"[DEBUG] Testing source: {source['name']}", flush=True)
            start = time.time()
            
            req = urllib.request.Request(
                source["url"],
                headers=source["headers"]
            )
            
            with urllib.request.urlopen(req, timeout=15) as response:
                elapsed = time.time() - start
                data = response.read().decode('utf-8')
                
                print(f"[DEBUG] {source['name']}: HTTP {response.status} in {elapsed:.2f}s", flush=True)
                
                # Essayer de parser JSON
                try:
                    json_data = json.loads(data)
                    
                    # Analyse basique selon la source
                    if source["name"] == "DexScreener":
                        pairs = json_data.get("pairs", [])
                        sample = []
                        for pair in pairs[:5]:
                            sample.append({
                                "symbol": pair.get("baseToken", {}).get("symbol", "?"),
                                "liquidity": pair.get("liquidity", {}).get("usd", 0),
                                "price": pair.get("priceUsd", 0),
                                "created": pair.get("pairCreatedAt", 0)
                            })
                        return jsonify({
                            "success": True,
                            "source": source["name"],
                            "status": response.status,
                            "total_items": len(pairs),
                            "sample": sample
                        })
                        
                    elif source["name"] == "Pump.fun":
                        # Pump.fun retourne une liste de coins
                        coins = json_data if isinstance(json_data, list) else []
                        sample = []
                        for coin in coins[:5]:
                            sample.append({
                                "symbol": coin.get("symbol", "?"),
                                "mint": coin.get("mint", "?")[:15] + "...",
                                "price": coin.get("price", 0),
                                "created": coin.get("createdAt", 0)
                            })
                        return jsonify({
                            "success": True,
                            "source": source["name"],
                            "status": response.status,
                            "total_items": len(coins),
                            "sample": sample
                        })
                        
                except json.JSONDecodeError:
                    return jsonify({
                        "success": True,
                        "source": source["name"],
                        "status": response.status,
                        "raw_preview": data[:200],
                        "note": "Response is not JSON"
                    })
                    
        except Exception as e:
            print(f"[DEBUG] Source {source['name']} failed: {str(e)}", flush=True)
            continue
    
    # Si toutes les sources échouent
    return jsonify({
        "success": False,
        "error": "All sources failed",
        "hint": "Try enabling Helius or Birdeye with API keys",
        "config_check": "Do you have HELIUS_API_KEY or BIRDEYE_API_KEY in env?"
    })

@app.route("/start", methods=["GET", "POST"])
def start_route():
    if not BOT_AVAILABLE:
        return jsonify({"success": False, "message": "Bot non disponible"}), 500

    ok, msg = start_bot()
    return jsonify({"success": ok, "message": msg, "bot_running": bool(bot_instance and getattr(bot_instance, "running", False))}), (200 if ok else 400)


@app.route("/stop", methods=["GET", "POST"])
def stop_route():
    ok, msg = stop_bot()
    return jsonify({"success": ok, "message": msg, "bot_running": bool(bot_instance and getattr(bot_instance, "running", False))}), (200 if ok else 400)


@app.route("/status", methods=["GET"])
def status_route():
    running = bool(bot_instance and getattr(bot_instance, "running", False))
    engine = getattr(bot_instance, "engine", None) if bot_instance else None
    wallet = getattr(bot_instance, "wallet", None) if bot_instance else None

    resp = {
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
            "min_mcap": getattr(Config, "MIN_MARKET_CAP_USD", None),
            "min_volume_24h": getattr(Config, "MIN_VOLUME_24H_USD", None),
        }
    }

    if wallet and getattr(wallet, "address", None):
        # balance async si possible
        try:
            bal = run_async(wallet.get_balance(), timeout=10.0)
        except Exception:
            bal = None
        resp["wallet"] = {"address": str(wallet.address), "balance_sol": bal}

    return jsonify(resp)


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
    if not bot_instance or not getattr(bot_instance, "running", False):
        return jsonify({"success": False, "message": "Bot non démarré"}), 400
    if not getattr(bot_instance, "engine", None):
        return jsonify({"success": False, "message": "Engine pas prêt"}), 400

    raw = request.args.get("raw", "0") == "1"
    debug = request.args.get("debug", "0") == "1"

    # --- RAW MODE: renvoie des paires brutes DexScreener
    if raw:
        try:
            import aiohttp
            async def fetch_raw():
                url = "https://api.dexscreener.com/latest/dex/pairs/solana"
                async with aiohttp.ClientSession() as session:
                    async with session.get(url, timeout=15) as resp:
                        data = await resp.json()
                        pairs = data.get("pairs", [])[:50]
                        return {"pairs_count": len(pairs), "pairs": pairs}

            loop = asyncio.new_event_loop()
            out = loop.run_until_complete(fetch_raw())
            loop.close()
            return jsonify({"success": True, **out})

        except Exception as e:
            return jsonify({"success": False, "message": f"raw error: {str(e)[:180]}"}), 500

    # --- NORMAL MODE: scan filtré
    try:
        loop = asyncio.new_event_loop()
        tokens = loop.run_until_complete(bot_instance.engine.scan_dexscreener())
        loop.close()
    except Exception as e:
        return jsonify({"success": False, "message": f"scan error: {str(e)[:180]}"}), 500

    resp = {"success": True, "tokens_found": len(tokens), "tokens": tokens[:10]}

    # --- DEBUG MODE: donne les raisons de rejet (si ta méthode existe)
    if debug and hasattr(bot_instance.engine, "check_token_criteria"):
        try:
            import aiohttp
            async def debug_rejects():
                url = "https://api.dexscreener.com/latest/dex/pairs/solana"
                async with aiohttp.ClientSession() as session:
                    async with session.get(url, timeout=15) as resp2:
                        data = await resp2.json()

                pairs = data.get("pairs", [])[:80]
                rejects = []
                for p in pairs:
                    addr = (p.get("baseToken") or {}).get("address")
                    sym = (p.get("baseToken") or {}).get("symbol", "UNK")
                    if not addr:
                        continue
                    ok, reason = await bot_instance.engine.check_token_criteria(addr)
                    if not ok:
                        rejects.append({"symbol": sym, "address": addr, "reason": reason})
                    if len(rejects) >= 20:
                        break
                return rejects

            loop = asyncio.new_event_loop()
            resp["debug_rejects"] = loop.run_until_complete(debug_rejects())
            loop.close()
        except Exception as e:
            resp["debug_error"] = str(e)[:180]

    return jsonify(resp)

# =========================================
# Render entry (Gunicorn)
# =========================================
# Ton Start Command Render est: gunicorn web_server:app --bind 0.0.0.0:$PORT
# Donc il faut bien que "app" soit exposé ici.

if __name__ == "__main__":
    port = int(os.environ.get("PORT", "5000"))
    app.run(host="0.0.0.0", port=port, debug=False)
