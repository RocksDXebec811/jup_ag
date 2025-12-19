#!/usr/bin/env python3
# -*- coding: utf-8 -*-

"""
Rocket Sniper Bot - Web Server (Render)
Version complète avec support Birdeye, Helius et DexScreener
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
# Import de ton bot
# =========================================
try:
    # Importe depuis rocket_sniper.py
    from rocket_sniper import RealSniperBot, Config
except Exception as e:
    logger.error(f"❌ Impossible d'importer RealSniperBot: {e}")
    # Fallback: crée des classes vides pour le test
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
# Globals Bot runtime
# =========================================
BOT_AVAILABLE = True
bot_instance: Optional[RealSniperBot] = None
bot_thread: Optional[threading.Thread] = None
bot_loop: Optional[asyncio.AbstractEventLoop] = None
state_lock = threading.Lock()

# =========================================
# Helper functions
# =========================================
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
    """Stop propre."""
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
            "/start (GET/POST)",
            "/stop (GET/POST)",
            "/status (GET)",
            "/scan (GET)  + ?debug=1 ou ?raw=1",
            "/toggle-auto (GET/POST)",
            "/health (GET)",
            "/debug_fetch (GET)",
            "/debug_fetch_new (GET)",
            "/test_scan_simple (GET)",
            "/scan_direct (GET)",
            "/scan_with_filters (GET)",
            "/test_raydium_api (GET)",
            "/debug_bot_state (GET)",
            "/scan_raydium_birdeye (GET)",
            "/scan_helius (GET)",
            "/scan_new_tokens (GET)",
            "/test_birdeye (GET)",
            "/update_config (POST)"
        ]
    })

@app.route("/health", methods=["GET"])
def health():
    return jsonify({"status": "healthy", "timestamp": time.time()}), 200

# =========================================
# Routes de debug et test
# =========================================
@app.route('/debug_fetch')
def debug_fetch():
    """Scanner Raydium via DexScreener"""
    import urllib.request
    import json as json_module
    import time
    
    try:
        print("[DEBUG] Scanning Raydium pairs...", flush=True)
        url = "https://api.dexscreener.com/latest/dex/search?q=raydium&limit=50"
        
        print(f"[DEBUG] Calling: {url}", flush=True)
        start = time.time()
        
        req = urllib.request.Request(
            url,
            headers={
                "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36",
                "Accept": "application/json",
                "Referer": "https://dexscreener.com/"
            }
        )
        
        with urllib.request.urlopen(req, timeout=20) as response:
            elapsed = time.time() - start
            data = json_module.loads(response.read().decode('utf-8'))
            pairs = data.get("pairs", [])
            
            print(f"[DEBUG] Found {len(pairs)} Raydium pairs", flush=True)
            
            sample = []
            for pair in pairs[:10]:
                created_at = pair.get('pairCreatedAt', 0)
                age_seconds = int(time.time() - (created_at / 1000)) if created_at else 0
                
                sample.append({
                    'symbol': pair.get('baseToken', {}).get('symbol', '?'),
                    'pairAddress': pair.get('pairAddress', '?')[:15] + '...',
                    'liquidity_usd': pair.get('liquidity', {}).get('usd', 0),
                    'price': pair.get('priceUsd', 0),
                    'volume_24h': pair.get('volume', {}).get('h24', 0),
                    'age_seconds': age_seconds,
                    'age_minutes': round(age_seconds / 60, 1) if age_seconds > 0 else 0,
                    'dex': pair.get('dexId', '?')
                })
            
            return jsonify({
                'success': True,
                'source': 'Raydium via DexScreener',
                'status': response.status,
                'total_pairs': len(pairs),
                'sample': sample,
                'hint': 'These are Raydium pairs. Adjust filters for new tokens.'
            })
            
    except Exception as e:
        print(f"[DEBUG ERROR] {str(e)}", flush=True)
        return jsonify({
            'success': False,
            'error': str(e),
            'hint': 'Raydium scan failed.'
        })

@app.route('/debug_fetch_new')
def debug_fetch_new():
    """Scanner les NOUVELLES paires Raydium (moins de 60 minutes)"""
    import urllib.request
    import json as json_module
    import time
    
    try:
        print("[DEBUG] Scanning NEW Raydium pairs...", flush=True)
        
        url = "https://api.dexscreener.com/latest/dex/search?q=raydium&limit=100"
        
        print(f"[DEBUG] Calling: {url}", flush=True)
        start = time.time()
        
        req = urllib.request.Request(
            url,
            headers={
                "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36",
                "Accept": "application/json",
                "Referer": "https://dexscreener.com/"
            }
        )
        
        with urllib.request.urlopen(req, timeout=20) as response:
            elapsed = time.time() - start
            data = json_module.loads(response.read().decode('utf-8'))
            pairs = data.get("pairs", [])
            
            print(f"[DEBUG] Found {len(pairs)} Raydium pairs total", flush=True)
            
            current_time = time.time()
            new_pairs = []
            
            for pair in pairs:
                created_at = pair.get('pairCreatedAt', 0)
                if not created_at:
                    continue
                    
                age_seconds = current_time - (created_at / 1000)
                age_minutes = age_seconds / 60
                
                if age_minutes <= 60:
                    new_pairs.append(pair)
            
            print(f"[DEBUG] Found {len(new_pairs)} Raydium pairs <60min", flush=True)
            
            sample = []
            for pair in new_pairs[:10]:
                created_at = pair.get('pairCreatedAt', 0)
                age_seconds = current_time - (created_at / 1000)
                age_minutes = age_seconds / 60
                
                sample.append({
                    'symbol': pair.get('baseToken', {}).get('symbol', '?'),
                    'pairAddress': pair.get('pairAddress', '?')[:15] + '...',
                    'liquidity_usd': pair.get('liquidity', {}).get('usd', 0),
                    'price': pair.get('priceUsd', 0),
                    'volume_24h': pair.get('volume', {}).get('h24', 0),
                    'age_seconds': int(age_seconds),
                    'age_minutes': round(age_minutes, 2),
                    'dex': pair.get('dexId', '?')
                })
            
            return jsonify({
                'success': True,
                'source': 'Raydium pairs <60min',
                'status': response.status,
                'total_pairs': len(pairs),
                'new_pairs': len(new_pairs),
                'sample': sample,
                'hint': 'These are Raydium pairs less than 60 minutes old.'
            })
            
    except Exception as e:
        print(f"[DEBUG ERROR] {str(e)}", flush=True)
        return jsonify({
            'success': False,
            'error': str(e),
            'hint': 'Failed to scan new pairs.'
        })

@app.route('/test_scan_simple')
def test_scan_simple():
    """Test simple du scanner"""
    import urllib.request
    import json as json_module
    import time
    
    try:
        url = "https://api.dexscreener.com/latest/dex/search?q=raydium&limit=50"
        
        print(f"[TEST SCAN] Calling {url}", flush=True)
        start = time.time()
        
        req = urllib.request.Request(
            url,
            headers={"User-Agent": "Mozilla/5.0"}
        )
        
        with urllib.request.urlopen(req, timeout=15) as response:
            elapsed = time.time() - start
            data = json_module.loads(response.read().decode('utf-8'))
            pairs = data.get("pairs", [])
            
            print(f"[TEST SCAN] Got {len(pairs)} pairs in {elapsed:.2f}s", flush=True)
            
            return jsonify({
                "success": True,
                "status": response.status,
                "pairs_found": len(pairs),
                "first_pair_symbol": pairs[0].get("baseToken", {}).get("symbol", "none") if pairs else "none",
                "fetch_time": f"{elapsed:.2f}s"
            })
            
    except Exception as e:
        print(f"[TEST SCAN ERROR] {str(e)}", flush=True)
        return jsonify({
            "success": False,
            "error": str(e)
        })

@app.route('/scan_direct')
def scan_direct():
    """Scan direct sans vérifier l'état du bot"""
    import urllib.request
    import json as json_module
    import time
    
    try:
        print("[SCAN DIRECT] Starting direct scan...", flush=True)
        
        url = "https://api.dexscreener.com/latest/dex/search?q=raydium&limit=100"
        
        req = urllib.request.Request(
            url,
            headers={"User-Agent": "Mozilla/5.0"}
        )
        
        with urllib.request.urlopen(req, timeout=15) as response:
            data = json_module.loads(response.read().decode('utf-8'))
            all_pairs = data.get("pairs", [])
            
            print(f"[SCAN DIRECT] Got {len(all_pairs)} total pairs", flush=True)
            
            current_time = time.time()
            filtered_tokens = []
            
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
                
                if (age_minutes >= 1 and age_minutes <= 60 and
                    liquidity >= 5000 and
                    volume_24h >= 1000):
                    
                    base_token = pair.get('baseToken', {})
                    
                    filtered_tokens.append({
                        'address': base_token.get('address', ''),
                        'symbol': base_token.get('symbol', ''),
                        'name': base_token.get('name', ''),
                        'liquidity': liquidity,
                        'price': pair.get('priceUsd', 0),
                        'volume_24h': volume_24h,
                        'age_minutes': round(age_minutes, 2),
                        'pair_address': pair.get('pairAddress', ''),
                        'url': pair.get('url', '')
                    })
            
            print(f"[SCAN DIRECT] Found {len(filtered_tokens)} matching tokens", flush=True)
            
            return jsonify({
                "success": True,
                "tokens": filtered_tokens[:20],
                "tokens_found": len(filtered_tokens),
                "debug": {
                    "source": "DexScreener Raydium search",
                    "total_pairs": len(all_pairs),
                    "filters": {
                        "age_min": 1,
                        "age_max": 60,
                        "min_liquidity": 5000,
                        "min_volume": 1000
                    }
                }
            })
            
    except Exception as e:
        print(f"[SCAN DIRECT ERROR] {str(e)}", flush=True)
        return jsonify({
            "success": False,
            "error": str(e),
            "tokens_found": 0
        })

@app.route('/scan_with_filters')
def scan_with_filters():
    """Scan avec filtres réalistes pour Raydium"""
    import urllib.request
    import json as json_module
    import time
    
    FILTERS = {
        "min_age": 1,
        "max_age": 180,
        "min_liquidity": 10000,
        "min_volume": 10000,
        "min_mcap": 100000
    }
    
    try:
        url = "https://api.dexscreener.com/latest/dex/search?q=raydium&limit=100"
        
        req = urllib.request.Request(
            url,
            headers={"User-Agent": "Mozilla/5.0"}
        )
        
        with urllib.request.urlopen(req, timeout=15) as response:
            data = json_module.loads(response.read().decode('utf-8'))
            all_pairs = data.get("pairs", [])
            
            current_time = time.time()
            filtered_tokens = []
            
            for pair in all_pairs:
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
                
                if (age_minutes >= FILTERS["min_age"] and 
                    age_minutes <= FILTERS["max_age"] and
                    liquidity >= FILTERS["min_liquidity"] and
                    volume_24h >= FILTERS["min_volume"] and
                    market_cap >= FILTERS["min_mcap"]):
                    
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
                        'url': pair.get('url', '')
                    })
            
            filtered_tokens.sort(key=lambda x: x['age_minutes'])
            
            return jsonify({
                "success": True,
                "tokens": filtered_tokens[:20],
                "tokens_found": len(filtered_tokens),
                "filters_used": FILTERS,
                "debug": {
                    "total_pairs": len(all_pairs),
                    "source": "DexScreener Raydium search"
                }
            })
            
    except Exception as e:
        return jsonify({
            "success": False,
            "error": str(e),
            "tokens_found": 0
        })

@app.route('/test_raydium_api')
def test_raydium_api():
    """Tester différents endpoints DexScreener pour Raydium"""
    import urllib.request
    import json as json_module
    import time
    
    tests = [
        {
            "name": "Raydium search",
            "url": "https://api.dexscreener.com/latest/dex/search?q=raydium&limit=50"
        },
        {
            "name": "Solana pairs with Raydium filter",
            "url": "https://api.dexscreener.com/latest/dex/pairs/solana?dex=raydium&limit=50"
        }
    ]
    
    results = []
    
    for test in tests:
        try:
            print(f"[TEST] Testing: {test['name']}", flush=True)
            start = time.time()
            
            req = urllib.request.Request(
                test["url"],
                headers={"User-Agent": "Mozilla/5.0"}
            )
            
            with urllib.request.urlopen(req, timeout=15) as response:
                elapsed = time.time() - start
                data = json_module.loads(response.read().decode('utf-8'))
                pairs = data.get("pairs", [])
                
                current_time = time.time()
                recent_pairs = []
                
                for pair in pairs[:10]:
                    created_at = pair.get('pairCreatedAt', 0)
                    if created_at:
                        age_seconds = current_time - (created_at / 1000)
                        age_minutes = age_seconds / 60
                        
                        if age_minutes <= 180:
                            recent_pairs.append({
                                "symbol": pair.get('baseToken', {}).get('symbol', '?'),
                                "age_minutes": round(age_minutes, 1),
                                "liquidity": pair.get('liquidity', {}).get('usd', 0),
                                "price": pair.get('priceUsd', 0),
                                "volume_24h": pair.get('volume', {}).get('h24', 0)
                            })
                
                results.append({
                    "test": test["name"],
                    "url": test["url"],
                    "status": response.status,
                    "total_pairs": len(pairs),
                    "recent_pairs": len(recent_pairs),
                    "sample": recent_pairs[:5],
                    "time_ms": round(elapsed * 1000, 2)
                })
                
        except Exception as e:
            results.append({
                "test": test["name"],
                "error": str(e),
                "status": "FAILED"
            })
    
    return jsonify({"success": True, "tests": results})

@app.route('/debug_bot_state')
def debug_bot_state():
    """Vérifie pourquoi /scan dit 'Bot non démarré'"""
    return jsonify({
        "bot_instance_exists": bot_instance is not None,
        "bot_running": getattr(bot_instance, "running", False) if bot_instance else False,
        "bot_engine_exists": getattr(bot_instance, "engine", None) is not None if bot_instance else False,
        "bot_thread_alive": bot_thread.is_alive() if bot_thread else False,
        "BOT_AVAILABLE": BOT_AVAILABLE
    })

# =========================================
# Routes API Birdeye et Helius
# =========================================
@app.route('/test_birdeye')
def test_birdeye():
    """Tester Birdeye API avec clé"""
    api_key = os.environ.get('BIRDEYE_API_KEY')
    
    if not api_key:
        return jsonify({
            "success": False,
            "error": "BIRDEYE_API_KEY manquante",
            "hint": "Obtenez une clé gratuite sur https://birdeye.so/"
        })
    
    import requests
    
    try:
        url = "https://public-api.birdeye.so/public/tokenlist"
        params = {"sort_by": "v24hUSD", "limit": 5}
        headers = {"X-API-KEY": api_key}
        
        response = requests.get(url, params=params, headers=headers, timeout=15)
        
        return jsonify({
            "success": True,
            "status": response.status_code,
            "data": response.json() if response.status_code == 200 else response.text[:200]
        })
        
    except Exception as e:
        return jsonify({"success": False, "error": str(e)})

@app.route('/scan_raydium_birdeye')
def scan_raydium_birdeye_route():
    """Scanner Raydium via Birdeye API"""
    import requests
    import time
    
    try:
        api_key = os.environ.get('BIRDEYE_API_KEY')
        if not api_key:
            return jsonify({
                "success": False,
                "error": "BIRDEYE_API_KEY non définie",
                "hint": "Ajoute BIRDEYE_API_KEY dans les variables d'environnement Render"
            })
        
        print("[BIRDEYE] Scanning via Birdeye API...", flush=True)
        
        url = "https://public-api.birdeye.so/public/tokenlist"
        
        params = {
            "sort_by": "v24hUSD",
            "sort_type": "desc",
            "offset": 0,
            "limit": 100
        }
        
        headers = {
            "X-API-KEY": api_key,
            "User-Agent": "Mozilla/5.0"
        }
        
        start = time.time()
        response = requests.get(url, params=params, headers=headers, timeout=15)
        elapsed = time.time() - start
        
        if response.status_code == 200:
            data = response.json()
            tokens = data.get("data", {}).get("tokens", [])
            
            print(f"[BIRDEYE] Found {len(tokens)} tokens", flush=True)
            
            filtered_tokens = []
            
            for token in tokens:
                markets = token.get('markets', '')
                if 'raydium' not in markets.lower():
                    continue
                
                address = token.get('address', '')
                symbol = token.get('symbol', '')
                name = token.get('name', '')
                price = token.get('price', 0)
                liquidity = token.get('liquidity', 0)
                volume_24h = token.get('volume24h', 0)
                market_cap = token.get('market_cap', 0)
                
                if (liquidity >= 10000 and
                    volume_24h >= 10000 and
                    market_cap >= 100000):
                    
                    filtered_tokens.append({
                        'address': address,
                        'symbol': symbol,
                        'name': name,
                        'liquidity': liquidity,
                        'price': price,
                        'volume_24h': volume_24h,
                        'market_cap': market_cap,
                        'source': 'Birdeye API',
                        'age_minutes': 'N/A'
                    })
            
            print(f"[BIRDEYE] Found {len(filtered_tokens)} Raydium tokens matching criteria", flush=True)
            
            return jsonify({
                "success": True,
                "tokens": filtered_tokens[:20],
                "tokens_found": len(filtered_tokens),
                "debug": {
                    "source": "Birdeye API",
                    "total_tokens": len(tokens),
                    "filters": {
                        "min_liquidity": 10000,
                        "min_volume": 10000,
                        "min_mcap": 100000
                    }
                }
            })
        else:
            return jsonify({
                "success": False,
                "error": f"Birdeye API error: {response.status_code}",
                "response": response.text[:200]
            })
            
    except Exception as e:
        print(f"[BIRDEYE ERROR] {str(e)}", flush=True)
        return jsonify({
            "success": False,
            "error": str(e),
            "tokens_found": 0
        })

@app.route('/scan_helius')
def scan_helius():
    """Scanner les nouvelles pools Raydium via Helius"""
    import requests
    import time
    
    try:
        helius_key = os.environ.get('HELIUS_API_KEY')
        if not helius_key:
            return jsonify({
                "success": False,
                "error": "HELIUS_API_KEY non définie"
            })
        
        print("[HELIUS] Scanning for new Raydium pools...", flush=True)
        
        url = f"https://mainnet.helius-rpc.com/?api-key={helius_key}"
        
        payload = {
            "jsonrpc": "2.0",
            "id": 1,
            "method": "getSignaturesForAddress",
            "params": [
                "675kPX9MHTjS2zt1qfr1NYHuzeLXfQM9H24wFSUt1Mp8",
                {
                    "limit": 50,
                    "commitment": "finalized"
                }
            ]
        }
        
        start = time.time()
        response = requests.post(url, json=payload, timeout=15)
        elapsed = time.time() - start
        
        if response.status_code != 200:
            return jsonify({
                "success": False,
                "error": f"Helius error: {response.status_code}",
                "response": response.text[:200]
            })
        
        data = response.json()
        signatures = data.get("result", [])
        
        print(f"[HELIUS] Found {len(signatures)} recent transactions", flush=True)
        
        tokens = []
        
        for sig in signatures[:5]:
            sig_str = sig.get("signature", "")
            if not sig_str:
                continue
            
            detail_payload = {
                "jsonrpc": "2.0",
                "id": 1,
                "method": "getTransaction",
                "params": [
                    sig_str,
                    {
                        "encoding": "json",
                        "maxSupportedTransactionVersion": 0,
                        "commitment": "finalized"
                    }
                ]
            }
            
            try:
                detail_response = requests.post(url, json=detail_payload, timeout=10)
                if detail_response.status_code == 200:
                    tx_data = detail_response.json()
                    meta = tx_data.get("result", {}).get("meta", {})
                    
                    if meta:
                        tokens.append({
                            "address": "N/A (analyse complexe)",
                            "symbol": "RAYDIUM_TX",
                            "name": f"Transaction {sig_str[:10]}...",
                            "source": "Helius",
                            "transaction": sig_str[:20] + "...",
                            "timestamp": time.strftime('%H:%M:%S')
                        })
                        
            except Exception as e:
                print(f"[HELIUS] Error parsing tx {sig_str[:10]}...: {e}", flush=True)
                continue
        
        return jsonify({
            "success": True,
            "tokens": tokens,
            "tokens_found": len(tokens),
            "debug": {
                "source": "Helius API",
                "signatures_found": len(signatures),
                "parsed_transactions": len(tokens),
                "api_time_ms": round(elapsed * 1000, 2)
            }
        })
        
    except Exception as e:
        print(f"[HELIUS ERROR] {str(e)}", flush=True)
        return jsonify({
            "success": False,
            "error": str(e),
            "tokens_found": 0
        })

@app.route('/scan_new_tokens')
def scan_new_tokens():
    """Scanner les NOUVEAUX tokens via Birdeye"""
    import requests
    import time
    
    try:
        api_key = os.environ.get('BIRDEYE_API_KEY')
        if not api_key:
            return jsonify({"success": False, "error": "BIRDEYE_API_KEY manquante"})
        
        print("[NEW TOKENS] Scanning for new tokens...", flush=True)
        
        url = "https://public-api.birdeye.so/public/tokenlist"
        
        params = {
            "sort_by": "createdAt",
            "sort_type": "desc",
            "offset": 0,
            "limit": 50
        }
        
        headers = {
            "X-API-KEY": api_key,
            "User-Agent": "Mozilla/5.0"
        }
        
        response = requests.get(url, params=params, headers=headers, timeout=15)
        
        if response.status_code == 200:
            data = response.json()
            tokens = data.get("data", {}).get("tokens", [])
            
            current_time = time.time()
            new_tokens = []
            
            for token in tokens:
                created_at = token.get('createdAt', 0)
                if created_at:
                    age_hours = (current_time - created_at) / 3600
                    
                    if age_hours < 24:
                        new_tokens.append({
                            'address': token.get('address', ''),
                            'symbol': token.get('symbol', ''),
                            'name': token.get('name', ''),
                            'created_at': time.strftime('%Y-%m-%d %H:%M', time.localtime(created_at)),
                            'age_hours': round(age_hours, 2),
                            'markets': token.get('markets', ''),
                            'liquidity': token.get('liquidity', 0),
                            'price': token.get('price', 0)
                        })
            
            return jsonify({
                "success": True,
                "tokens": new_tokens,
                "tokens_found": len(new_tokens),
                "debug": {
                    "source": "Birdeye New Tokens",
                    "total_tokens": len(tokens),
                    "age_filter": "<24h"
                }
            })
            
    except Exception as e:
        return jsonify({"success": False, "error": str(e)})

# =========================================
# Routes de configuration
# =========================================
@app.route("/update_config", methods=["POST"])
def update_config():
    """Mettre à jour la configuration du bot"""
    try:
        data = request.json or {}
        
        if 'min_liquidity' in data:
            Config.MIN_LIQUIDITY_USD = float(data['min_liquidity'])
        if 'min_mcap' in data:
            Config.MIN_MARKET_CAP_USD = float(data['min_mcap'])
        if 'min_volume_24h' in data:
            Config.MIN_VOLUME_24H_USD = float(data['min_volume_24h'])
        if 'max_age' in data:
            Config.MAX_AGE_MINUTES = int(data['max_age'])
        if 'min_age' in data:
            Config.MIN_AGE_MINUTES = int(data['min_age'])
        
        return jsonify({
            "success": True,
            "message": "Configuration mise à jour",
            "new_config": {
                "min_liquidity": Config.MIN_LIQUIDITY_USD,
                "min_mcap": Config.MIN_MARKET_CAP_USD,
                "min_volume_24h": Config.MIN_VOLUME_24H_USD,
                "max_age": Config.MAX_AGE_MINUTES,
                "min_age": Config.MIN_AGE_MINUTES
            }
        })
    except Exception as e:
        return jsonify({"success": False, "error": str(e)})

# =========================================
# Routes de gestion du bot
# =========================================
@app.route("/start", methods=["GET", "POST"])
def start_route():
    if not BOT_AVAILABLE:
        return jsonify({"success": False, "message": "Bot non disponible"}), 500

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
        try:
            bal = run_async(wallet.get_balance(), timeout=10.0) if hasattr(wallet, 'get_balance') else 0
        except Exception:
            bal = 0
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
    """Scanner principal - utilise Birdeye si disponible, sinon DexScreener"""
    import requests
    import time
    
    try:
        # Vérifie si le bot est démarré
        if not bot_instance or not getattr(bot_instance, "running", False):
            return jsonify({"success": False, "message": "Bot non démarré"}), 400
        
        print("[SCAN] Starting main scan...", flush=True)
        
        # Essayer Birdeye d'abord si la clé est disponible
        api_key = os.environ.get('BIRDEYE_API_KEY')
        if api_key:
            print("[SCAN] Using Birdeye API...", flush=True)
            
            url = "https://public-api.birdeye.so/public/tokenlist"
            
            params = {
                "sort_by": "v24hUSD",
                "sort_type": "desc",
                "offset": 0,
                "limit": 100
            }
            
            headers = {
                "X-API-KEY": api_key,
                "User-Agent": "Mozilla/5.0"
            }
            
            start = time.time()
            response = requests.get(url, params=params, headers=headers, timeout=15)
            elapsed = time.time() - start
            
            if response.status_code == 200:
                data = response.json()
                tokens = data.get("data", {}).get("tokens", [])
                
                print(f"[SCAN] Found {len(tokens)} tokens from Birdeye", flush=True)
                
                filtered_tokens = []
                
                for token in tokens:
                    markets = token.get('markets', '')
                    if 'raydium' not in markets.lower():
                        continue
                    
                    address = token.get('address', '')
                    symbol = token.get('symbol', '')
                    name = token.get('name', '')
                    price = token.get('price', 0)
                    liquidity = token.get('liquidity', 0)
                    volume_24h = token.get('volume24h', 0)
                    market_cap = token.get('market_cap', 0)
                    
                    if (liquidity >= Config.MIN_LIQUIDITY_USD and
                        market_cap >= Config.MIN_MARKET_CAP_USD and
                        volume_24h >= Config.MIN_VOLUME_24H_USD):
                        
                        filtered_tokens.append({
                            'address': address,
                            'symbol': symbol,
                            'name': name,
                            'liquidity': liquidity,
                            'price': price,
                            'volume_24h': volume_24h,
                            'market_cap': market_cap,
                            'source': 'Birdeye',
                            'url': f"https://birdeye.so/token/{address}?chain=solana"
                        })
                
                print(f"[SCAN] Found {len(filtered_tokens)} tokens matching criteria", flush=True)
                
                return jsonify({
                    "success": True,
                    "tokens": filtered_tokens[:20],
                    "tokens_found": len(filtered_tokens),
                    "debug": {
                        "source": "Birdeye API",
                        "api_time_ms": round(elapsed * 1000, 2),
                        "total_tokens": len(tokens),
                        "config_used": {
                            "min_liquidity": Config.MIN_LIQUIDITY_USD,
                            "min_mcap": Config.MIN_MARKET_CAP_USD,
                            "min_volume": Config.MIN_VOLUME_24H_USD
                        }
                    }
                })
        
        # Fallback: Utiliser DexScreener
        print("[SCAN] Falling back to DexScreener...", flush=True)
        import urllib.request
        import json as json_module
        
        url = "https://api.dexscreener.com/latest/dex/search?q=raydium&limit=100"
        
        req = urllib.request.Request(
            url,
            headers={"User-Agent": "Mozilla/5.0"}
        )
        
        with urllib.request.urlopen(req, timeout=15) as response:
            data = json_module.loads(response.read().decode('utf-8'))
            all_pairs = data.get("pairs", [])
            
            current_time = time.time()
            filtered_tokens = []
            
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
                
                if (age_minutes >= Config.MIN_AGE_MINUTES and 
                    age_minutes <= Config.MAX_AGE_MINUTES and
                    liquidity >= Config.MIN_LIQUIDITY_USD and
                    volume_24h >= Config.MIN_VOLUME_24H_USD and
                    market_cap >= Config.MIN_MARKET_CAP_USD):
                    
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
                    "source": "DexScreener (fallback)",
                    "total_pairs": len(all_pairs),
                    "config": {
                        "min_age": Config.MIN_AGE_MINUTES,
                        "max_age": Config.MAX_AGE_MINUTES,
                        "min_liquidity": Config.MIN_LIQUIDITY_USD,
                        "min_volume": Config.MIN_VOLUME_24H_USD,
                        "min_mcap": Config.MIN_MARKET_CAP_USD
                    }
                }
            })
            
    except Exception as e:
        print(f"[SCAN ERROR] {str(e)}", flush=True)
        return jsonify({
            "success": False,
            "error": str(e),
            "tokens_found": 0
        })

# =========================================
# Main
# =========================================
if __name__ == "__main__":
    port = int(os.environ.get("PORT", "5000"))
    app.run(host="0.0.0.0", port=port, debug=False)
