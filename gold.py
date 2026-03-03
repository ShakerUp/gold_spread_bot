import asyncio
import json
import websockets
import aiohttp
import os
import telegram
import logging
from dotenv import load_dotenv

from telegram import InlineKeyboardButton, InlineKeyboardMarkup, Update
from telegram.ext import (
    ApplicationBuilder,
    CommandHandler,
    CallbackQueryHandler,
    ContextTypes,
)

# ================= ЛОГИРОВАНИЕ =================

logging.basicConfig(
    format="%(asctime)s - %(levelname)s - %(message)s",
    level=logging.INFO
)

logger = logging.getLogger(__name__)

# ================= ЗАГРУЗКА ENV =================

load_dotenv()
BOT_TOKEN = os.getenv("BOT_TOKEN")

if not BOT_TOKEN:
    raise ValueError("BOT_TOKEN не найден в .env файле")

# ================= НАСТРОЙКИ API =================

PARA_WS = "wss://ws.api.prod.paradex.trade/v1?cancel-on-disconnect=false"
MEXC_REST = "https://contract.mexc.com/api/v1/contract/ticker?symbol=XAUT_USDT"
VAR_WS = "wss://omni-ws-server.prod.ap-northeast-1.variational.io/prices"

# ================= ГЛОБАЛЬНЫЕ ЦЕНЫ =================

paradex_mid = None
mexc_mid = None
variational_mid = None

# ================= DATA LISTENERS =================

async def paradex_listener():
    global paradex_mid
    while True:
        try:
            logger.info("Connecting to Paradex WS...")
            async with websockets.connect(PARA_WS, ping_interval=20) as ws:
                await ws.send(json.dumps({
                    "jsonrpc": "2.0",
                    "method": "subscribe",
                    "params": {"channel": "order_book.PAXG-USD-PERP.interactive@15@100ms@0_01"},
                    "id": 1
                }))
                async for msg in ws:
                    data = json.loads(msg)
                    if "params" in data:
                        ob = data["params"]["data"]
                        bid = ob.get("best_bid_api")
                        ask = ob.get("best_ask_api")
                        if bid and ask:
                            paradex_mid = (float(bid["price"]) + float(ask["price"])) / 2
        except Exception as e:
            logger.error(f"Paradex error: {e}")
            await asyncio.sleep(5)


async def mexc_listener():
    global mexc_mid
    async with aiohttp.ClientSession() as session:
        while True:
            try:
                async with session.get(MEXC_REST) as resp:
                    data = await resp.json()
                    if data.get("success"):
                        mexc_mid = float(data["data"]["lastPrice"])
            except Exception as e:
                logger.error(f"MEXC error: {e}")
            await asyncio.sleep(2)


VAR_REST = "https://omni-client-api.prod.ap-northeast-1.variational.io/metadata/stats"

async def variational_listener():
    global variational_mid

    headers = {
        "Origin": "https://omni.variational.io"
    }

    subscribe_message = {
        "action": "subscribe",
        "instruments": [
            {
                "underlying": "PAXG",
                "instrument_type": "perpetual_future",
                "settlement_asset": "USDC",
                "funding_interval_s": 3600
            }
        ]
    }

    while True:
        try:
            logger.info("Connecting to Variational WS...")

            async with websockets.connect(
                "wss://omni-ws-server.prod.ap-northeast-1.variational.io/prices",
                extra_headers=headers,
                ping_interval=20
            ) as ws:

                logger.info("Connected to Variational")
                await ws.send(json.dumps(subscribe_message))
                logger.info("Subscribed to PAXG perpetual")

                async for msg in ws:
                    data = json.loads(msg)

                    if "pricing" in data:
                        variational_mid = float(data["pricing"]["price"])
                        logger.info(f"Variational price updated: {variational_mid}")

        except Exception as e:
            logger.error(f"Variational WS error: {e}")
            await asyncio.sleep(5)

# ================= TELEGRAM =================

async def start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    logger.info("Received /start")

    keyboard = [
        [InlineKeyboardButton("📊 Paradex vs MEXC", callback_data="start_track_mexc")],
        [InlineKeyboardButton("📊 Paradex vs Variational", callback_data="start_track_var")]
    ]

    await update.message.reply_text(
        "📊 Gold Spread Monitor\n\nВыберите режим:",
        reply_markup=InlineKeyboardMarkup(keyboard)
    )


async def button_handler(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    logger.info(f"Callback received: {query.data}")

    await query.answer()

    try:
        if query.data == "start_track_mexc":
            logger.info("Starting MEXC tracking")
            context.user_data["tracking"] = True
            context.user_data["mode"] = "mexc"
            context.user_data["task"] = asyncio.create_task(update_loop(query, context))

        elif query.data == "start_track_var":
            logger.info("Starting Variational tracking")
            context.user_data["tracking"] = True
            context.user_data["mode"] = "variational"
            context.user_data["task"] = asyncio.create_task(update_loop(query, context))

        elif query.data == "stop_track":
            logger.info("Stopping tracking")
            context.user_data["tracking"] = False
            if "task" in context.user_data:
                context.user_data["task"].cancel()

            await query.edit_message_text("⛔ Трекинг остановлен")

    except Exception as e:
        logger.error(f"Button handler error: {e}")


async def update_loop(query, context):
    message = query.message
    last_text = ""

    logger.info("Update loop started")

    while context.user_data.get("tracking"):
        try:
            mode = context.user_data.get("mode")

            if mode == "mexc" and paradex_mid and mexc_mid:
                diff = paradex_mid - mexc_mid
                pct = (diff / mexc_mid) * 100
                text = (
                    f"Paradex: {paradex_mid:.2f}\n"
                    f"MEXC: {mexc_mid:.2f}\n\n"
                    f"Spread: {diff:.2f}$ ({pct:.4f}%)"
                )

            elif mode == "variational" and paradex_mid and variational_mid:
                diff = paradex_mid - variational_mid
                pct = (diff / variational_mid) * 100
                text = (
                    f"Paradex: {paradex_mid:.2f}\n"
                    f"Variational: {variational_mid:.2f}\n\n"
                    f"Spread: {diff:.2f}$ ({pct:.4f}%)"
                )
            else:
                text = "⏳ Waiting for data..."

            if text != last_text:
                await message.edit_text(
                    text,
                    reply_markup=InlineKeyboardMarkup([
                        [InlineKeyboardButton("⛔ Stop", callback_data="stop_track")]
                    ])
                )
                last_text = text

            await asyncio.sleep(10)

        except Exception as e:
            logger.error(f"Update loop error: {e}")
            await asyncio.sleep(3)

# ================= INIT =================

async def post_init(application):
    logger.info("Starting background listeners...")
    asyncio.create_task(paradex_listener())
    asyncio.create_task(mexc_listener())
    asyncio.create_task(variational_listener())


def main():
    logger.info("Bot starting...")

    app = (
        ApplicationBuilder()
        .token(BOT_TOKEN)
        .post_init(post_init)
        .build()
    )

    app.add_handler(CommandHandler("start", start))
    app.add_handler(CallbackQueryHandler(button_handler))

    app.run_polling()


if __name__ == "__main__":
    main()