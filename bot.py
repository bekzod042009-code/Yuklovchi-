"""
Universal Video Downloader Bot — bitta fayldagi versiya (telefon/Termux uchun qulay).

Ishga tushirishdan oldin pastdagi "SOZLAMALAR" qismini to'ldiring
yoki muhit o'zgaruvchilari (environment variables) orqali bering:
  BOT_TOKEN, ADMIN_IDS, FORCE_SUB_CHANNEL

O'rnatish:
    pip install aiogram yt-dlp
    (FFmpeg ham kerak: Termux'da  pkg install ffmpeg)

Ishga tushirish:
    python bot.py
"""

import os
import re
import uuid
import sqlite3
import asyncio
import logging
from contextlib import contextmanager
from datetime import datetime

import yt_dlp
from aiogram import Bot, Dispatcher, Router, F
from aiogram.filters import CommandStart, Command
from aiogram.fsm.context import FSMContext
from aiogram.fsm.state import State, StatesGroup
from aiogram.fsm.storage.memory import MemoryStorage
from aiogram.types import (
    Message, CallbackQuery, FSInputFile,
    InlineKeyboardMarkup, InlineKeyboardButton,
)

# =========================== SOZLAMALAR ===========================

BOT_TOKEN = os.getenv("BOT_TOKEN", "SIZNING_BOT_TOKENINGIZ_BU_YERGA")
ADMIN_IDS = [int(x) for x in os.getenv("ADMIN_IDS", "").split(",") if x.strip()]
FORCE_SUB_CHANNEL = os.getenv("FORCE_SUB_CHANNEL", "")  # masalan: @mychannel yoki bo'sh qoldiring

DOWNLOAD_DIR = "downloads"
DB_PATH = "bot.db"
MAX_FILESIZE = 50 * 1024 * 1024  # Telegram standart limiti - 50MB

os.makedirs(DOWNLOAD_DIR, exist_ok=True)
logging.basicConfig(level=logging.INFO, format="%(asctime)s - %(levelname)s - %(message)s")
logger = logging.getLogger(__name__)

# =========================== MA'LUMOTLAR BAZASI ===========================

@contextmanager
def get_conn():
    conn = sqlite3.connect(DB_PATH)
    conn.row_factory = sqlite3.Row
    try:
        yield conn
        conn.commit()
    finally:
        conn.close()


def init_db():
    with get_conn() as conn:
        conn.execute("""CREATE TABLE IF NOT EXISTS users (
            user_id INTEGER PRIMARY KEY, username TEXT, full_name TEXT,
            is_blocked INTEGER DEFAULT 0, joined_at TEXT, last_active TEXT)""")
        conn.execute("""CREATE TABLE IF NOT EXISTS downloads (
            id INTEGER PRIMARY KEY AUTOINCREMENT, user_id INTEGER, url TEXT,
            kind TEXT, quality TEXT, filesize INTEGER, created_at TEXT)""")
        conn.execute("""CREATE TABLE IF NOT EXISTS settings (
            key TEXT PRIMARY KEY, value TEXT)""")


def add_or_update_user(user_id, username, full_name):
    now = datetime.utcnow().isoformat()
    with get_conn() as conn:
        row = conn.execute("SELECT user_id FROM users WHERE user_id=?", (user_id,)).fetchone()
        if row:
            conn.execute("UPDATE users SET username=?, full_name=?, last_active=? WHERE user_id=?",
                         (username, full_name, now, user_id))
        else:
            conn.execute("INSERT INTO users (user_id, username, full_name, joined_at, last_active) VALUES (?,?,?,?,?)",
                         (user_id, username, full_name, now, now))


def is_user_blocked(user_id):
    with get_conn() as conn:
        row = conn.execute("SELECT is_blocked FROM users WHERE user_id=?", (user_id,)).fetchone()
        return bool(row and row["is_blocked"])


def set_user_blocked(user_id, blocked):
    with get_conn() as conn:
        conn.execute("UPDATE users SET is_blocked=? WHERE user_id=?", (1 if blocked else 0, user_id))


def get_all_user_ids():
    with get_conn() as conn:
        return [r["user_id"] for r in conn.execute("SELECT user_id FROM users WHERE is_blocked=0").fetchall()]


def count_users():
    with get_conn() as conn:
        return conn.execute("SELECT COUNT(*) c FROM users").fetchone()["c"]


def count_active_users(days=7):
    with get_conn() as conn:
        return conn.execute(
            "SELECT COUNT(*) c FROM users WHERE datetime(last_active) >= datetime('now', ?)",
            (f"-{days} days",)).fetchone()["c"]


def log_download(user_id, url, kind, quality, filesize):
    with get_conn() as conn:
        conn.execute("INSERT INTO downloads (user_id, url, kind, quality, filesize, created_at) VALUES (?,?,?,?,?,?)",
                     (user_id, url, kind, quality, filesize, datetime.utcnow().isoformat()))


def count_downloads():
    with get_conn() as conn:
        return conn.execute("SELECT COUNT(*) c FROM downloads").fetchone()["c"]


def get_setting(key, default=None):
    with get_conn() as conn:
        row = conn.execute("SELECT value FROM settings WHERE key=?", (key,)).fetchone()
        return row["value"] if row else default


def set_setting(key, value):
    with get_conn() as conn:
        conn.execute("INSERT INTO settings (key, value) VALUES (?,?) "
                     "ON CONFLICT(key) DO UPDATE SET value=excluded.value", (key, value))


# =========================== YORDAMCHI FUNKSIYALAR ===========================

def format_duration(seconds):
    seconds = int(seconds or 0)
    h, rem = divmod(seconds, 3600)
    m, s = divmod(rem, 60)
    return f"{h}:{m:02d}:{s:02d}" if h else f"{m}:{s:02d}"


def format_filesize(num_bytes):
    if not num_bytes:
        return "noma'lum"
    for unit in ["B", "KB", "MB", "GB"]:
        if num_bytes < 1024:
            return f"{num_bytes:.1f} {unit}"
        num_bytes /= 1024
    return f"{num_bytes:.1f} TB"


# =========================== VIDEO YUKLASH (yt-dlp) ===========================

def extract_info(url):
    ydl_opts = {"quiet": True, "no_warnings": True, "skip_download": True, "noplaylist": True}
    with yt_dlp.YoutubeDL(ydl_opts) as ydl:
        return ydl.extract_info(url, download=False)


def available_qualities(raw_info):
    """Mavjud sifatlarni (144p...2160p) kichikdan kattaga tartiblab qaytaradi."""
    seen = {}
    for f in raw_info.get("formats") or []:
        height = f.get("height")
        if not height or f.get("vcodec") == "none":
            continue
        filesize = f.get("filesize") or f.get("filesize_approx") or 0
        existing = seen.get(height)
        if existing is None or (filesize and filesize > existing["filesize"]):
            seen[height] = {"height": height, "filesize": filesize}
    return [seen[h] for h in sorted(seen.keys())]


def _unique_path(ext):
    return os.path.join(DOWNLOAD_DIR, f"{uuid.uuid4().hex}.{ext}")


def download_video(url, height=None):
    out_path = _unique_path("mp4")
    if height:
        fmt = (f"bestvideo[height<={height}][ext=mp4]+bestaudio[ext=m4a]/"
               f"bestvideo[height<={height}]+bestaudio/best[height<={height}]")
    else:
        fmt = f"bestvideo[filesize<{MAX_FILESIZE}][ext=mp4]+bestaudio[ext=m4a]/best[filesize<{MAX_FILESIZE}]/best"

    ydl_opts = {
        "quiet": True, "no_warnings": True, "format": fmt, "noplaylist": True,
        "outtmpl": out_path.replace(".mp4", ".%(ext)s"),
        "merge_output_format": "mp4",
    }
    with yt_dlp.YoutubeDL(ydl_opts) as ydl:
        info = ydl.extract_info(url, download=True)
        filename = ydl.prepare_filename(info)
        alt = filename.rsplit(".", 1)[0] + ".mp4"
        if not filename.endswith(".mp4") and os.path.exists(alt):
            filename = alt
    return filename


def download_audio(url):
    out_path = _unique_path("mp3")
    ydl_opts = {
        "quiet": True, "no_warnings": True, "format": "bestaudio/best", "noplaylist": True,
        "outtmpl": out_path.replace(".mp3", ".%(ext)s"),
        "postprocessors": [{"key": "FFmpegExtractAudio", "preferredcodec": "mp3", "preferredquality": "192"}],
    }
    with yt_dlp.YoutubeDL(ydl_opts) as ydl:
        info = ydl.extract_info(url, download=True)
        filename = ydl.prepare_filename(info)
        mp3_path = os.path.splitext(filename)[0] + ".mp3"
        if os.path.exists(mp3_path):
            filename = mp3_path
    return filename


def cleanup_file(path):
    try:
        if path and os.path.exists(path):
            os.remove(path)
    except OSError:
        pass


# =========================== FOYDALANUVCHI HANDLERLARI ===========================

user_router = Router()
URL_REGEX = re.compile(r"https?://\S+")
PENDING_VIDEOS = {}  # key -> {"url":..., "user_id":...}


async def check_force_sub(message: Message) -> bool:
    if not FORCE_SUB_CHANNEL or get_setting("force_sub", "off") != "on":
        return True
    try:
        member = await message.bot.get_chat_member(FORCE_SUB_CHANNEL, message.from_user.id)
        if member.status in ("left", "kicked"):
            await message.answer(f"Botdan foydalanish uchun avval kanalga obuna bo'ling: {FORCE_SUB_CHANNEL}")
            return False
    except Exception:
        pass
    return True


@user_router.message(CommandStart())
async def cmd_start(message: Message):
    add_or_update_user(message.from_user.id, message.from_user.username, message.from_user.full_name)
    await message.answer(
        "Salom! 👋\n\nMenga video havolasini yuboring (YouTube, Instagram, TikTok va h.k.) — "
        "men uni sizga MP4 fayl sifatida yuklab beraman. Sifat va faqat audio (MP3) tanlash imkoniyati ham bor."
    )


@user_router.message(F.text.regexp(URL_REGEX))
async def handle_link(message: Message):
    if is_user_blocked(message.from_user.id):
        return
    if not await check_force_sub(message):
        return

    add_or_update_user(message.from_user.id, message.from_user.username, message.from_user.full_name)
    url = URL_REGEX.search(message.text).group(0)
    status_msg = await message.answer("🔍 Havola tekshirilmoqda...")

    try:
        raw = extract_info(url)
    except Exception as e:
        logger.warning(f"extract_info xatosi: {e}")
        await status_msg.edit_text("❌ Bu havolani tanib bo'lmadi yoki platforma qo'llab-quvvatlanmaydi.")
        return

    qualities = available_qualities(raw)
    key = uuid.uuid4().hex[:12]
    PENDING_VIDEOS[key] = {"url": url, "user_id": message.from_user.id}

    title = raw.get("title") or "Nomsiz video"
    duration = format_duration(raw.get("duration"))
    extractor = raw.get("extractor_key") or "Noma'lum"

    caption = f"🎬 <b>{title}</b>\n⏱ Davomiyligi: {duration}\n🌐 Manba: {extractor}\n\nKerakli sifatni tanlang:"

    buttons, row = [], []
    for q in qualities:
        label = f"{q['height']}p" + (f" (~{format_filesize(q['filesize'])})" if q["filesize"] else "")
        row.append(InlineKeyboardButton(text=label, callback_data=f"dl:{key}:{q['height']}"))
        if len(row) == 2:
            buttons.append(row); row = []
    if row:
        buttons.append(row)
    if not qualities:
        buttons.append([InlineKeyboardButton(text="🎥 Yuklab olish (avtomatik sifat)", callback_data=f"dl:{key}:0")])
    buttons.append([InlineKeyboardButton(text="🎵 Faqat audio (MP3)", callback_data=f"audio:{key}:0")])

    await status_msg.edit_text(caption, reply_markup=InlineKeyboardMarkup(inline_keyboard=buttons), parse_mode="HTML")


@user_router.callback_query(F.data.startswith("dl:"))
async def handle_quality_choice(callback: CallbackQuery):
    _, key, height = callback.data.split(":")
    height = int(height) or None
    entry = PENDING_VIDEOS.get(key)
    if not entry:
        await callback.answer("So'rov muddati o'tgan, havolani qayta yuboring.", show_alert=True)
        return

    await callback.answer()
    await callback.message.edit_text("⬇️ Video yuklab olinmoqda, iltimos kuting...")

    file_path = None
    try:
        file_path = download_video(entry["url"], height=height)
        size = os.path.getsize(file_path)
        if size > MAX_FILESIZE:
            await callback.message.edit_text(
                "❌ Video hajmi ruxsat etilgan limitdan (50MB) katta. Pastroq sifatni tanlab ko'ring.")
            return
        await callback.message.answer_video(FSInputFile(file_path), caption=f"✅ Tayyor{f' ({height}p)' if height else ''}")
        log_download(entry["user_id"], entry["url"], "video", str(height or "auto"), size)
        await callback.message.delete()
    except Exception:
        logger.exception("Video yuklashda xatolik")
        await callback.message.edit_text("❌ Xatolik yuz berdi: yuklab bo'lmadi.")
    finally:
        cleanup_file(file_path)
        PENDING_VIDEOS.pop(key, None)


@user_router.callback_query(F.data.startswith("audio:"))
async def handle_audio_choice(callback: CallbackQuery):
    _, key, _ = callback.data.split(":")
    entry = PENDING_VIDEOS.get(key)
    if not entry:
        await callback.answer("So'rov muddati o'tgan, havolani qayta yuboring.", show_alert=True)
        return

    await callback.answer()
    await callback.message.edit_text("⬇️ Audio yuklab olinmoqda, iltimos kuting...")

    file_path = None
    try:
        file_path = download_audio(entry["url"])
        size = os.path.getsize(file_path)
        if size > MAX_FILESIZE:
            await callback.message.edit_text("❌ Audio fayl hajmi ruxsat etilgan limitdan katta.")
            return
        await callback.message.answer_audio(FSInputFile(file_path), caption="✅ Tayyor (MP3)")
        log_download(entry["user_id"], entry["url"], "audio", "mp3", size)
        await callback.message.delete()
    except Exception:
        logger.exception("Audio yuklashda xatolik")
        await callback.message.edit_text("❌ Xatolik yuz berdi: audio yuklab bo'lmadi.")
    finally:
        cleanup_file(file_path)
        PENDING_VIDEOS.pop(key, None)


# =========================== ADMIN HANDLERLARI ===========================

admin_router = Router()


def is_admin(user_id):
    return user_id in ADMIN_IDS


class BroadcastState(StatesGroup):
    waiting_message = State()


class BlockState(StatesGroup):
    waiting_user_id = State()
    waiting_unblock_id = State()


def admin_menu_kb():
    force_sub_status = get_setting("force_sub", "off")
    toggle_label = "🔕 Majburiy obunani o'chirish" if force_sub_status == "on" else "🔔 Majburiy obunani yoqish"
    return InlineKeyboardMarkup(inline_keyboard=[
        [InlineKeyboardButton(text="📊 Statistika", callback_data="admin:stats")],
        [InlineKeyboardButton(text="📢 Xabar yuborish", callback_data="admin:broadcast")],
        [InlineKeyboardButton(text=toggle_label, callback_data="admin:togglesub")],
        [InlineKeyboardButton(text="⛔ Bloklash", callback_data="admin:block")],
        [InlineKeyboardButton(text="✅ Blokdan chiqarish", callback_data="admin:unblock")],
    ])


@admin_router.message(Command("admin"))
async def cmd_admin(message: Message):
    if not is_admin(message.from_user.id):
        return
    await message.answer("🛠 Admin panel", reply_markup=admin_menu_kb())


@admin_router.callback_query(F.data == "admin:stats")
async def admin_stats(callback: CallbackQuery):
    if not is_admin(callback.from_user.id):
        return await callback.answer()
    text = (f"📊 <b>Bot statistikasi</b>\n\n"
            f"👥 Jami foydalanuvchilar: {count_users()}\n"
            f"🟢 Faol (7 kun): {count_active_users(7)}\n"
            f"⬇️ Jami yuklab olishlar: {count_downloads()}")
    await callback.message.answer(text, parse_mode="HTML")
    await callback.answer()


@admin_router.callback_query(F.data == "admin:togglesub")
async def admin_toggle_sub(callback: CallbackQuery):
    if not is_admin(callback.from_user.id):
        return await callback.answer()
    new_value = "off" if get_setting("force_sub", "off") == "on" else "on"
    set_setting("force_sub", new_value)
    await callback.message.edit_text("🛠 Admin panel", reply_markup=admin_menu_kb())
    await callback.answer(f"Majburiy obuna: {'yoqildi' if new_value == 'on' else 'ochirildi'}")


@admin_router.callback_query(F.data == "admin:broadcast")
async def admin_broadcast_start(callback: CallbackQuery, state: FSMContext):
    if not is_admin(callback.from_user.id):
        return await callback.answer()
    await callback.message.answer("📢 Yuboriladigan xabar matnini kiriting:")
    await state.set_state(BroadcastState.waiting_message)
    await callback.answer()


@admin_router.message(BroadcastState.waiting_message)
async def admin_broadcast_send(message: Message, state: FSMContext, bot: Bot):
    await state.clear()
    user_ids = get_all_user_ids()
    sent, failed = 0, 0
    status = await message.answer(f"⏳ {len(user_ids)} foydalanuvchiga yuborilmoqda...")
    for uid in user_ids:
        try:
            await bot.copy_message(chat_id=uid, from_chat_id=message.chat.id, message_id=message.message_id)
            sent += 1
        except Exception:
            failed += 1
        await asyncio.sleep(0.05)
    await status.edit_text(f"✅ Yuborildi: {sent}\n❌ Yuborilmadi: {failed}")


@admin_router.callback_query(F.data == "admin:block")
async def admin_block_start(callback: CallbackQuery, state: FSMContext):
    if not is_admin(callback.from_user.id):
        return await callback.answer()
    await callback.message.answer("⛔ Bloklanadigan foydalanuvchi ID sini kiriting:")
    await state.set_state(BlockState.waiting_user_id)
    await callback.answer()


@admin_router.message(BlockState.waiting_user_id)
async def admin_block_apply(message: Message, state: FSMContext):
    await state.clear()
    try:
        uid = int(message.text.strip())
    except ValueError:
        return await message.answer("❌ Noto'g'ri ID format.")
    set_user_blocked(uid, True)
    await message.answer(f"✅ Foydalanuvchi {uid} bloklandi.")


@admin_router.callback_query(F.data == "admin:unblock")
async def admin_unblock_start(callback: CallbackQuery, state: FSMContext):
    if not is_admin(callback.from_user.id):
        return await callback.answer()
    await callback.message.answer("✅ Blokdan chiqariladigan foydalanuvchi ID sini kiriting:")
    await state.set_state(BlockState.waiting_unblock_id)
    await callback.answer()


@admin_router.message(BlockState.waiting_unblock_id)
async def admin_unblock_apply(message: Message, state: FSMContext):
    await state.clear()
    try:
        uid = int(message.text.strip())
    except ValueError:
        return await message.answer("❌ Noto'g'ri ID format.")
    set_user_blocked(uid, False)
    await message.answer(f"✅ Foydalanuvchi {uid} blokdan chiqarildi.")


# =========================== ISHGA TUSHIRISH ===========================

async def main():
    init_db()
    bot = Bot(token=BOT_TOKEN)
    dp = Dispatcher(storage=MemoryStorage())
    dp.include_router(admin_router)
    dp.include_router(user_router)
    logger.info("Bot ishga tushdi.")
    await dp.start_polling(bot)


if __name__ == "__main__":
    asyncio.run(main())
