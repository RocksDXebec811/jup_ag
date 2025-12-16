#!/usr/bin/env python3
"""
ROCKET SNIPER BOT - Version complète corrigée
Mode réel 24/7 avec achats manuels/automatiques
"""

# ============================================================
#          CHARGEMENT DES VARIABLES D'ENVIRONNEMENT
# ============================================================
from dotenv import load_dotenv
load_dotenv()

import os
import sys
import json
import time
import asyncio
import atexit
import aiohttp
import logging
import base58
import threading
from datetime import datetime, timedelta
from typing import Dict, List, Optional, Tuple, Any
from flask import Flask, jsonify, request

# ============================================================
#          IMPORTS SOLANA
# ============================================================
try:
    from solders.keypair import Keypair
    from solders.pubkey import Pubkey
    from solders.signature import Signature
    from solders.transaction import VersionedTransaction
    from solana.rpc.async_api import AsyncClient
    from solana.rpc.commitment import Confirmed
    from solana.rpc.types import TokenAccountOpts
    from spl.token.constants import TOKEN_PROGRAM_ID
    from spl.token.instructions import get_associated_token_address
    IMPORT_SUCCESS = True
except ImportError as e:
    print(f"❌ Erreur d'import: {e}")
    print("📦 Installez: pip install solana solders aiohttp python-dotenv spl-token")
    sys.exit(1)

# ============================================================
#          NETTOYAGE DES RESSOURCES
# ============================================================
def cleanup():
    """Nettoyer les ressources à la sortie"""
    print("🔧 Nettoyage des ressources en cours...")

atexit.register(cleanup)

# ============================================================
#          CONFIGURATION
# ============================================================
logging.basicConfig(
    level=logging.INFO,
    format='%(asctime)s - %(levelname)s - %(message)s',
    handlers=[
        logging.StreamHandler(),
        logging.FileHandler('sniper.log')
    ]
)
logger = logging.getLogger(__name__)

class Config:
    # === TELEGRAM ===
    TELEGRAM_TOKEN = os.getenv("TELEGRAM_TOKEN", "").strip()
    TELEGRAM_CHAT_ID = os.getenv("TELEGRAM_CHAT_ID", "").strip()
    
    # === WALLET ===
    WALLET_PRIVATE_KEY = os.getenv("WALLET_PRIVATE_KEY", "").strip()
    RPC_URL = os.getenv("RPC_URL", "https://api.mainnet-beta.solana.com").strip()
    
    # === JUPITER ===
    JUPITER_QUOTE_URL = "https://quote-api.jup.ag/v6/quote"
    JUPITER_SWAP_URL = "https://quote-api.jup.ag/v6/swap"
    
    # === TRADING ===
    AUTO_BUY_AMOUNT = float(os.getenv("AUTO_BUY_AMOUNT", "0.01"))
    AUTO_BUY_ENABLED = os.getenv("AUTO_BUY_ENABLED", "False").lower() == "true"
    SLIPPAGE_BPS = int(os.getenv("SLIPPAGE_BPS", "100"))
    
    # === CRITÈRES ===
    MIN_AGE_MINUTES = 1
    MAX_AGE_MINUTES = 60
    MIN_LIQUIDITY_USD = 50000
    MIN_MARKET_CAP_USD = 1000000
    MIN_VOLUME_24H_USD = 100000
    
    # === PROFIT TARGETS ===
    PROFIT_TARGETS = [
        {"multiplier": 2.0, "sell_percent": 15, "name": "2x"},
        {"multiplier": 5.0, "sell_percent": 20, "name": "5x"},
        {"multiplier": 10.0, "sell_percent": 20, "name": "10x"},
        {"multiplier": 20.0, "sell_percent": 15, "name": "20x"},
        {"multiplier": 50.0, "sell_percent": 15, "name": "50x"},
        {"multiplier": 100.0, "sell_percent": 15, "name": "100x"},
    ]
    
    STOP_LOSS_PCT = -20.0
    TAKE_PROFIT_PCT = 500.0
    
    # === INTERVALLES ===
    SCAN_INTERVAL = 30
    CHECK_INTERVAL = 60
    STATUS_INTERVAL = 300

# ============================================================
#          TELEGRAM SIMPLE
# ============================================================
class TelegramSimple:
    def __init__(self):
        self.token = Config.TELEGRAM_TOKEN
        self.chat_id = Config.TELEGRAM_CHAT_ID
        self.session = None
    
    async def start(self):
        if not self.token or not self.chat_id:
            logger.error("❌ Telegram non configuré")
            return False

        self.session = aiohttp.ClientSession()

        try:
            await self.send_message("🤖 Rocket Sniper Bot - Mode RÉEL démarré")
            logger.info("✅ Telegram initialisé")
            return True
        except Exception as e:
            logger.error(f"❌ Erreur Telegram: {e}")
            return False
    
    async def send_message(self, text: str, use_html: bool = False):
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
            logger.error(f"❌ Erreur Telegram send_message: {e}")
            return False
    
    async def close(self):
        if self.session:
            await self.session.close()

# ============================================================
#          WALLET RÉEL
# ============================================================
class RealWallet:
    def __init__(self, private_key):
        self.private_key = private_key.strip()
        self.keypair = None
        self.address = None
        self.balance = 0
        self.session = None
        self.initialized = False
        self.client = None
        
    async def initialize(self):
        """Initialiser le portefeuille Solana"""
        try:
            import base58
            
            print(f"🔑 Traitement de la clé...")
            print(f"   Clé: {self.private_key[:15]}...{self.private_key[-15:]}")
            
            # Décoder la clé base58
            try:
                decoded = base58.b58decode(self.private_key)
                print(f"   Décodage réussi: {len(decoded)} bytes")
            except Exception as e:
                print(f"❌ Erreur de décodage base58: {e}")
                return False
            
            # Vérifier et créer le keypair selon la longueur
            if len(decoded) == 64:
                self.keypair = Keypair.from_bytes(decoded)
                print("   ✅ Format: Clé Solana 64-byte")
            elif len(decoded) == 32:
                self.keypair = Keypair.from_seed(decoded)
                print("   ✅ Format: Seed Solana 32-byte")
            else:
                print(f"   ⚠ Format non standard ({len(decoded)} bytes)")
                if len(decoded) >= 32:
                    seed = decoded[:32]
                    self.keypair = Keypair.from_seed(seed)
                    print(f"   ✅ Converti en seed 32-byte")
                else:
                    seed = decoded.ljust(32, b'\x00')
                    self.keypair = Keypair.from_seed(seed)
                    print(f"   ⚠ Clé trop courte, complétée avec des zéros")
            
            # Obtenir l'adresse
            self.address = str(self.keypair.pubkey())
            print(f"   📬 Adresse: {self.address}")
            print(f"   🔗 Explorer: https://solscan.io/account/{self.address}")
            
            # Initialiser le client Solana
            self.client = AsyncClient(Config.RPC_URL)
            
            # Initialiser la session HTTP
            self.session = aiohttp.ClientSession()
            
            self.initialized = True
            print("✅ Portefeuille Solana prêt!")
            return True
            
        except Exception as e:
            print(f"❌ Erreur d'initialisation: {e}")
            import traceback
            traceback.print_exc()
            return False
    
    async def get_balance(self):
        """Récupérer le solde SOL réel"""
        if not self.initialized:
            await self.initialize()
        
        try:
            # Utiliser directement le client Solana
            if self.client:
                response = await self.client.get_balance(Pubkey.from_string(self.address))
                if response.value:
                    lamports = response.value
                    self.balance = lamports / 1_000_000_000
                    print(f"💰 Solde: {self.balance} SOL")
                    return self.balance
            
            # Fallback via RPC HTTP
            rpc_urls = [
                Config.RPC_URL,
                "https://api.mainnet-beta.solana.com",
                "https://rpc.ankr.com/solana"
            ]
            
            payload = {
                "jsonrpc": "2.0",
                "id": 1,
                "method": "getBalance",
                "params": [self.address]
            }
            
            for rpc_url in rpc_urls:
                try:
                    async with self.session.post(rpc_url, json=payload, timeout=5) as response:
                        if response.status == 200:
                            data = await response.json()
                            if 'result' in data:
                                lamports = data['result']['value']
                                self.balance = lamports / 1_000_000_000
                                return self.balance
                except:
                    continue
            
            return 0
            
        except Exception as e:
            print(f"⚠ Erreur récupération solde: {e}")
            return 0
    
    async def get_token_balance(self, token_mint: str):
        """Récupérer le solde d'un token SPL"""
        try:
            # Trouver l'ATA (Associated Token Account)
            ata = get_associated_token_address(
                Pubkey.from_string(self.address),
                Pubkey.from_string(token_mint)
            )
            
            # Récupérer le solde du token
            response = await self.client.get_token_account_balance(ata)
            if response.value:
                return float(response.value.amount) / 10**6  # Ajuster selon les decimals
            
            return 0
        except Exception as e:
            # Si le compte n'existe pas, le solde est 0
            return 0
    
    async def close(self):
        """Fermer proprement"""
        if self.session:
            await self.session.close()
        if self.client:
            await self.client.close()
        print("🔒 Session HTTP fermée")

# ============================================================
#          JUPITER CLIENT RÉEL
# ============================================================
class JupiterReal:
    def __init__(self, wallet: RealWallet):
        self.wallet = wallet
        self.quote_url = Config.JUPITER_QUOTE_URL
        self.swap_url = Config.JUPITER_SWAP_URL
    
    async def get_quote(self, input_mint: str, output_mint: str, amount: float):
        """Obtenir un devis de swap"""
        try:
            amount_lamports = int(amount * 1e9)
            
            params = {
                "inputMint": input_mint,
                "outputMint": output_mint,
                "amount": str(amount_lamports),
                "slippageBps": Config.SLIPPAGE_BPS,
                "feeBps": 0
            }
            
            async with aiohttp.ClientSession() as session:
                async with session.get(self.quote_url, params=params, timeout=10) as resp:
                    if resp.status == 200:
                        return await resp.json()
                    else:
                        logger.error(f"❌ Erreur quote: {resp.status}")
                        return None
        except Exception as e:
            logger.error(f"❌ Erreur get_quote: {e}")
            return None
    
    async def execute_swap(self, quote_response: Dict) -> Tuple[Optional[str], str]:
        """Exécuter un swap via Jupiter"""
        try:
            swap_data = {
                "quoteResponse": quote_response,
                "userPublicKey": str(self.wallet.keypair.pubkey()),
                "wrapAndUnwrapSol": True,
                "dynamicComputeUnitLimit": True,
                "prioritizationFeeLamports": {
                    "priorityLevelWithMaxLamports": {
                        "maxLamports": 1_000_000,
                        "priorityLevel": "veryHigh"
                    }
                }
            }
            
            async with aiohttp.ClientSession() as session:
                async with session.post(self.swap_url, json=swap_data, timeout=15) as resp:
                    if resp.status != 200:
                        error_text = await resp.text()
                        return None, f"API error: {resp.status} - {error_text}"
                    
                    swap_result = await resp.json()
                    raw_tx = swap_result.get('swapTransaction')
                    
                    if not raw_tx:
                        return None, "No transaction in response"
                    
                    try:
                        tx_bytes = base58.b58decode(raw_tx)
                    except:
                        try:
                            import base64
                            tx_bytes = base64.b64decode(raw_tx)
                        except:
                            return None, "Failed to decode transaction"
                    
                    transaction = VersionedTransaction.from_bytes(tx_bytes)
                    transaction.sign([self.wallet.keypair])
                    
                    raw_transaction = bytes(transaction)
                    tx_hash = await self.wallet.client.send_raw_transaction(
                        raw_transaction,
                        opts={"skip_preflight": False, "preflight_commitment": "confirmed"}
                    )
                    
                    if tx_hash.value:
                        tx_sig = str(tx_hash.value)
                        logger.info(f"✅ Transaction envoyée: {tx_sig}")
                        
                        # Attendre la confirmation
                        await asyncio.sleep(2)
                        confirmation = await self.wallet.client.confirm_transaction(
                            Signature.from_string(tx_sig),
                            commitment=Confirmed
                        )
                        
                        if confirmation.value[0]:
                            return tx_sig, "Success"
                        else:
                            return tx_sig, "Transaction may have failed"
                    else:
                        return None, "Failed to send transaction"
                        
        except Exception as e:
            logger.error(f"❌ Erreur execute_swap: {e}")
            return None, str(e)
    
    async def swap_sol_to_token(self, token_mint: str, sol_amount: float):
        """Swap SOL vers token"""
        try:
            quote = await self.get_quote(
                input_mint="So11111111111111111111111111111111111111112",
                output_mint=token_mint,
                amount=sol_amount
            )
            
            if not quote:
                return None, "Failed to get quote"
            
            return await self.execute_swap(quote)
        except Exception as e:
            logger.error(f"❌ Erreur swap_sol_to_token: {e}")
            return None, str(e)
    
    async def swap_token_to_sol(self, token_mint: str, token_amount: float):
        """Swap token vers SOL"""
        try:
            quote = await self.get_quote(
                input_mint=token_mint,
                output_mint="So11111111111111111111111111111111111111112",
                amount=token_amount
            )
            
            if not quote:
                return None, "Failed to get quote"
            
            return await self.execute_swap(quote)
        except Exception as e:
            logger.error(f"❌ Erreur swap_token_to_sol: {e}")
            return None, str(e)

# ============================================================
#          TRADING ENGINE RÉEL
# ============================================================
class RealTradingEngine:
    def __init__(self, wallet: RealWallet, telegram: TelegramSimple):
        self.wallet = wallet
        self.telegram = telegram
        self.jupiter = JupiterReal(wallet)
        self.active_trades: Dict[str, Dict] = {}
        self.auto_trading = Config.AUTO_BUY_ENABLED
        self.start_time = datetime.now()
        self.recent_tokens = set()
        
        self.stats = {
            "total_buys": 0,
            "total_sells": 0,
            "sol_spent": 0.0,
            "sol_earned": 0.0,
            "total_profit": 0.0
        }
    
    async def get_token_info(self, token_address: str):
        """Obtenir les infos d'un token depuis DexScreener"""
        try:
            url = f"https://api.dexscreener.com/latest/dex/tokens/{token_address}"
            async with aiohttp.ClientSession() as session:
                async with session.get(url, timeout=5) as resp:
                    if resp.status == 200:
                        data = await resp.json()
                        if data.get("pairs"):
                            return data["pairs"][0]
            return None
        except Exception as e:
            logger.error(f"❌ Erreur get_token_info: {e}")
            return None
    
    async def check_token_criteria(self, token_address: str):
        """Vérifier si un token respecte les critères"""
        try:
            info = await self.get_token_info(token_address)
            if not info:
                return False, "No DexScreener data"
            
            created_at = info.get("pairCreatedAt") or info.get("createdAt")
            if created_at:
                now_ms = int(time.time() * 1000)
                age_min = (now_ms - int(created_at)) / 60000
                
                if age_min < Config.MIN_AGE_MINUTES:
                    return False, f"Too young ({age_min:.1f} min)"
                if age_min > Config.MAX_AGE_MINUTES:
                    return False, f"Too old ({age_min:.1f} min)"
            
            liquidity = float((info.get("liquidity") or {}).get("usd") or 0)
            if liquidity < Config.MIN_LIQUIDITY_USD:
                return False, f"Low liquidity (${liquidity:,.0f})"
            
            mcap = float(info.get("fdv") or info.get("marketCap") or 0)
            if mcap < Config.MIN_MARKET_CAP_USD:
                return False, f"Low MCAP (${mcap:,.0f})"
            
            volume = float((info.get("volume") or {}).get("h24") or 0)
            if volume < Config.MIN_VOLUME_24H_USD:
                return False, f"Low volume (${volume:,.0f})"
            
            if token_address in self.recent_tokens:
                return False, "Recently processed"
            
            return True, "All criteria passed"
        except Exception as e:
            return False, f"Error: {str(e)[:100]}"
    
    async def buy_token_real(self, token_address: str, sol_amount: float, source: str = "manual"):
        """Acheter un token réellement"""
        try:
            logger.info(f"🛒 ACHAT RÉEL: {sol_amount} SOL -> {token_address[:16]}...")
            
            balance = await self.wallet.get_balance()
            if balance < sol_amount:
                return False, f"Insufficient balance: {balance:.4f} SOL"
            
            if token_address in self.active_trades:
                return False, "Already in position"
            
            if source == "auto":
                ok, reason = await self.check_token_criteria(token_address)
                if not ok:
                    await self.telegram.send_message(
                        f"⛔ AUTO-BUY BLOCKED\nToken: {token_address[:16]}...\nReason: {reason}",
                        use_html=False
                    )
                    return False, reason
            
            self.recent_tokens.add(token_address)
            if len(self.recent_tokens) > 100:
                self.recent_tokens = set(list(self.recent_tokens)[-50:])
            
            tx_hash, message = await self.jupiter.swap_sol_to_token(token_address, sol_amount)
            
            if not tx_hash:
                return False, f"Swap failed: {message}"
            
            token_info = await self.get_token_info(token_address)
            symbol = token_info.get('baseToken', {}).get('symbol', 'UNKNOWN') if token_info else 'UNKNOWN'
            price = float(token_info.get('priceUsd', 0) or 0) if token_info else 0.0
            liquidity = float((token_info.get('liquidity') or {}).get('usd') or 0) if token_info else 0
            
            await asyncio.sleep(3)
            token_amount = await self.wallet.get_token_balance(token_address)
            
            self.active_trades[token_address] = {
                "symbol": symbol,
                "buy_sol": sol_amount,
                "buy_price": price,
                "buy_time": datetime.now(),
                "buy_tx": tx_hash,
                "token_amount": token_amount,
                "source": source,
                "sell_targets": []
            }
            
            self.stats["total_buys"] += 1
            self.stats["sol_spent"] += sol_amount
            
            msg = f"""
✅ ACHAT RÉEL RÉUSSI!

🪙 Token: {symbol} ({token_address[:16]}...)
💰 Montant: {sol_amount} SOL
🧮 Tokens reçus: {token_amount:,.0f}
💲 Prix: ${price:.10f}
📊 Liquidité: ${liquidity:,.0f}

🔗 TX: https://solscan.io/tx/{tx_hash}
⚠️ TRANSACTION RÉELLE EXÉCUTÉE!
"""
            await self.telegram.send_message(msg)
            
            logger.info(f"✅ Achat réel enregistré: {symbol}")
            return True, tx_hash
        except Exception as e:
            logger.error(f"❌ Erreur buy_token_real: {e}")
            return False, str(e)
    
    async def sell_token_real(self, token_address: str, percent: float = 100.0, reason: str = "manual"):
        """Vendre un token réellement"""
        try:
            if token_address not in self.active_trades:
                return False, "Token not owned"
            
            trade = self.active_trades[token_address]
            logger.info(f"🎯 VENTE RÉELLE: {percent}% de {token_address[:16]}...")
            
            token_balance = await self.wallet.get_token_balance(token_address)
            if token_balance <= 0:
                return False, "No token balance"
            
            sell_amount = token_balance * (percent / 100.0)
            tx_hash, message = await self.jupiter.swap_token_to_sol(token_address, sell_amount)
            
            if not tx_hash:
                return False, f"Sell failed: {message}"
            
            token_info = await self.get_token_info(token_address)
            current_price = float(token_info.get('priceUsd', 0)) if token_info else 0
            buy_price = trade.get('buy_price', 0)
            
            profit_pct = ((current_price - buy_price) / buy_price * 100) if buy_price > 0 else 0
            profit_sol = (trade['buy_sol'] * (percent / 100) * profit_pct) / 100
            
            trade["sell_targets"].append({
                "percent": percent,
                "time": datetime.now(),
                "tx": tx_hash,
                "profit_pct": profit_pct,
                "profit_sol": profit_sol,
                "reason": reason
            })
            
            if percent >= 100:
                del self.active_trades[token_address]
            
            self.stats["total_sells"] += 1
            self.stats["sol_earned"] += trade['buy_sol'] * (percent / 100) + profit_sol
            self.stats["total_profit"] += profit_sol
            
            msg = f"""
✅ VENTE RÉELLE RÉUSSIE!

🪙 Token: {trade['symbol']} ({token_address[:16]}...)
📊 Pourcentage: {percent}%
💰 Tokens vendus: {sell_amount:,.0f}
📈 Profit: {profit_pct:+.2f}% ({profit_sol:+.4f} SOL)
📝 Raison: {reason}

🔗 TX: https://solscan.io/tx/{tx_hash}
"""
            await self.telegram.send_message(msg)
            
            logger.info(f"✅ Vente réel enregistrée: {percent}% vendu")
            return True, tx_hash
        except Exception as e:
            logger.error(f"❌ Erreur sell_token_real: {e}")
            return False, str(e)
    
    async def check_profits(self):
        """Vérifier et exécuter les prises de bénéfices"""
        for token_address, trade in list(self.active_trades.items()):
            try:
                token_info = await self.get_token_info(token_address)
                if not token_info:
                    continue
                
                current_price = float(token_info.get('priceUsd', 0))
                buy_price = trade.get('buy_price', 0)
                
                if buy_price <= 0 or current_price <= 0:
                    continue
                
                profit_pct = ((current_price - buy_price) / buy_price) * 100
                
                if profit_pct <= Config.STOP_LOSS_PCT:
                    logger.warning(f"⚠️ Stop-loss triggered: {profit_pct:.1f}%")
                    await self.sell_token_real(token_address, 100, "stop-loss")
                    continue
                
                for target in Config.PROFIT_TARGETS:
                    target_pct = (target["multiplier"] - 1) * 100
                    if profit_pct >= target_pct:
                        already_sold = any(
                            sell.get("reason", "").startswith(f"take-profit {target['name']}")
                            for sell in trade.get("sell_targets", [])
                        )
                        
                        if not already_sold:
                            logger.info(f"🎯 Take-profit {target['name']}: {profit_pct:.1f}%")
                            await self.sell_token_real(
                                token_address,
                                target["sell_percent"],
                                f"take-profit {target['name']}"
                            )
                            break
            except Exception as e:
                logger.error(f"❌ Erreur check_profits: {e}")
    
    async def scan_dexscreener(self):
        """Scanner DexScreener pour de nouveaux tokens"""
        try:
            logger.info("🔍 Scan DexScreener en cours...")
            
            url = "https://api.dexscreener.com/latest/dex/pairs/solana"
            async with aiohttp.ClientSession() as session:
                async with session.get(url, timeout=10) as resp:
                    if resp.status != 200:
                        return []
                    data = await resp.json()
            
            pairs = data.get("pairs", [])
            tokens_found = []
            
            for pair in pairs[:50]:
                try:
                    token_address = pair.get("baseToken", {}).get("address")
                    if not token_address:
                        continue
                    
                    ok, reason = await self.check_token_criteria(token_address)
                    if not ok:
                        continue
                    
                    symbol = pair.get("baseToken", {}).get("symbol", "UNKNOWN")
                    price = pair.get("priceUsd", "N/A")
                    liquidity = float((pair.get("liquidity") or {}).get("usd") or 0)
                    volume = float((pair.get("volume") or {}).get("h24") or 0)
                    mcap = float(pair.get("fdv") or 0)
                    created_at = pair.get("pairCreatedAt", 0)
                    
                    age_min = (int(time.time() * 1000) - created_at) / 60000 if created_at else 0
                    
                    token_data = {
                        "address": token_address,
                        "symbol": symbol,
                        "price": price,
                        "liquidity": liquidity,
                        "volume": volume,
                        "mcap": mcap,
                        "age_min": age_min,
                        "pair_url": pair.get("url", "")
                    }
                    
                    tokens_found.append(token_data)
                    
                    await self.telegram.send_message(
                        f"🎯 TOKEN FILTRÉ TROUVÉ\n\n"
                        f"💰 {symbol} ({token_address[:16]}...)\n"
                        f"⏱️ Âge: {age_min:.1f} min\n"
                        f"💧 Liquidité: ${liquidity:,.0f}\n"
                        f"🏦 MCAP: ${mcap:,.0f}\n"
                        f"📈 Volume: ${volume:,.0f}\n"
                        f"💰 Prix: ${price}\n\n"
                        f"Auto-trading: {'✅ ON' if self.auto_trading else '❌ OFF'}"
                    )
                except Exception as e:
                    logger.error(f"❌ Erreur traitement paire: {e}")
            
            return tokens_found
        except Exception as e:
            logger.error(f"❌ Erreur scan_dexscreener: {e}")
            return []

# ============================================================
#          BOT PRINCIPAL RÉEL
# ============================================================
class RealSniperBot:
    def __init__(self):
        # IMPORTANT: Déplacez votre clé dans le fichier .env !
        self.private_key = os.getenv("WALLET_PRIVATE_KEY", "").strip()
        
        if not self.private_key:
            print("❌ ERREUR: Aucune clé privée fournie")
            print("   Définissez WALLET_PRIVATE_KEY dans le fichier .env")
            sys.exit(1)
        
        print(f"🚀 Initialisation du bot Solana...")
        print(f"   Clé: {self.private_key[:10]}...{self.private_key[-10:]}")
        
        self.wallet = RealWallet(private_key=self.private_key)
        self.telegram = None
        self.engine = None
        self.running = False
    
    async def start(self):
        """Démarrer le bot avec la surveillance RÉELLE"""
        try:
            print("\n" + "="*50)
            print("🚀 DÉMARRAGE DU SNIPER BOT SOLANA")
            print("="*50)
            
            # Initialiser le portefeuille
            if not await self.wallet.initialize():
                print("❌ Impossible d'initialiser le portefeuille")
                return False
            
            # Initialiser Telegram
            self.telegram = TelegramSimple()
            if not await self.telegram.start():
                print("⚠ Telegram non configuré, continuation sans notifications")
            
            # Créer le moteur de trading RÉEL
            self.engine = RealTradingEngine(self.wallet, self.telegram)
            
            # Démarrer la boucle de trading RÉELLE
            await self.run()
            
            return True
            
        except KeyboardInterrupt:
            print("\n⏹ Arrêt demandé par l'utilisateur")
            return False
        except Exception as e:
            print(f"❌ Erreur fatale au démarrage: {e}")
            import traceback
            traceback.print_exc()
            return False
        finally:
            await self.stop()
    
    async def run(self):
        """Boucle principale de trading RÉELLE"""
        if not self.engine:
            return
        
        balance = await self.wallet.get_balance()
        
        # Afficher les paliers de profit
        profit_lines = []
        for target in Config.PROFIT_TARGETS[:3]:
            profit_lines.append(f"• Take-profit: {target['multiplier']}x ({target['sell_percent']}%)")
        profit_text = "\n".join(profit_lines)
        
        startup_msg = f"""
🚀 ROCKET SNIPER - MODE RÉEL

👛 Wallet: {self.wallet.address[:16]}...
💰 Solde: {balance:.4f} SOL
🤖 Auto-trading: {'✅ ON' if self.engine.auto_trading else '❌ OFF'}

⚠️ ATTENTION: TRANSACTIONS RÉELLES!
Chaque trade exécutera une transaction sur Solana.

Critères:
• Âge: {Config.MIN_AGE_MINUTES}-{Config.MAX_AGE_MINUTES} min
• Liquidité: ≥${Config.MIN_LIQUIDITY_USD:,}
• MCAP: ≥${Config.MIN_MARKET_CAP_USD:,}
• Volume: ≥${Config.MIN_VOLUME_24H_USD:,}

Paliers de profit:
• Stop-loss: {Config.STOP_LOSS_PCT}%
{profit_text}
"""
        await self.telegram.send_message(startup_msg)
        
        print("\n📱 Bot en marche! Vérifiez Telegram pour les alertes.")
        print("🔍 Scan automatique activé...")
        
        last_scan = 0
        last_profit_check = 0
        last_status = time.time()
        
        self.running = True
        
        try:
            while self.running:
                current_time = time.time()
                
                # Scanner DexScreener
                if current_time - last_scan >= Config.SCAN_INTERVAL:
                    tokens = await self.engine.scan_dexscreener()
                    
                    # Achat automatique si activé
                    if self.engine.auto_trading and tokens:
                        for token in tokens:
                            success, tx = await self.engine.buy_token_real(
                                token["address"],
                                Config.AUTO_BUY_AMOUNT,
                                source="auto"
                            )
                            if success:
                                break
                    
                    last_scan = current_time
                
                # Vérifier les profits
                if current_time - last_profit_check >= Config.CHECK_INTERVAL:
                    if self.engine.active_trades:
                        await self.engine.check_profits()
                    last_profit_check = current_time
                
                # Envoyer le statut périodique
                if current_time - last_status >= Config.STATUS_INTERVAL:
                    balance = await self.wallet.get_balance()
                    uptime = datetime.now() - self.engine.start_time
                    
                    status_msg = f"""
📊 STATUT PÉRIODIQUE

💰 SOL: {balance:.4f}
📈 Trades actifs: {len(self.engine.active_trades)}
📊 Profit total: {self.engine.stats['total_profit']:+.4f} SOL
⏰ Uptime: {uptime.days}j {uptime.seconds//3600}h
🛒 Achats: {self.engine.stats['total_buys']}
🎯 Ventes: {self.engine.stats['total_sells']}

⚠️ MODE RÉEL ACTIF
"""
                    await self.telegram.send_message(status_msg)
                    last_status = current_time
                
                await asyncio.sleep(1)
                
        except KeyboardInterrupt:
            print("\n🛑 Arrêt demandé...")
        except Exception as e:
            logger.error(f"❌ Erreur boucle principale: {e}")
            await self.telegram.send_message(f"❌ Erreur: {str(e)[:200]}")
        finally:
            self.running = False
    
    async def stop(self):
        """Arrêter proprement"""
        self.running = False
        print("\n🛑 Arrêt du bot en cours...")
        
        if self.telegram:
            await self.telegram.send_message("👋 Bot arrêté")
            await self.telegram.close()
        
        if self.wallet:
            await self.wallet.close()
        
        print("✅ Bot arrêté proprement")

# ============================================================
#          APPLICATION FLASK POUR RENDER
# ============================================================
app = Flask(__name__)

# Variables globales pour gérer le bot
bot_instance = None
bot_thread = None
bot_loop = None
BOT_AVAILABLE = True

def run_bot_in_background():
    global bot_instance, bot_loop
    try:
        bot_loop = asyncio.new_event_loop()
        asyncio.set_event_loop(bot_loop)
        bot_instance = RealSniperBot()
        bot_loop.run_until_complete(bot_instance.start())
    except Exception as e:
        logging.error(f"❌ Erreur dans le bot: {e}")
    finally:
        if bot_loop and not bot_loop.is_closed():
            bot_loop.close()

def stop_bot():
    global bot_instance, bot_thread, bot_loop
    if bot_instance and bot_instance.running:
        bot_instance.running = False
        if bot_thread and bot_thread.is_alive():
            bot_thread.join(timeout=10)
        if bot_loop and not bot_loop.is_closed():
            bot_loop.close()
        return True
    return False

def start_bot():
    global bot_thread
    if bot_thread and bot_thread.is_alive():
        return False, "Bot déjà en cours d'exécution"
    
    stop_bot()
    bot_thread = threading.Thread(target=run_bot_in_background, daemon=True)
    bot_thread.start()
    time.sleep(3)
    return True, "Bot démarré avec succès"

# ============================================================
#          ROUTES FLASK
# ============================================================
@app.route("/")
def home():
    status = {
        "status": "online",
        "service": "Rocket Sniper Bot API",
        "bot_running": bot_instance.running if bot_instance else False,
        "bot_available": BOT_AVAILABLE,
        "endpoints": [
            "/ - Cette page",
            "/start - Démarrer le bot",
            "/stop - Arrêter le bot",
            "/status - Statut complet",
            "/scan - Scanner manuellement",
            "/toggle-auto - Activer/désactiver auto-trading",
            "/buy - Acheter un token (POST)",
            "/health - Vérifier la santé"
        ]
    }
    return jsonify(status)

@app.route("/start", methods=["POST", "GET"])
def start_bot_route():
    if not BOT_AVAILABLE:
        return jsonify({"error": "Bot non disponible."}), 500
    
    success, message = start_bot()
    if success:
        return jsonify({"success": True, "message": message, "bot_running": True})
    else:
        return jsonify({"success": False, "message": message, "bot_running": False}), 400

@app.route("/stop", methods=["POST", "GET"])
def stop_bot_route():
    if stop_bot():
        return jsonify({"success": True, "message": "Bot arrêté", "bot_running": False})
    else:
        return jsonify({"success": False, "message": "Bot non démarré ou déjà arrêté", "bot_running": False})

@app.route("/status")
def bot_status():
    status = {
        "server": "online",
        "timestamp": time.time(),
        "bot": {
            "available": BOT_AVAILABLE,
            "running": bot_instance.running if bot_instance else False,
            "auto_trading": Config.AUTO_BUY_ENABLED,
            "stats": bot_instance.engine.stats if bot_instance and bot_instance.engine else {}
        },
        "config": {
            "min_age": Config.MIN_AGE_MINUTES,
            "min_liquidity": Config.MIN_LIQUIDITY_USD,
            "min_mcap": Config.MIN_MARKET_CAP_USD,
            "auto_buy_amount": Config.AUTO_BUY_AMOUNT
        }
    }
    
    if bot_instance and hasattr(bot_instance, 'wallet'):
        try:
            loop = asyncio.new_event_loop()
            balance = loop.run_until_complete(bot_instance.wallet.get_balance())
            loop.close()
            status["wallet"] = {
                "address": bot_instance.wallet.address[:16] + "..." if bot_instance.wallet.address else "N/A",
                "balance_sol": balance
            }
        except:
            status["wallet"] = {"info": "Non disponible"}
    
    return jsonify(status)

@app.route("/scan", methods=["POST", "GET"])
def manual_scan():
    if not bot_instance or not bot_instance.running:
        return jsonify({"error": "Bot non démarré"}), 400
    
    try:
        if hasattr(bot_instance, 'engine') and hasattr(bot_instance.engine, 'scan_dexscreener'):
            loop = asyncio.new_event_loop()
            asyncio.set_event_loop(loop)
            tokens_found = loop.run_until_complete(bot_instance.engine.scan_dexscreener())
            loop.close()
            return jsonify({
                "success": True,
                "tokens_found": len(tokens_found),
                "tokens": tokens_found[:5]
            })
        else:
            return jsonify({"success": False, "message": "Fonction scan non disponible"}), 400
    except Exception as e:
        return jsonify({"success": False, "error": str(e)}), 500

@app.route("/toggle-auto", methods=["POST"])
def toggle_auto_trading():
    if not bot_instance:
        return jsonify({"error": "Bot non démarré"}), 400
    
    try:
        if hasattr(bot_instance, 'engine'):
            bot_instance.engine.auto_trading = not bot_instance.engine.auto_trading
            Config.AUTO_BUY_ENABLED = bot_instance.engine.auto_trading
            return jsonify({
                "success": True,
                "auto_trading": bot_instance.engine.auto_trading,
                "message": f"Auto-trading {'activé' if bot_instance.engine.auto_trading else 'désactivé'}"
            })
        else:
            return jsonify({"success": False, "message": "Engine non disponible"}), 400
    except Exception as e:
        return jsonify({"success": False, "error": str(e)}), 500

@app.route("/buy", methods=["POST"])
def buy_token():
    if not bot_instance or not bot_instance.running:
        return jsonify({"error": "Bot non démarré"}), 400
    
    data = request.get_json()
    if not data or 'token_address' not in data:
        return jsonify({"error": "token_address requis"}), 400
    
    token_address = data['token_address']
    sol_amount = data.get('sol_amount', Config.AUTO_BUY_AMOUNT)
    
    try:
        if hasattr(bot_instance, 'engine') and hasattr(bot_instance.engine, 'buy_token_real'):
            loop = asyncio.new_event_loop()
            asyncio.set_event_loop(loop)
            success, result = loop.run_until_complete(
                bot_instance.engine.buy_token_real(token_address, sol_amount, source="api")
            )
            loop.close()
            
            if success:
                return jsonify({"success": True, "tx_hash": result, "message": "Achat réussi"})
            else:
                return jsonify({"success": False, "error": result}), 400
        else:
            return jsonify({"success": False, "message": "Fonction d'achat non disponible"}), 400
    except Exception as e:
        return jsonify({"success": False, "error": str(e)}), 500

@app.route("/health")
def health_check():
    return jsonify({
        "status": "healthy",
        "timestamp": time.time(),
        "service": "Rocket Sniper Bot"
    }), 200

# ============================================================
#          DÉMARRAGE
# ============================================================
async def main_async():
    """Fonction principale asynchrone"""
    bot = RealSniperBot()
    try:
        await bot.start()
    except KeyboardInterrupt:
        print("\nArrêt demandé par l'utilisateur")
        await bot.stop()
    except Exception as e:
        print(f"Erreur fatale: {e}")
        await bot.stop()
    finally:
        print("Main async terminé")

def main_console():
    """Point d'entrée pour la console"""
    try:
        loop = asyncio.new_event_loop()
        asyncio.set_event_loop(loop)
        loop.run_until_complete(main_async())
    except KeyboardInterrupt:
        print("\nProgramme interrompu")
    except Exception as e:
        print(f"Erreur dans main_console: {e}")
    finally:
        print("Programme terminé.")

def main_web():
    """Mode web pour Render"""
    port = int(os.environ.get("PORT", 5000))
    print(f"🚀 Serveur Rocket Sniper Bot démarré sur le port {port}")
    print("📡 Endpoints disponibles:")
    print("   / - Page d'accueil avec tous les endpoints")
    print("   /start - Démarrer le bot")
    print("   /stop - Arrêter le bot")
    print("   /status - Statut complet")
    print("   /scan - Scanner manuellement")
    print("   /toggle-auto - Activer/désactiver auto-trading")
    print("   /buy - Acheter un token (POST)")
    print("   /health - Vérifier la santé")
    app.run(host="0.0.0.0", port=port, debug=False)

if __name__ == "__main__":
    # Détection automatique du mode
    if os.environ.get("PORT") or "--web" in sys.argv:
        main_web()
    else:
        main_console()
