import asyncio
import json
import websockets
import aiohttp
import os
from dotenv import load_dotenv

from telegram import InlineKeyboardButton, InlineKeyboardMarkup, Update
from telegram.ext import (
    ApplicationBuilder,
    CommandHandler,
    CallbackQueryHandler,
    ContextTypes,
)

load_dotenv()
BOT_TOKEN = os.getenv("BOT_TOKEN")

if not BOT_TOKEN:
    raise ValueError("BOT_TOKEN not found in .env file")

PARA_WS = "wss://ws.api.prod.paradex.trade/v1?cancel-on-disconnect=false"
MEXC_REST = "https://contract.mexc.com/api/v1/contract/ticker?symbol=XAUT_USDT"

paradex_mid = None
mexc_mid = None


# ================= PAR ADEX =================

async def paradex_listener():
    global paradex_mid

    while True:
        try:
            async with websockets.connect(PARA_WS, ping_interval=20) as ws:
                await ws.send(json.dumps({
                    "jsonrpc": "2.0",
                    "method": "subscribe",
                    "params": {
                        "channel": "order_book.PAXG-USD-PERP.interactive@15@100ms@0_01"
                    },
                    "id": 1
                }))

                async for msg in ws:
                    data = json.loads(msg)

                    if "params" not in data:
                        continue

                    ob = data["params"]["data"]
                    bid = ob.get("best_bid_api")
                    ask = ob.get("best_ask_api")

                    if bid and ask:
                        bid = float(bid["price"])
                        ask = float(ask["price"])
                        paradex_mid = (bid + ask) / 2

        except Exception as e:
            print("Paradex reconnecting...", e)
            await asyncio.sleep(2)


# ================= MEXC =================

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
                print("MEXC error:", e)

            await asyncio.sleep(1)


# ================= TG BOT =================

async def start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    keyboard = [
        [InlineKeyboardButton("▶ Начать трекинг", callback_data="start_track")],
    ]

    await update.message.reply_text(
        "📊 Gold Spread Tracker\n\nНажми кнопку ниже:",
        reply_markup=InlineKeyboardMarkup(keyboard)
    )


async def button_handler(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await query.answer()

    if query.data == "start_track":

        if context.user_data.get("tracking"):
            return

        context.user_data["tracking"] = True

        keyboard = [
            [InlineKeyboardButton("⛔ Остановить трекинг", callback_data="stop_track")]
        ]

        await query.edit_message_text(
            "Запуск трекинга...",
            reply_markup=InlineKeyboardMarkup(keyboard)
        )

        task = asyncio.create_task(update_message_loop(query, context))
        context.user_data["task"] = task

    elif query.data == "stop_track":

        context.user_data["tracking"] = False

        task = context.user_data.get("task")
        if task:
            task.cancel()

        keyboard = [
            [InlineKeyboardButton("▶ Начать трекинг", callback_data="start_track")]
        ]

        await query.edit_message_text(
            "⛔ Трекинг остановлен",
            reply_markup=InlineKeyboardMarkup(keyboard)
        )


async def update_message_loop(query, context):
    message = query.message

    while context.user_data.get("tracking", False):
        if paradex_mid and mexc_mid:
            diff = paradex_mid - mexc_mid
            pct = diff / mexc_mid * 100

            text = (
                f"📊 Gold Spread Monitor\n\n"
                f"PAXG (Paradex): {paradex_mid:.2f}\n"
                f"XAUT (MEXC): {mexc_mid:.2f}\n\n"
                f"Spread: {diff:.2f}$ ({pct:.4f}%)"
            )
        else:
            text = "Загрузка цен..."

        try:
            await message.edit_text(
                text,
                reply_markup=InlineKeyboardMarkup(
                    [[InlineKeyboardButton("⛔ Остановить трекинг", callback_data="stop_track")]]
                )
            )
        except:
            pass

        await asyncio.sleep(1)


# ================= INIT LISTENERS =================

async def post_init(application):
    asyncio.create_task(paradex_listener())
    asyncio.create_task(mexc_listener())


# ================= MAIN =================

def main():
    app = (
        ApplicationBuilder()
        .token(BOT_TOKEN)
        .post_init(post_init)
        .build()
    )

    app.add_handler(CommandHandler("start", start))
    app.add_handler(CallbackQueryHandler(button_handler))

    print("Bot started...")
    app.run_polling()


if __name__ == "__main__":
    main()