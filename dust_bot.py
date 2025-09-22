import json
import time
import base58
import os
import asyncio
from solana.rpc.api import Client
from solana.rpc.types import TxOpts
from solana.keypair import Keypair
from solana.publickey import PublicKey
from solders.pubkey import Pubkey
from solders.transaction import VersionedTransaction, Transaction
from solders.system_program import TransferParams, transfer
from solders.spl.token.instructions import burn, close_account, BurnParams, CloseAccountParams
from solders.spl.token.constants import TOKEN_PROGRAM_ID, TOKEN_2022_PROGRAM_ID
import requests
from flask import Flask, jsonify, request
from flask_cors import CORS
import telebot
import redis
from concurrent.futures import ThreadPoolExecutor

app = Flask(__name__)
CORS(app)

# Config
RPC_ENDPOINT = os.getenv('RPC_ENDPOINT', 'https://api.mainnet-beta.solana.com')
WALLET_PRIVATE_KEY_B58 = os.getenv('WALLET_PRIVATE_KEY')
TELEGRAM_TOKEN = os.getenv('TELEGRAM_TOKEN', '8293367338:AAGJZlGNUDDXx3H88GkTvyuBCcAXA5sjlhU')
MINI_APP_URL = os.getenv('MINI_APP_URL', 'https://dust-jzt3jnjgf-dust-bot.vercel.app')
REDIS_URL = os.getenv('REDIS_URL', 'redis://localhost:6379')
JUPITER_QUOTE_API = "https://quote-api.jup.ag/v6/quote"
JUPITER_SWAP_API = "https://quote-api.jup.ag/v6/swap"
INCINERATOR_API = "https://v1.api.sol-incinerator.com"
DEXSCREENER_API = "https://api.dexscreener.com/latest/dex/tokens/"
PUMP_API = "https://frontend-api.pump.fun/coins?offset=0&limit=500"
WALLET_PUBKEY = PublicKey("B99peTzS2ZRXkZLpcE3CbisFXkxZ77EEWwgkGRbkuWmb")
SOL_MINT = PublicKey("So11111111111111111111111111111111111111112")
INCINERATOR_ADDR = PublicKey("1nc1nerator11111111111111111111111111111111")
FEE_WALLET = PublicKey("9tzPdS72tm7vE8669BkghpsFaiR3Z1VS9K8rdEDeFQRD")
API_KEY = os.getenv('INCINERATOR_API_KEY', '')
LAMPORTS_PER_SOL = 1_000_000_000

INACTIVE_VOLUME_THRESHOLD = 100
MIN_SWAP_AMOUNT_LAMPORTS = 100
SLIPPAGE_BPS = 1
DELAY_SEC = 1
DEX_DELAY_SEC = 2
TX_OPTS = TxOpts(skip_preflight=True, preflight_commitment="processed")
BATCH_SIZE = 10

client = Client(RPC_ENDPOINT)
if WALLET_PRIVATE_KEY_B58:
    keypair_bytes = base58.b58decode(WALLET_PRIVATE_KEY_B58)
    keypair = Keypair.from_bytes(keypair_bytes)
    if keypair.pubkey() != WALLET_PUBKEY:
        raise ValueError("Private key mismatch!")
else:
    raise ValueError("Set WALLET_PRIVATE_KEY env var!")

bot = telebot.TeleBot(TELEGRAM_TOKEN)
headers = {"x-api-key": API_KEY} if API_KEY else {}

r = redis.from_url(REDIS_URL)
executor = ThreadPoolExecutor(max_workers=4)

successful_buys = 0
total_fees_sent = 0.0

def get_balance():
    return client.get_balance(WALLET_PUBKEY).value / LAMPORTS_PER_SOL

def fetch_meme_coins():
    response = requests.get(PUMP_API)
    if response.status_code != 200:
        return []
    coins = response.json()
    inactive_coins = []
    for coin in coins:
        if 'mint' not in coin:
            continue
        mint = coin['mint']
        symbol = coin.get('name', 'UNK')[:4].upper()
        dex_resp = requests.get(f"{DEXSCREENER_API}{mint}")
        if dex_resp.status_code == 200:
            dex_data = dex_resp.json()
            if dex_data.get('pairs'):
                volume_h24 = float(dex_data['pairs'][0].get('volume', {}).get('h24', 0))
                if volume_h24 < INACTIVE_VOLUME_THRESHOLD:
                    inactive_coins.append((symbol, mint))
        time.sleep(DEX_DELAY_SEC)
    return inactive_coins

def get_quote(input_mint, output_mint, amount_lamports):
    params = {
        "inputMint": str(input_mint),
        "outputMint": str(output_mint),
        "amount": amount_lamports,
        "slippageBps": SLIPPAGE_BPS,
        "onlyDirectRoutes": False,
    }
    response = requests.get(JUPITER_QUOTE_API, params=params)
    if response.status_code == 200:
        quote = response.json()
        if int(quote.get('outAmount', 0)) > 0:
            return quote
    return None

def execute_swap(quote_response):
    global successful_buys
    swap_request = {
        "quoteResponse": quote_response,
        "userPublicKey": str(WALLET_PUBKEY),
        "wrapAndUnwrapSol": True,
        "computeUnitPriceMicroLamports": 0,
    }
    response = requests.post(JUPITER_SWAP_API, json=swap_request)
    if response.status_code == 200:
        swap_tx_b64 = response.json()["swapTransaction"]
        tx = VersionedTransaction.from_bytes(base58.b58decode(swap_tx_b64))
        tx.sign([keypair])
        sig = client.send_transaction(tx, opts=TX_OPTS).value
        print(f"Swap TX: https://solscan.io/tx/{sig}")
        successful_buys += 1
        return True
    return False

async def async_execute_swap(quote_response):
    loop = asyncio.get_event_loop()
    return await loop.run_in_executor(executor, execute_swap, quote_response)

def queue_user_job(user_id, action, params):
    job = json.dumps({'user_id': user_id, 'action': action, 'params': params, 'timestamp': time.time()})
    r.lpush('user_queue', job)
    return f"Job queued for user {user_id}: {action}"

def process_queue():
    while True:
        job = r.brpop('user_queue', timeout=5)
        if job:
            data = json.loads(job[1])
            user_id = data['user_id']
            action = data['action']
            print(f"Processed {action} for {user_id}")
        time.sleep(1)

from threading import Thread
queue_thread = Thread(target=process_queue, daemon=True)
queue_thread.start()

def run_dust_bot(user_id):
    global successful_buys
    successful_buys = 0
    MEME_COINS = fetch_meme_coins()
    print(f"Proceeding with {len(MEME_COINS)} inactive coins for {user_id}.")
    for symbol, token_addr in MEME_COINS:
        current_balance = get_balance()
        if current_balance < 0.0000002:
            print("Out of dust! Starting auto-burn phase.")
            break
        print(f"Buying dust from inactive {symbol}...")
        quote = get_quote(SOL_MINT, PublicKey(token_addr), MIN_SWAP_AMOUNT_LAMPORTS)
        if quote and execute_swap(quote):
            print(f"Got dust from inactive {symbol}!")
        time.sleep(DELAY_SEC)
    burn_all_tokens()
    send_remaining_to_incinerator()
    send_transaction_fees()
    return successful_buys

# (Include all other functions from v8: get_token_accounts, manual_burn_batch, auto_burn_via_api, burn_all_tokens, send_remaining_to_incinerator, send_transaction_fees)

# Telegram Handlers
@bot.message_handler(commands=['start'])
def start_handler(message):
    user_id = message.from_user.id
    markup = telebot.types.ReplyKeyboardMarkup(one_time_keyboard=True)
    btn_launch = telebot.types.KeyboardButton('Launch Dashboard', web_app=telebot.types.WebAppInfo(url=MINI_APP_URL))
    markup.add(btn_launch)
    bot.reply_to(message, "Welcome to Solana Dust Bot! Connect your wallet and start accumulating dust.", reply_markup=markup)

@bot.message_handler(commands=['status'])
def status_handler(message):
    status = f"Successful Buys: {successful_buys} | Fees Sent: {total_fees_sent:.6f} SOL"
    bot.reply_to(message, status)

@bot.message_handler(commands=['run'])
def run_handler(message):
    user_id = message.from_user.id
    queue_user_job(user_id, 'buy', {})
    bot.reply_to(message, "Dust buy queued—check /status.")

@bot.message_handler(commands=['burn'])
def burn_handler(message):
    user_id = message.from_user.id
    queue_user_job(user_id, 'burn', {})
    bot.reply_to(message, "Burn queued.")

@bot.message_handler(func=lambda message: True)
def echo_handler(message):
    bot.reply_to(message, "Use /start for dashboard, /status, /run, or /burn.")

@app.route('/webhook', methods=['POST'])
def webhook():
    update = telebot.types.Update.de_json(request.get_json())
    bot.process_new_updates([update])
    return 'OK', 200

@app.route('/api/run-bot', methods=['POST'])
def api_run_bot():
    user_id = request.json.get('user_id', 'default')
    buys = run_dust_bot(user_id)
    return jsonify({'logs': f'Completed {buys} buys for {user_id}'})

@app.route('/api/burn', methods=['POST'])
def api_burn():
    user_id = request.json.get('user_id', 'default')
    reclaimed = burn_all_tokens()  # Adapt for user
    return jsonify({'reclaimed': reclaimed})

@app.route('/api/logs', methods=['GET'])
def api_logs():
    return jsonify(['Dust bot logs...'])

@app.route('/api/status', methods=['GET'])
def api_status():
    return jsonify({
        'successful_buys': successful_buys,
        'total_fees_sent': total_fees_sent,
        'fee_wallet': str(FEE_WALLET)
    })

if __name__ == '__main__':
    app.run(host='0.0.0.0', port=5000)
