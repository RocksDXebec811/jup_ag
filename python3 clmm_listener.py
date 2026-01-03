import asyncio
import json
import time
import os
import requests
import websockets
from typing import List, Optional
from datetime import datetime, timezone
from zoneinfo import ZoneInfo

# ===================== CONFIG =====================
HELIUS_API_KEY = os.getenv("HELIUS_API_KEY")
if not HELIUS_API_KEY:
    raise SystemExit("Missing HELIUS_API_KEY")

RPC_HTTP = f"https://mainnet.helius-rpc.com/?api-key={HELIUS_API_KEY}"
RPC_WS   = f"wss://mainnet.helius-rpc.com/?api-key={HELIUS_API_KEY}"

EARLY_POOL_URL = os.getenv(
    "EARLY_POOL_URL",
    "https://jup-ag-ep8t.onrender.com/early-pool"
)

CLMM_PROGRAM_ID = "CAMMCzo5YL8w4VFF8KVHrK22GGUsp5VTaW7grrKgrWqK"
WSOL_MINT = "So11111111111111111111111111111111111111112"

# ===================== STRICT FILTERS =====================
MAX_AGE_MINUTES = 30
BRUSSELS_TZ = ZoneInfo("Europe/Brussels")

REQUIRE_RAYDIUM = True
REQUIRE_WSOL = True

# ===================== PERFORMANCE =====================
QUEUE_MAX = 1000
MAX_WORKERS = 1
RPC_MIN_INTERVAL = 0.2

SESSION = requests.Session()
SESSION.headers.update({"user-agent": "Mozilla/5.0"})

SEEN_SIGS = set()
SEEN_KEYS = set()

# ===================== TIME HELPERS =====================
def now_brussels_date():
    return datetime.now(BRUSSELS_TZ).date()

def minutes_since(ts: int) -> float:
    return (time.time() - ts) / 60

def is_today(ts: int) -> bool:
    d = datetime.fromtimestamp(ts, tz=timezone.utc).astimezone(BRUSSELS_TZ).date()
    return d == now_brussels_date()

# ===================== RPC =====================
RPC_LOCK = asyncio.Lock()
_last_rpc = 0.0

async def rpc(method: str, params: list):
    global _last_rpc
    async with RPC_LOCK:
        delta = RPC_MIN_INTERVAL - (time.time() - _last_rpc)
        if delta > 0:
            await asyncio.sleep(delta)
        _last_rpc = time.time()

        payload = {"jsonrpc": "2.0", "id": 1, "method": method, "params": params}
        r = await asyncio.to_thread(SESSION.post, RPC_HTTP, json=payload, timeout=30)
        r.raise_for_status()
        return r.json().get("result")

# ===================== LOG FILTER =====================
def looks_like_pool_init(logs: List[str]) -> bool:
    joined = " ".join(logs).lower()
    return (
        ("initialize" in joined or "create pool" in joined or "initialize pool" in joined)
        and "swap" not in joined
    )

# ===================== DEXSCREENER =====================
DEX_CACHE = {}
DEX_CACHE_TTL = 60

async def dexscreener_confirm(mint: str) -> Optional[dict]:
    now = time.time()
    cached = DEX_CACHE.get(mint)
    if cached and now - cached[0] < DEX_CACHE_TTL:
        return cached[1]

    url = f"https://api.dexscreener.com/tokens/v1/solana/{mint}"
    try:
        r = await asyncio.to_thread(SESSION.get, url, timeout=10)
        r.raise_for_status()
        data = r.json()
    except Exception:
        DEX_CACHE[mint] = (now, None)
        return None

    valid = []
    for p in data:
        if REQUIRE_RAYDIUM and "raydium" not in (p.get("dexId") or "").lower():
            continue
        if REQUIRE_WSOL and p.get("quoteToken", {}).get("address") != WSOL_MINT:
            continue

        created = p.get("pairCreatedAt")
        if not isinstance(created, int):
            continue

        age = minutes_since(created // 1000)
        if age > MAX_AGE_MINUTES:
            continue

        if not is_today(created // 1000):
            continue

        valid.append(p)

    if not valid:
        DEX_CACHE[mint] = (now, None)
        return None

    best = sorted(valid, key=lambda x: x["pairCreatedAt"], reverse=True)[0]
    DEX_CACHE[mint] = (now, best)
    return best

# ===================== WS PARSING =====================
def parse_ws(raw: str):
    try:
        j = json.loads(raw)
    except Exception:
        return []

    if isinstance(j, dict):
        return [j]
    if isinstance(j, list):
        return [x for x in j if isinstance(x, dict)]
    return []

def extract_sig_logs(msg):
    if msg.get("method") != "logsNotification":
        return None
    v = msg.get("params", {}).get("result", {}).get("value", {})
    sig = v.get("signature")
    logs = v.get("logs", [])
    if sig and isinstance(logs, list):
        return sig, logs
    return None

# ===================== WORKER =====================
async def worker(q: asyncio.Queue):
    while True:
        sig = await q.get()
        try:
            tx = await rpc(
                "getTransaction",
                [sig, {"encoding": "jsonParsed", "commitment": "confirmed"}],
            )
            if not tx:
                continue

            bt = tx.get("blockTime")
            if not isinstance(bt, int):
                continue

            if not is_today(bt):
                continue

            age = minutes_since(bt)
            if age > MAX_AGE_MINUTES:
                continue

            instructions = tx.get("transaction", {}).get("message", {}).get("instructions", [])
            for ins in instructions:
                if ins.get("programId") != CLMM_PROGRAM_ID:
                    continue

                accs = ins.get("accounts", [])
                mints = [a for a in accs if a != WSOL_MINT]

                if not mints:
                    continue

                mint = mints[0]
                key = f"{mint}"
                if key in SEEN_KEYS:
                    continue
                SEEN_KEYS.add(key)

                # ===== DEX CONFIRMATION =====
                pair = await dexscreener_confirm(mint)
                if not pair:
                    continue

                print("\n✅ CONFIRMED-DEX TOKEN")
                print(f"mint: {mint}")
                print(f"age: {age:.2f}m")
                print(f"dexscreener: {pair['url']}")

                payload = {
                    "source": "confirmed-dex",
                    "mint": mint,
                    "signature": sig,
                    "age_minutes": age,
                    "dexscreener_url": pair["url"],
                    "pairAddress": pair.get("pairAddress"),
                    "liquidity_usd": pair.get("liquidity", {}).get("usd", 0),
                }

                try:
                    await asyncio.to_thread(SESSION.post, EARLY_POOL_URL, json=payload, timeout=20)
                except Exception as e:
                    print("post EARLY_POOL_URL error:", e)

        except Exception as e:
            print("worker error:", e)
        finally:
            q.task_done()

# ===================== LISTENER LOOP WITH RECONNECT =====================
async def run_listener():
    q = asyncio.Queue(maxsize=QUEUE_MAX)

    # worker(s) started once, continue across WS reconnects
    for _ in range(MAX_WORKERS):
        asyncio.create_task(worker(q))

    backoff = 1
    while True:
        try:
            async with websockets.connect(
                RPC_WS,
                ping_interval=15,   # more frequent keepalive
                ping_timeout=60,    # allow lag
                close_timeout=5,    # avoid "timed out while closing connection"
                max_queue=2048,
                open_timeout=20,
            ) as ws:
                # reset backoff on successful connect
                backoff = 1

                await ws.send(json.dumps({
                    "jsonrpc": "2.0",
                    "id": 1,
                    "method": "logsSubscribe",
                    "params": [
                        {"mentions": [CLMM_PROGRAM_ID]},
                        {"commitment": "processed"},
                    ],
                }))

                print("✅ Listening (CONFIRMED-DEX ONLY, <30min)")

                while True:
                    raw = await ws.recv()
                    for msg in parse_ws(raw):
                        res = extract_sig_logs(msg)
                        if not res:
                            continue
                        sig, logs = res

                        if sig in SEEN_SIGS:
                            continue
                        if not looks_like_pool_init(logs):
                            continue

                        SEEN_SIGS.add(sig)
                        if not q.full():
                            await q.put(sig)

        except (
            websockets.exceptions.ConnectionClosedError,
            websockets.exceptions.ConnectionClosedOK,
            TimeoutError,
            OSError,
        ) as e:
            print(f"⚠️ WS disconnected: {type(e).__name__}: {e}")
            sleep_s = min(60, backoff)
            print(f"🔁 Reconnecting in {sleep_s}s...")
            await asyncio.sleep(sleep_s)
            backoff = min(60, backoff * 2)

        except Exception as e:
            print(f"❌ Unexpected error: {type(e).__name__}: {e}")
            await asyncio.sleep(5)

# ===================== MAIN =====================
async def main():
    await run_listener()

if __name__ == "__main__":
    asyncio.run(main())
