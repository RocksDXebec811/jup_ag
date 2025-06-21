import asyncio
import json
import os
from dotenv import load_dotenv
from base58 import b58decode
from solders.keypair import Keypair
from solders.pubkey import Pubkey
from solders.signature import Signature
from solders.system_program import TransferParams, transfer
from solders.instruction import Instruction
from solders.hash import Hash
from solders.transaction import Transaction
from solders.message import Message
from solders.rpc.types import TxOpts
from solders.rpc.async_api import AsyncClient
from websockets.sync.client import connect

load_dotenv()

TELEGRAM_TOKEN = os.getenv("TELEGRAM_TOKEN")
TELEGRAM_CHAT_ID = os.getenv("TELEGRAM_CHAT_ID")
PRIVATE_KEY = os.getenv("PRIVATE_KEY")
BUY_AMOUNT = float(os.getenv("BUY_AMOUNT", "0.009"))
WATCHLIST = os.getenv("WATCHLIST", "").split(",")

keypair = Keypair.from_bytes(b58decode(PRIVATE_KEY))

async def send_jupiter_swap(input_mint, output_mint, amount, slippage_bps=100):
    from jup.ag import quote, swap  # requires jup-ag python lib (custom or wrapper)
    quote_result = await quote(input_mint, output_mint, amount, slippage_bps)
    tx = await swap(quote_result, user_keypair=keypair)
    return tx

async def monitor_tokens():
    uri = "wss://api.pump.fun/live"
    print("Connexion WebSocket à Pump.fun...")

    async with connect(uri) as websocket:
        print("✅ Connecté à Pump.fun WebSocket")
        while True:
            message = await websocket.recv()
            data = json.loads(message)

            creator = data.get("creator")
            token = data.get("token")
            if creator in WATCHLIST:
                print(f"🎯 Nouveau token repéré : {token} par {creator}")
                await buy_and_schedule_sell(token)

async def buy_and_schedule_sell(token_mint):
    input_mint = "So11111111111111111111111111111111111111112"  # SOL
    output_mint = token_mint

    print(f"💰 Achat en cours du token {token_mint} via Jupiter...")
    tx_sig = await send_jupiter_swap(input_mint, output_mint, int(BUY_AMOUNT * 1e9))

    print(f"✅ Achat effectué : {tx_sig}")
    await schedule_sell(token_mint)

async def schedule_sell(token_mint):
    print(f"📈 Planification des ventes pour {token_mint} (x2, x5, x10)...")
    # (Simulation) En production, ici tu surveilles le prix du token pour vendre selon la valeur atteinte
    # simulate wait and sell (replace with actual price check)
    await asyncio.sleep(30)  # attendre pour le x2
    await sell_token(token_mint, 0.5)

    await asyncio.sleep(30)  # attendre pour x5
    await sell_token(token_mint, 0.3)

    await asyncio.sleep(30)  # attendre pour x10
    await sell_token(token_mint, 0.2)

async def sell_token(token_mint, percentage):
    output_mint = "So11111111111111111111111111111111111111112"  # SOL
    input_mint = token_mint

    print(f"🔁 Revente de {percentage*100:.0f}% du token {token_mint}")
    # ici, on suppose un solde constant simulé
    simulated_amount = int(BUY_AMOUNT * percentage * 1e9)
    tx_sig = await send_jupiter_swap(input_mint, output_mint, simulated_amount)
    print(f"✅ Token revendu : {tx_sig}")

if __name__ == "__main__":
    try:
        asyncio.run(monitor_tokens())
    except KeyboardInterrupt:
        print("Arrêt du bot.")

