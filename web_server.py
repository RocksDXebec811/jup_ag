#!/usr/bin/env python3
"""
Rocket Sniper Bot - Web Server (Render)
Corrections:
- /toggle-auto GET+POST
- /test-telegram endpoint
- /status affiche engine.auto_trading réel
- appels async via run_coroutine_threadsafe sur la loop du bot (pas de new_event_loop dans les routes)
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
from typing import Optional, Tuple, Any, Dict

import aiohttp
from flask import Flask, jsonify, request

# ------------------------------------------------------------
# LOGGING
# ------------------------------------------------------------
logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s - %(levelname)s - %(message)s",
)
logger = logging.getLogger("web_server")


# ------------------------------------------------------------
# CONFIG (env)
# ------------------------------------------------------------
class Config:
    TELEGRAM_TOKEN = os.getenv("TELEGRAM_TOKEN", "").strip()
    TELEGRAM_CHAT_ID = os.getenv("TELEGRAM_CHAT_ID", "").strip()

    WALLET_PRIVATE_KEY = os.getenv("WALLET_PRIVATE_KEY", "").strip()
    RPC_URL = os.getenv("RPC_URL", "https://api.mainnet-beta.solana.com").strip()

    AUTO_BUY_AMOUNT = float(os.getenv("AUTO_BUY_AMOUNT", "0.01"))
    AUTO_BUY_ENABLED = os.getenv("AUTO_BUY_ENABLED", "False").lower() == "true"
    SLIPPAGE_BPS = int(os.getenv("SLIPPAGE_BPS", "100"))

    # Critères (ex: Dexscreener)
    MIN_AGE_MINUTES = int(os.getenv("MIN_AGE_MINUTES", "1"))
    MAX_AGE_MINUTES = int(os.getenv("MAX_AGE_MINUTES", "60"))
    MIN_LIQUIDITY_USD = float(os.getenv("MIN_LIQUIDITY_USD", "50000"))
    MIN_MARKET_CAP_USD = float(os.getenv("MIN_MARKET_CAP_USD", "1000000"))
    MIN_VOLUME_24H_USD = float(os.getenv("MIN_24H_VOLUME_USD", "100000"))

    SCAN_INTERVAL = int(os.getenv("SCAN_INTERVAL", "30"))
    CHECK_INTERVAL = int(os.getenv("CHECK_INTERVAL", "60"))
    STATUS_INTERVAL = int(os.getenv("STATUS_INTERVAL", "300"))


# ------------------------------------------------------------
# TELEGRAM (simple)
# ------------------------------------------------------------
class TelegramSimple:
    def __init__(self):
        self.token = Config.TELEGRAM_TOKEN
        self.chat_id = Config.TELEGRAM_CHAT_ID
        self.session: Optional[aiohttp.ClientSession] = None

    async def start(self) -> bool:
        if not self.token or not self.chat_id:
            logger.error("❌ Telegram non configuré (TELEGRAM_TOKEN / TELEGRAM_CHAT_ID)")
            return False

        self.session = aiohttp.ClientSession()
        ok = await self.send_message("🤖 Rocket Sniper Bot - démarré (Render)")
        if ok:
            logger.info("✅ Telegram initialisé")
        else:
            logger.error("❌ Telegram init échoué (sendMessage KO)")
        return ok

    async def send_message(self, text: str, use_html: bool = False) -> bool:
        if not self.session:
            return False

        try:
            url = f"https://api.telegram.org/bot{self.token}/sendMessage"
            payload = {
                "chat_id": self.chat_id,
                "text": text[:4000],
                "disable_web_page_preview": False
            }
            if use_html:
                payload["parse_mode"] = "HTML"

            async with self.session.post(url, json=payload, timeout=10) as resp:
                return resp.status == 200
        except Exception as e:
            logger.error(f"❌ Telegram send_message error: {e}")
            return False

    async def close(self):
        if self.session:
            await self.session.close()
            self.session = None


# ------------------------------------------------------------
# IMPORTS SOLANA / JUPITER (dépendances)
# ------------------------------------------------------------
try:
    from solders.keypair import Keypair
    from solders.pubkey import Pubkey
    from solders.signature import Signature
    from solders.transaction import VersionedTransaction
    from solana.rpc.async_api import AsyncClient
    from solana.rpc.commitment import Confirmed
    from spl.token.instructions import get_associated_token_address
    import base58
except Exception as e:
    logger.error(f"❌ Imports Solana/SPL manquants: {e}")
    logger.error("📦 Installe: pip install solana solders spl-token aiohttp python-dotenv base58")
    # Sur Render, si ça plante ici, le service ne démarre pas.
    # On force l'arrêt propre.
    sys.exit(1)


# ------------------------------------------------------------
# WALLET
# ------------------------------------------------------------
class RealWallet:
    def __init__(self, private_key: str):
        self.private_key = private_key.strip()
        self.keypair: Optional[Keypair] = None
        self.address: Optional[str] = None
        self.client: Optional[AsyncClient] = None
        self.session: Optional[aiohttp.ClientSession] = None
        self.initialized = False
        self.balance = 0.0

    async def initialize(self) -> bool:
        try:
            decoded = base58.b58decode(self.private_key)

            if len(decoded) == 64:
                self.keypair = Keypair.from_bytes(decoded)
            elif len(decoded) == 32:
                self.keypair = Keypair.from_seed(decoded)
            else:
                # fallback: seed 32 bytes
                seed = decoded[:32].ljust(32, b"\x00")
                self.keypair = Keypair.from_seed(seed)

            self.address = str(self.keypair.pubkey())
            self.client = AsyncClient(Config.RPC_URL)
            self.session = aiohttp.ClientSession()
            self.initialized = True
            logger.info(f"✅ Wallet prêt: {self.address}")
            return True
        except Exception as e:
            logger.error(f"❌ Wallet initialize error: {e}")
            return False

    async def get_balance(self) -> float:
        if not self.initialized:
            ok = await self.initialize()
            if not ok:
                return 0.0

        try:
            resp = await self.client.get_balance(Pubkey.from_string(self.address))
            if resp and resp.value is not None:
                self.balance = resp.value / 1_000_000_000
                return self.balance
        except Exception as e:
            logger.error(f"⚠ get_balance error: {e}")
        return 0.0

    async def get_token_balance(self, token_mint: str) -> float:
        if not self.initialized:
            ok = await self.initialize()
            if not ok:
                return 0.0
        try:
            ata = get_associated_token_address(
                Pubkey.from_string(self.address),
                Pubkey.from_string(token_mint)
            )
            resp = await self.client.get_token_account_balance(ata)
            if resp and resp.value:
                # NOTE: decimals inconnus ici, donc on renvoie amount brut en float
                # Si tu veux être exact, récupère decimals via get_token_supply / metadata.
                return float(resp.value.amount)
        except Exception:
            return 0.0
        return 0.0

    async def close(self):
        try:
            if self.session:
                await self.session.close()
                self.session = None
            if self.client:
                await self.client.close()
                self.client = None
        except Exception:
            pass


# ------------------------------------------------------------
# JUPITER (réel)
# ------------------------------------------------------------
class JupiterReal:
    QUOTE_URL = "https://quote-api.jup.ag/v6/quote"
    SWAP_URL = "https://quote-api.jup.ag/v6/swap"

    def __init__(self, wallet: RealWallet):
        self.wallet = wallet

    async def get_quote(self, input_mint: str, output_mint: str, amount_lamports: int) -> Optional[Dict]:
        try:
            params = {
                "inputMint": input_mint,
                "outputMint": output_mint,
                "amount": str(amount_lamports),
                "slippageBps": str(Config.SLIPPAGE_BPS),
                "feeBps": "0",
            }
            async with aiohttp.ClientSession() as s:
                async with s.get(self.QUOTE_URL, params=params, timeout=10) as r:
                    if r.status == 200:
                        return await r.json()
                    return None
        except Exception as e:
            logger.error(f"❌ get_quote error: {e}")
            return None

    async def execute_swap(self, quote_response: Dict) -> Tuple[Optional[str], str]:
        """
        ⚠️ Cette fonction exécute une transaction réelle.
        """
        try:
            swap_data = {
                "quoteResponse": quote_response,
                "userPublicKey": str(self.wallet.keypair.pubkey()),
                "wrapAndUnwrapSol": True,
                "dynamicComputeUnitLimit": True,
            }

            async with aiohttp.ClientSession() as s:
                async with s.post(self.SWAP_URL, json=swap_data, timeout=20) as r:
                    if r.status != 200:
                        return None, f"swap api error {r.status}: {await r.text()}"

                    data = await r.json()
                    raw_tx = data.get("swapTransaction")
                    if not raw_tx:
                        return None, "No swapTransaction in response"

                    # decode tx
                    try:
                        tx_bytes = base58.b58decode(raw_tx)
                    except Exception:
                        import base64
                        tx_bytes = base64.b64decode(raw_tx)

                    tx = VersionedTransaction.from_bytes(tx_bytes)
                    tx.sign([self.wallet.keypair])

                    send_resp = await self.wallet.client.send_raw_transaction(
                        bytes(tx),
                        opts={"skip_preflight": False, "preflight_commitment": "confirmed"},
                    )

                    if not send_resp or not send_resp.value:
                        return None, "Failed to send transaction"

                    sig = str(send_resp.value)

                    # confirm
                    await asyncio.sleep(2)
                    conf = await self.wallet.client.confirm_transaction(
                        Signature.from_string(sig),
                        commitment=Confirmed,
                    )

                    if conf and conf.value and conf.value[0]:
                        return sig, "Success"
                    return sig, "Sent, but confirmation uncertain"

        except Exception as e:
            logger.error(f"❌ execute_swap error: {e}")
            return None, str(e)

    async def swap_sol_to_token(self, token_mint: str, sol_amount: float) -> Tuple[Optional[str], str]:
        sol_mint = "So11111111111111111111111111111111111111112"
        lamports = int(sol_amount * 1_000_000_000)
        quote = await self.get_quote(sol_mint, token_mint, lamports)
        if not quote:
            return None, "Failed to get quote"
        return await self.execute_swap(quote)


# ------------------------------------------------------------
# ENGINE (exemple minimal basé DexScreener)
# ------------------------------------------------------------
class RealTradingEngine:
    def __init__(self, wallet: RealWallet, telegram: TelegramSimple):
        self.wallet = wallet
        self.telegram = telegram
        self.jupiter = JupiterReal(wallet)
        self.auto_trading = Config.AUTO_BUY_ENABLED

        self.stats = {
            "total_buys": 0,
            "total_sells": 0,
            "sol_spent": 0.0,
            "sol_earned": 0.0,
            "total_profit": 0.0
        }

    async def scan_dexscreener(self) -> list:
        """
        Exemple simple: renvoie des tokens filtrés.
        """
        url = "https://api.dexscreener.com/latest/dex/pairs/solana"
        try:
            async with aiohttp.ClientSession() as s:
                async with s.get(url, timeout=10) as r:
                    if r.status != 200:
                        return []
                    data = await r.json()

            pairs = data.get("pairs", [])[:50]
            out = []

            now_ms = int(time.time() * 1000)

            for p in pairs:
                base = (p.get("baseToken") or {})
                mint = base.get("address")
                if not mint:
                    continue

                liquidity = float(((p.get("liquidity") or {}).get("usd")) or 0)
                volume = float(((p.get("volume") or {}).get("h24")) or 0)
                mcap = float(p.get("fdv") or p.get("marketCap") or 0)
                created = int(p.get("pairCreatedAt") or 0)
                age_min = (now_ms - created) / 60000 if created else 9999

                if age_min < Config.MIN_AGE_MINUTES or age_min > Config.MAX_AGE_MINUTES:
                    continue
                if liquidity < Config.MIN_LIQUIDITY_USD:
                    continue
                if volume < Config.MIN_VOLUME_24H_USD:
                    continue
                if mcap < Config.MIN_MARKET_CAP_USD:
                    continue

                out.append({
                    "address": mint,
                    "symbol": base.get("symbol", "UNKNOWN"),
                    "priceUsd": p.get("priceUsd"),
                    "liquidity": liquidity,
                    "volume24h": volume,
                    "mcap": mcap,
                    "ageMin": age_min,
                    "url": p.get("url")
                })

            return out
        except Exception as e:
            logger.error(f"scan_dexscreener error: {e}")
            return []

    async def buy_token_real(self, token_address: str, sol_amount: float) -> Tuple[bool, str]:
        """
        ⚠️ ACHAT RÉEL.
        """
        bal = await self.wallet.get_balance()
        if bal < sol_amount:
            return False, f"Insufficient balance: {bal:.4f} SOL"

        tx, msg = await self.jupiter.swap_sol_to_token(token_address, sol_amount)
        if not tx:
            return False, msg

        self.stats["total_buys"] += 1
        self.stats["sol_spent"] += sol_amount

        await self.telegram.send_message(
            f"✅ Achat exécuté\nToken: {token_address}\nMontant: {sol_amount} SOL\nTX: https://solscan.io/tx/{tx}"
        )
        return True, tx


# ------------------------------------------------------------
# BOT (thread + loop)
# ------------------------------------------------------------
class RealSniperBot:
    def __init__(self):
        pk = Config.WALLET_PRIVATE_KEY
        if not pk:
            raise RuntimeError("WALLET_PRIVATE_KEY manquant dans les variables d'environnement")

        self.wallet = RealWallet(pk)
        self.telegram = TelegramSimple()
        self.engine: Optional[RealTradingEngine] = None
        self.running = False
        self.start_ts = time.time()

    async def start(self):
        ok = await self.wallet.initialize()
        if not ok:
            raise RuntimeError("Wallet init failed")

        # Telegram (si mal config => bot tourne mais sans messages)
        await self.telegram.start()

        self.engine = RealTradingEngine(self.wallet, self.telegram)
        self.running = True

        await self.telegram.send_message("🚀 Bot démarré (loop active).")

        # boucle simple
        while self.running:
            # ici tu peux remettre ton auto-scan / auto-buy si tu veux
            await asyncio.sleep(1)

    async def stop(self):
        self.running = False
        try:
            await self.telegram.send_message("👋 Bot arrêté.")
        except Exception:
            pass
        await self.telegram.close()
        await self.wallet.close()


# ------------------------------------------------------------
# FLASK APP
# ------------------------------------------------------------
app = Flask(__name__)

BOT_AVAILABLE = True
bot_instance: Optional[RealSniperBot] = None
bot_thread: Optional[threading.Thread] = None
bot_loop: Optional[asyncio.AbstractEventLoop] = None


def _run_bot_thread():
    global bot_instance, bot_loop
    try:
        bot_loop = asyncio.new_event_loop()
        asyncio.set_event_loop(bot_loop)

        bot_instance = RealSniperBot()
        bot_loop.run_until_complete(bot_instance.start())
    except Exception as e:
        logger.error(f"❌ Bot thread crashed: {e}")
    finally:
        try:
            if bot_loop and not bot_loop.is_closed():
                bot_loop.close()
        except Exception:
            pass


def start_bot() -> Tuple[bool, str]:
    global bot_thread

    if bot_thread and bot_thread.is_alive():
        return False, "Bot déjà en cours d'exécution"

    bot_thread = threading.Thread(target=_run_bot_thread, daemon=True)
    bot_thread.start()
    time.sleep(1.0)
    return True, "Bot démarré"


def stop_bot() -> bool:
    global bot_instance, bot_loop, bot_thread
    if not bot_instance:
        return False

    try:
        bot_instance.running = False
        if bot_loop and bot_loop.is_running():
            # on schedule un stop propre
            asyncio.run_coroutine_threadsafe(bot_instance.stop(), bot_loop)
    except Exception:
        pass
    return True


def submit_coro(coro, timeout: int = 25) -> Any:
    """
    Exécute un coroutine dans la loop du bot (thread-safe).
    """
    if not bot_loop or not bot_loop.is_running():
        raise RuntimeError("Bot loop not running")
    fut = asyncio.run_coroutine_threadsafe(coro, bot_loop)
    return fut.result(timeout=timeout)


# ------------------------------------------------------------
# ROUTES
# ------------------------------------------------------------
@app.route("/", methods=["GET"])
def home():
    return jsonify({
        "status": "online",
        "service": "Rocket Sniper Bot API",
        "bot_available": BOT_AVAILABLE,
        "bot_running": bool(bot_instance and bot_instance.running),
        "endpoints": [
            "/",
            "/start  (GET/POST)",
            "/stop   (GET/POST)",
            "/status (GET)",
            "/scan   (GET/POST)",
            "/toggle-auto (GET/POST)",
            "/buy    (POST json: {token_address, sol_amount})",
            "/test-telegram (GET)",
            "/health (GET)",
        ]
    })


@app.route("/start", methods=["GET", "POST"])
def start_bot_route():
    if not BOT_AVAILABLE:
        return jsonify({"success": False, "error": "Bot non disponible"}), 500

    ok, msg = start_bot()
    return jsonify({"success": ok, "message": msg, "bot_running": ok})


@app.route("/stop", methods=["GET", "POST"])
def stop_bot_route():
    ok = stop_bot()
    if ok:
        return jsonify({"success": True, "message": "Stop demandé", "bot_running": False})
    return jsonify({"success": False, "message": "Bot non démarré"}), 400


@app.route("/status", methods=["GET"])
def status_route():
    running = bool(bot_instance and bot_instance.running)
    engine = bot_instance.engine if (bot_instance and bot_instance.engine) else None

    payload = {
        "server": "online",
        "timestamp": time.time(),
        "bot": {
            "available": BOT_AVAILABLE,
            "running": running,
            "auto_trading": (engine.auto_trading if engine else False),
            "stats": (engine.stats if engine else {}),
        },
        "config": {
            "auto_buy_amount": Config.AUTO_BUY_AMOUNT,
            "min_age": Config.MIN_AGE_MINUTES,
            "max_age": Config.MAX_AGE_MINUTES,
            "min_liquidity": Config.MIN_LIQUIDITY_USD,
            "min_mcap": Config.MIN_MARKET_CAP_USD,
            "min_volume_24h": Config.MIN_VOLUME_24H_USD,
        }
    }

    # wallet info + balance (via loop du bot)
    if bot_instance and bot_instance.wallet and bot_instance.wallet.address and bot_loop and bot_loop.is_running():
        try:
            bal = submit_coro(bot_instance.wallet.get_balance(), timeout=10)
            payload["wallet"] = {
                "address": bot_instance.wallet.address,
                "balance_sol": bal
            }
        except Exception as e:
            payload["wallet"] = {"error": str(e)}

    return jsonify(payload)


@app.route("/scan", methods=["GET", "POST"])
def scan_route():
    if not bot_instance or not bot_instance.running or not bot_instance.engine:
        return jsonify({"success": False, "error": "Bot non démarré"}), 400

    try:
        tokens = submit_coro(bot_instance.engine.scan_dexscreener(), timeout=20)
        return jsonify({"success": True, "tokens_found": len(tokens), "tokens": tokens[:10]})
    except Exception as e:
        return jsonify({"success": False, "error": str(e)}), 500


@app.route("/toggle-auto", methods=["GET", "POST"])
def toggle_auto_route():
    if not bot_instance or not bot_instance.running or not bot_instance.engine:
        return jsonify({"success": False, "error": "Bot non démarré"}), 400

    try:
        # toggle en mémoire (le vrai état)
        bot_instance.engine.auto_trading = not bot_instance.engine.auto_trading
        # optionnel: sync Config
        Config.AUTO_BUY_ENABLED = bot_instance.engine.auto_trading

        return jsonify({
            "success": True,
            "auto_trading": bot_instance.engine.auto_trading,
            "message": f"Auto-trading {'activé' if bot_instance.engine.auto_trading else 'désactivé'}"
        })
    except Exception as e:
        return jsonify({"success": False, "error": str(e)}), 500


@app.route("/buy", methods=["POST"])
def buy_route():
    if not bot_instance or not bot_instance.running or not bot_instance.engine:
        return jsonify({"success": False, "error": "Bot non démarré"}), 400

    data = request.get_json(silent=True) or {}
    token_address = (data.get("token_address") or "").strip()
    sol_amount = float(data.get("sol_amount") or Config.AUTO_BUY_AMOUNT)

    if not token_address:
        return jsonify({"success": False, "error": "token_address requis"}), 400

    try:
        ok, result = submit_coro(bot_instance.engine.buy_token_real(token_address, sol_amount), timeout=35)
        if ok:
            return jsonify({"success": True, "tx_hash": result})
        return jsonify({"success": False, "error": result}), 400
    except Exception as e:
        return jsonify({"success": False, "error": str(e)}), 500


@app.route("/test-telegram", methods=["GET"])
def test_telegram_route():
    """
    Test immédiat Telegram depuis Render.
    """
    token = Config.TELEGRAM_TOKEN
    chat_id = Config.TELEGRAM_CHAT_ID

    if not token or not chat_id:
        return jsonify({"success": False, "error": "TELEGRAM_TOKEN ou TELEGRAM_CHAT_ID manquant"}), 400

    # On teste via HTTP direct vers Telegram (pas besoin que le bot tourne)
    try:
        import requests  # généralement déjà présent
        url = f"https://api.telegram.org/bot{token}/sendMessage"
        r = requests.post(url, json={"chat_id": chat_id, "text": "✅ Test Telegram depuis Render OK"})
        try:
            data = r.json()
        except Exception:
            data = {"raw": r.text}
        return jsonify({"success": r.ok, "status_code": r.status_code, "telegram": data}), (200 if r.ok else 500)
    except Exception as e:
        return jsonify({"success": False, "error": str(e)}), 500


@app.route("/health", methods=["GET"])
def health():
    return jsonify({"status": "healthy", "timestamp": time.time()}), 200


# ------------------------------------------------------------
# ENTRYPOINT (gunicorn web_server:app)
# ------------------------------------------------------------
if __name__ == "__main__":
    port = int(os.environ.get("PORT", "5000"))
    logger.info(f"🚀 Web server running on 0.0.0.0:{port}")
    app.run(host="0.0.0.0", port=port, debug=False)
