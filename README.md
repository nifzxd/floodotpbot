# 🛡️ Telegram FloodWait Control Bot

Bot Telegram berbasis Python yang dirancang untuk mengunci sementara sebuah nomor dari permintaan kode OTP (One-Time Password). 

Bot ini bekerja dengan cara menembak fungsi `sign_in` menggunakan tebakan kode acak yang salah secara berulang-ulang (brute-force). Hal ini memaksa sistem keamanan Telegram mendeteksi aktivitas tersebut sebagai ancaman dan mengaktifkan status **FloodWait** (limit OTP) pada nomor tersebut, biasanya hingga 24 jam.

Cocok digunakan sebagai "Panic Button" untuk mengamankan nomor Anda sendiri saat dalam bahaya, atau untuk mengunci nomor tertentu agar tidak ada pihak lain yang bisa login.

## ⚠️ PERINGATAN KERAS (DISCLAIMER)
**Gunakan script ini dengan risiko Anda sendiri.**
1. **Risiko Ban API ID:** Karena script ini melakukan *brute-force* secara agresif, Telegram dapat menandai `api_id` yang Anda gunakan sebagai spam dan memblokirnya secara permanen.
2. **Gunakan API ID Pribadi:** Jangan gunakan API ID publik. Selalu buat API ID pribadi di [my.telegram.org](https://my.telegram.org).
3. **Jangan Serang Massal:** Script ini didesain untuk "Sniper" (satu nomor target), bukan "Nuke" (massal). Menembak banyak nomor sekaligus secara bersamaan akan membuat `api_id` Anda langsung ter-*banned*.

---

## ✨ Fitur Utama (Sesuai Kode Terbaru)

- **🔥 Sniper Lock (FloodWait):** Mengunci nomor target hingga 24 jam dari permintaan OTP.
- **🤖 Human-like Jitter:** Menggunakan jeda waktu acak (`random.uniform(1.2, 2.5)` detik) antar tembakan. Ini mencegah Telegram mendeteksi pola robot yang kaku dan memperpanjang umur `api_id` Anda.
- **🛡️ Anti Double-Click (Race Condition):** Mencegah *error* atau pemblokiran ganda jika Anda tidak sengaja mengklik tombol eksekusi dua kali dengan cepat. Bot akan menolak proses ganda untuk nomor yang sama.
- **🧹 Auto-Cleanup Memory:** Menghapus data status limit yang sudah kedaluwarsa dari RAM server secara otomatis setiap kali Anda mengecek statusnya.
- **🖥️ UI Inline Keyboard:** Semua interaksi menggunakan tombol yang rapi dan interaktif (tanpa perlu mengingat *slash command* selain `/start`).

---

## 📋 Prasyarat

- Python 3.8 atau lebih baru.
- `api_id` dan `api_hash` dari [my.telegram.org](https://my.telegram.org).
- `bot_token` dari [@BotFather](https://t.me/BotFather) di Telegram.
- Telegram ID Anda (bisa dicek lewat bot seperti [@userinfobot](https://t.me/userinfobot)).

---

## 🚀 Instalasi & Persiapan

### 1. Siapkan Folder
Simpan file bot (misal: `flood_bot.py`), `requirements.txt`, dan file `config.json` di dalam satu folder yang sama.

### 2. Install Dependencies
Buka Terminal atau Command Prompt, arahkan ke folder script, lalu jalankan:
```bash
pip install -r requirements.txt