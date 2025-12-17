import os
import time
import asyncio
import threading
from flask import Flask, jsonify, request

# ============================================================
# IMPORT DE TON BOT
# ============================================================
from rocket_sniper import RealSniperBot   # adapte si le fichier a un autre nom

app = Flask(__name__)

# ============================================================
# VARIABLES GLOBALES
# ============================================================
bot_instance: RealSniperBot | None = None
bot_thread: threading.Thread | None = None
bot_loop: asyncio.AbstractEventLoop | None = None

BOT_AVAILABLE = True

# ============================================================
# GESTION THREAD / EVENT LOOP
# ============================================================
def run_bot():
    global bot_instance, bot_loop
    try:
        bot_loop = asyncio.new_event_loop()
        asyncio.set_event_loop(bot_loop)
        bot_instance = RealSniperBot()
        bot_loop.run_until_complete(bot_instance.start())
    except Exception as e:
        print(f"❌ Erreur bot: {e}")
    finally:
        if bot_loop and not bot_loop.is_closed():
            bot_loop.close()

def start_bot_internal():
    global bot_thread
    if bot_thread and bot_thread.is_alive():
        return False, "Bot déjà en cours d'exécution"

    bot_thread = threading.Thread(target=run_bot, daemon=True)
    bot_thread.start()
    time.sleep(2)
    return True, "Bot démarré"

def stop_bot_internal():
    global bot_instance
    if bot_instance and bot_instance.running:
        bot_instance.running = False
        return True
    return False

# ============================================================
# ROUTES API
# ============================================================
@app.route("/", methods=["GET"])
def home():
    return jsonify({
        "status": "online",
        "service": "Rocket Sniper Bot API",
        "bot_running": bot_instance.running if bot_instance else False,
        "bot_available": BOT_AVAILABLE,
        "endpoints": {
            "/start": "POST",
            "/stop": "POST",
            "/status": "GET",
            "/scan": "GET",
            "/toggle-auto": "GET / POST",
            "/buy": "POST",
            "/health": "GET"
        }
    })

@app.route("/start", methods=["POST"])
def start_bot():
    if not BOT_AVAILABLE:
        return jsonify({"success": False, "message": "Bot non disponible"}), 500

    success, message = start_bot_internal()
    return jsonify({
        "success": success,
        "message": message,
        "bot_running": bot_instance.running if bot_instance else False
    })

@app.route("/stop", methods=["POST"])
def stop_bot():
    success = stop_bot_internal()
    return jsonify({
        "success": success,
        "bot_running": False,
        "message": "Bot arrêté" if success else "Bot déjà arrêté"
    })

@app.route("/status", methods=["GET"])
def status():
    if not bot_instance:
        return jsonify({
            "server": "online",
            "bot": {"running": False, "available": True}
        })

    engine = bot_instance.engine
    wallet = bot_instance.wallet

    return jsonify({
        "server": "online",
        "timestamp": time.time(),
        "bot": {
            "running": bot_instance.running,
            "available": True,
            "auto_trading": engine.auto_trading if engine else False,
            "stats": engine.stats if engine else {}
        },
        "config": {
            "auto_buy_amount": engine.AUTO_BUY_AMOUNT if engine else None
        },
        "wallet": {
            "address": wallet.address if wallet else None,
            "balance_sol": wallet.balance if wallet else None
        }
    })

@app.route("/toggle-auto", methods=["GET", "POST"])
def toggle_auto():
    if not bot_instance or not bot_instance.engine:
        return jsonify({"success": False, "message": "Bot non démarré"}), 400

    bot_instance.engine.auto_trading = not bot_instance.engine.auto_trading

    return jsonify({
        "success": True,
        "auto_trading": bot_instance.engine.auto_trading,
        "message": f"Auto-trading {'activé' if bot_instance.engine.auto_trading else 'désactivé'}"
    })

@app.route("/scan", methods=["GET"])
def scan():
    if not bot_instance or not bot_instance.running:
        return jsonify({"success": False, "message": "Bot non démarré"}), 400

    async def do_scan():
        return await bot_instance.engine.scan_dexscreener()

    loop = asyncio.new_event_loop()
    tokens = loop.run_until_complete(do_scan())
    loop.close()

    return jsonify({
        "success": True,
        "tokens_found": len(tokens),
        "tokens": tokens[:5]
    })

@app.route("/buy", methods=["POST"])
def buy():
    if not bot_instance or not bot_instance.running:
        return jsonify({"success": False, "message": "Bot non démarré"}), 400

    data = request.get_json()
    token = data.get("token_address")
    sol_amount = data.get("sol_amount")

    if not token:
        return jsonify({"success": False, "message": "token_address requis"}), 400

    async def do_buy():
        return await bot_instance.engine.buy_token_real(token, sol_amount, source="api")

    loop = asyncio.new_event_loop()
    success, result = loop.run_until_complete(do_buy())
    loop.close()

    return jsonify({
        "success": success,
        "result": result
    })

@app.route("/health", methods=["GET"])
def health():
    return jsonify({
        "status": "healthy",
        "time": time.time()
    })

# ============================================================
# DÉMARRAGE RENDER
# ============================================================
if __name__ == "__main__":
    port = int(os.environ.get("PORT", 5000))
    print(f"🚀 Web server démarré sur le port {port}")
    app.run(host="0.0.0.0", port=port)
