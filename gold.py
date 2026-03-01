import asyncio
import json
import websockets
import aiohttp
import os
import telegram
from dotenv import load_dotenv

from telegram import InlineKeyboardButton, InlineKeyboardMarkup, Update
from telegram.ext import (
    ApplicationBuilder,
    CommandHandler,
    CallbackQueryHandler,
    ContextTypes,
)

# Загрузка переменных
load_dotenv()
BOT_TOKEN = os.getenv("BOT_TOKEN")

if not BOT_TOKEN:
    raise ValueError("BOT_TOKEN не найден в .env файле")

# Настройки API
PARA_WS = "wss://ws.api.prod.paradex.trade/v1?cancel-on-disconnect=false"
MEXC_REST = "https://contract.mexc.com/api/v1/contract/ticker?symbol=XAUT_USDT"

# Глобальные переменные для цен
paradex_mid = None
mexc_mid = None

# ================= БЛОК ДАННЫХ (БИРЖИ) =================

async def paradex_listener():
    global paradex_mid
    while True:
        try:
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
            print(f"Paradex reconnecting... {e}")
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
                print(f"MEXC error: {e}")
            await asyncio.sleep(2)

# ================= БЛОК ТЕЛЕГРАМ БОТА =================

async def start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    keyboard = [[InlineKeyboardButton("▶ Начать трекинг", callback_data="start_track")]]
    markup = InlineKeyboardMarkup(keyboard)
    await update.message.reply_text(
        "📊 Мониторинг цен PAXG/XAUT\n\nНажмите кнопку для запуска обновления (раз в 10 сек):", 
        reply_markup=markup
    )

async def button_handler(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await query.answer()

    if query.data == "start_track":
        if context.user_data.get("tracking"):
            return
        
        context.user_data["tracking"] = True
        task = asyncio.create_task(update_message_loop(query, context))
        context.user_data["task"] = task

    elif query.data == "stop_track":
        context.user_data["tracking"] = False
        if "task" in context.user_data:
            context.user_data["task"].cancel()
        
        await query.edit_message_text(
            "⛔ Трекинг остановлен",
            reply_markup=InlineKeyboardMarkup([[InlineKeyboardButton("▶ Начать заново", callback_data="start_track")]])
        )

async def update_message_loop(query, context):
    message = query.message
    last_text = ""

    while context.user_data.get("tracking"):
        if paradex_mid and mexc_mid:
            diff = paradex_mid - mexc_mid
            pct = (diff / mexc_mid) * 100
            text = (
                f"📊 Gold Spread Monitor\n\n"
                f"PAXG (Paradex): {paradex_mid:.2f}\n"
                f"XAUT (MEXC): {mexc_mid:.2f}\n\n"
                f"Spread: {diff:.2f}$ ({pct:.4f}%)"
            )
        else:
            text = "⏳ Получение данных с бирж..."

        if text != last_text:
            try:
                await message.edit_text(
                    text,
                    reply_markup=InlineKeyboardMarkup([[InlineKeyboardButton("⛔ Остановить", callback_data="stop_track")]])
                )
                last_text = text
            except telegram.error.RetryAfter as e:
                await asyncio.sleep(e.retry_after)
            except Exception:
                pass
        
        await asyncio.sleep(10)

# ================= ИНИЦИАЛИЗАЦИЯ =================

async def post_init(application):
    asyncio.create_task(paradex_listener())
    asyncio.create_task(mexc_listener())

def main():
    app = (
        ApplicationBuilder()
        .token(BOT_TOKEN)
        .post_init(post_init)
        .build()
    )

    app.add_handler(CommandHandler("start", start))
    app.add_handler(CallbackQueryHandler(button_handler))

    print("--- Бот запущен (без пароля) ---")
    app.run_polling()

if __name__ == "__main__":
    main()