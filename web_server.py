import os
import time
import asyncio
import threading
from flask import Flask, jsonify, request

# ✅ IMPORTANT: adapte si ton fichier/classe a un autre nom
from rocket_sniper import RealSniperBot

app = Flask(__name__)

bot_instance = None
bot_thread = None
bot_loop = None

BOT_AVAILABLE = True


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
        try:
            if bot_loop and not bot_loop.is_closed():
                bot_loop.close()
        except:
            pass


def start_bot_internal():
    global bot_thread
    if bot_thread and bot_thread.is_alive():
        return False, "Bot déjà en cours d'exécution"

    bot_thread = threading.Thread(target=run_bot, daemon=True)
    bot_thread.start()
    return True, "Bot démarré"


def stop_bot_internal():
    global bot_instance
    if bot_instance and getattr(bot_instance, "running", False):
        bot_instance.running = False
        return True
    return False


@app.route("/", methods=["GET"])
def home():
    return jsonify({
        "status": "online",
        "service": "Rocket Sniper Bot API",
        "bot_available": BOT_AVAILABLE,
        "bot_running": bool(bot_instance and getattr(bot_instance, "running", False)),
        "endpoints": [
            "/start (POST)",
            "/stop (POST)",
            "/status (GET)",
            "/scan (GET)",
            "/toggle-auto (GET/POST)",
            "/buy (POST)",
            "/health (GET)",
        ]
    })


@app.route("/start", methods=["POST"])
def start_bot():
    if not BOT_AVAILABLE:
        return jsonify({"success": False, "message": "Bot non disponible"}), 500

    ok, msg = start_bot_internal()
    return jsonify({
        "success": ok,
        "message": msg,
        "bot_running": bool(bot_instance and getattr(bot_instance, "running", False)),
    })


@app.route("/stop", methods=["POST"])
def stop_bot():
    ok = stop_bot_internal()
    return jsonify({
        "success": ok,
        "message": "Bot arrêté" if ok else "Bot déjà arrêté",
        "bot_running": False,
    })


@app.route("/toggle-auto", methods=["GET", "POST"])
def toggle_auto():
    if not bot_instance or not getattr(bot_instance, "engine", None):
        return jsonify({"success": False, "message": "Bot non démarré / engine pas prêt"}), 400

    engine = bot_instance.engine
    engine.auto_trading = not bool(getattr(engine, "auto_trading", False))

    return jsonify({
        "success": True,
        "auto_trading": engine.auto_trading,
        "message": f"Auto-trading {'activé' if engine.auto_trading else 'désactivé'}"
    })


@app.route("/status", methods=["GET"])
def status():
    # ✅ ROUTE SAFE: jamais de crash
    try:
        running = bool(bot_instance and getattr(bot_instance, "running", False))
        engine = getattr(bot_instance, "engine", None) if bot_instance else None
        wallet = getattr(bot_instance, "wallet", None) if bot_instance else None

        auto_trading = bool(getattr(engine, "auto_trading", False)) if engine else False
        stats = getattr(engine, "stats", {}) if engine else {}

        addr = getattr(wallet, "address", None) if wallet else None

        # balance_sol: on essaie, sinon 0 sans casser
        balance_sol = 0.0
        try:
            bal = getattr(wallet, "balance", None)
            if isinstance(bal, (int, float)):
                balance_sol = float(bal)
        except:
            pass

        return jsonify({
            "server": "online",
            "timestamp": time.time(),
            "bot": {
                "available": True,
                "running": running,
                "auto_trading": auto_trading,
                "stats": stats
            },
            "wallet": {
                "address": addr,
                "balance_sol": balance_sol
            }
        })
    except Exception as e:
        # même ici: on renvoie un JSON propre
        return jsonify({
            "server": "online",
            "success": False,
            "error": f"/status crash: {str(e)}"
        }), 200


@app.route("/scan", methods=["GET"])
def scan():
    if not bot_instance or not getattr(bot_instance, "running", False):
        return jsonify({"success": False, "message": "Bot non démarré"}), 400
    if not getattr(bot_instance, "engine", None):
        return jsonify({"success": False, "message": "Engine pas prêt"}), 400

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
    if not bot_instance or not getattr(bot_instance, "running", False):
        return jsonify({"success": False, "message": "Bot non démarré"}), 400
    if not getattr(bot_instance, "engine", None):
        return jsonify({"success": False, "message": "Engine pas prêt"}), 400

    data = request.get_json(silent=True) or {}
    token = data.get("token_address")
    sol_amount = data.get("sol_amount")

    if not token:
        return jsonify({"success": False, "message": "token_address requis"}), 400

    async def do_buy():
        return await bot_instance.engine.buy_token_real(token, sol_amount, source="api")

    loop = asyncio.new_event_loop()
    ok, result = loop.run_until_complete(do_buy())
    loop.close()

    return jsonify({"success": ok, "result": result})


@app.route("/health", methods=["GET"])
def health():
    return jsonify({"status": "healthy", "timestamp": time.time()}), 200


if __name__ == "__main__":
    port = int(os.environ.get("PORT", 5000))
    print(f"🚀 Web server démarré sur le port {port}")
    app.run(host="0.0.0.0", port=port)
