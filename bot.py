import asyncio
import logging
import os
import shutil
import tempfile

from aiogram import Bot, Dispatcher, F, Router
from aiogram.client.session.aiohttp import AiohttpSession
from aiogram.filters import Command
from aiogram.fsm.context import FSMContext
from aiogram.fsm.state import State, StatesGroup
from aiogram.fsm.storage.memory import MemoryStorage
from aiogram.types import CallbackQuery, FSInputFile, Message
from aiogram.utils.keyboard import InlineKeyboardBuilder

import renderer

TOKEN = os.getenv("BOT_TOKEN")
if not TOKEN:
    raise SystemExit("BOT_TOKEN o'rnatilmagan")

logging.basicConfig(level=logging.INFO)
router = Router()
RENDER_SEM = asyncio.Semaphore(1)  # bir vaqtda bitta render (server qotib qolmasin)
MAX_FILE = 20 * 1024 * 1024  # Telegram bot yuklab olish limiti


class Form(StatesGroup):
    bg = State()
    center = State()
    music = State()
    lyrics = State()
    fmt = State()
    quality = State()
    color = State()
    rendering = State()


def kb(rows):
    b = InlineKeyboardBuilder()
    for text, data in rows:
        b.button(text=text, callback_data=data)
    b.adjust(2)
    return b.as_markup()


async def get_dir(state: FSMContext) -> str:
    d = (await state.get_data()).get("dir")
    if not d or not os.path.isdir(d):
        d = tempfile.mkdtemp(prefix="vb_")
        await state.update_data(dir=d)
    return d


async def reset(state: FSMContext):
    d = (await state.get_data()).get("dir")
    if d:
        shutil.rmtree(d, ignore_errors=True)
    await state.clear()


async def safe_edit(msg: Message, text: str):
    try:
        await msg.edit_text(text)
    except Exception:
        pass


# ------------------------------------------------------------ buyruqlar
@router.message(Command("start", "new"))
async def start(m: Message, state: FSMContext):
    await reset(state)
    await state.set_state(Form.bg)
    await m.answer(
        "🎬 Phonk montaj botiga xush kelibsiz!\n\n"
        "1/7 — Avval ORQA FON rasmini yuboring."
    )


@router.message(Command("cancel"))
async def cancel(m: Message, state: FSMContext):
    await reset(state)
    await m.answer("Bekor qilindi. Qaytadan boshlash: /new")


# ------------------------------------------------------------ qadamlar
async def save_image(m: Message, bot: Bot, dest: str) -> bool:
    if m.photo:
        fid = m.photo[-1].file_id
    elif m.document and (m.document.mime_type or "").startswith("image/"):
        fid = m.document.file_id
    else:
        return False
    await bot.download(fid, destination=dest)
    return True


@router.message(Form.bg)
async def got_bg(m: Message, state: FSMContext, bot: Bot):
    d = await get_dir(state)
    if not await save_image(m, bot, os.path.join(d, "bg.img")):
        return await m.answer("Iltimos, rasm yuboring (orqa fon).")
    await state.set_state(Form.center)
    await m.answer("2/7 — Endi O'RTADAGI rasmni yuboring (dumaloq qilib qirqiladi).")


@router.message(Form.center)
async def got_center(m: Message, state: FSMContext, bot: Bot):
    d = await get_dir(state)
    if not await save_image(m, bot, os.path.join(d, "center.img")):
        return await m.answer("Iltimos, rasm yuboring (o'rtadagi rasm).")
    await state.set_state(Form.music)
    await m.answer(
        "3/7 — MUSIQA yuboring (mp3 yoki audio fayl, 20 MB gacha).\n"
        "Uzun bo'lsa, eng kuchli bass'li 25 soniyalik qismi avtomatik olinadi."
    )


@router.message(Form.music)
async def got_music(m: Message, state: FSMContext, bot: Bot):
    obj = m.audio or m.voice
    if not obj and m.document:
        mt = m.document.mime_type or ""
        name = (m.document.file_name or "").lower()
        if mt.startswith("audio/") or name.endswith((".mp3", ".wav", ".m4a", ".ogg", ".flac", ".aac")):
            obj = m.document
    if not obj:
        return await m.answer("Iltimos, musiqa (audio fayl) yuboring.")
    if obj.file_size and obj.file_size > MAX_FILE:
        return await m.answer("Fayl 20 MB dan katta. Kichikroq (siqilgan) mp3 yuboring.")
    d = await get_dir(state)
    await bot.download(obj.file_id, destination=os.path.join(d, "music.bin"))
    await state.set_state(Form.lyrics)
    await m.answer(
        "4/7 — Qo'shiq SO'ZLARINI yozing (har bir qator alohida qatorda).\n"
        "So'z kerak bo'lmasa: /skip"
    )


@router.message(Form.lyrics, Command("skip"))
async def skip_lyrics(m: Message, state: FSMContext):
    await state.update_data(lyrics="")
    await ask_format(m, state)


@router.message(Form.lyrics, F.text)
async def got_lyrics(m: Message, state: FSMContext):
    if m.text.startswith("/"):
        return
    await state.update_data(lyrics=m.text)
    await ask_format(m, state)


async def ask_format(m: Message, state: FSMContext):
    await state.set_state(Form.fmt)
    await m.answer("5/7 — Video formati:", reply_markup=kb([
        ("📱 Shorts 9:16", "fmt:shorts"), ("🖥 YouTube 16:9", "fmt:youtube")]))


@router.callback_query(Form.fmt, F.data.startswith("fmt:"))
async def got_fmt(cb: CallbackQuery, state: FSMContext):
    await cb.answer()
    await state.update_data(fmt=cb.data.split(":")[1])
    await state.set_state(Form.quality)
    await cb.message.edit_text("6/7 — Sifat:", reply_markup=kb([
        ("720p (tez)", "q:720"), ("1080p (sekinroq)", "q:1080")]))


@router.callback_query(Form.quality, F.data.startswith("q:"))
async def got_quality(cb: CallbackQuery, state: FSMContext):
    await cb.answer()
    await state.update_data(quality=cb.data.split(":")[1])
    await state.set_state(Form.color)
    await cb.message.edit_text("7/7 — Neon rang:", reply_markup=kb([
        ("🟣 Binafsha", "col:purple"), ("🔴 Qizil", "col:red"),
        ("🔵 Ko'k", "col:blue"), ("🩵 Moviy", "col:cyan"),
        ("🟢 Yashil", "col:green"), ("🟡 Oltin", "col:gold")]))


@router.callback_query(Form.color, F.data.startswith("col:"))
async def got_color(cb: CallbackQuery, state: FSMContext, bot: Bot):
    await cb.answer()
    color = cb.data.split(":")[1]
    data = await state.get_data()
    d = data["dir"]
    await state.set_state(Form.rendering)
    status = await cb.message.edit_text(
        "⏳ Navbatda..." if RENDER_SEM.locked() else "⏳ Render boshlandi...")
    chat_id = cb.message.chat.id
    loop = asyncio.get_running_loop()
    last = {"q": -1}

    def progress(p: float):
        q = int(p * 4)
        if q > last["q"]:
            last["q"] = q
            asyncio.run_coroutine_threadsafe(
                safe_edit(status, f"⏳ Render: {int(p * 100)}%"), loop)

    out = os.path.join(d, "out.mp4")
    try:
        async with RENDER_SEM:
            await safe_edit(status, "⏳ Render boshlandi...")
            _, W, H, dur = await asyncio.to_thread(
                renderer.render_video,
                os.path.join(d, "bg.img"), os.path.join(d, "center.img"),
                os.path.join(d, "music.bin"), data.get("lyrics", ""),
                data["fmt"], data["quality"], color, out, progress)
        if os.path.getsize(out) > 49 * 1024 * 1024:
            await bot.send_message(chat_id, "Video 50 MB dan katta chiqdi. 720p tanlab qayta urinib ko'ring: /new")
        else:
            await bot.send_video(chat_id, FSInputFile(out), width=W, height=H,
                                 duration=int(dur), supports_streaming=True,
                                 caption="✅ Tayyor! Yangi video: /new")
        await status.delete()
    except ValueError as e:
        await safe_edit(status, f"❌ {e}\n\nQaytadan: /new")
    except Exception as e:
        logging.exception("render xatosi")
        await safe_edit(status, f"❌ Xatolik: {str(e)[:300]}\n\nQaytadan: /new")
    finally:
        await reset(state)


@router.message()
async def fallback(m: Message):
    await m.answer("Boshlash uchun /new yozing.")


# ------------------------------------------------------------ ishga tushirish
async def health_server():
    """Render'da Web Service bo'lsa port ochib turish uchun."""
    port = os.getenv("PORT")
    if not port:
        return
    from aiohttp import web
    app = web.Application()
    app.router.add_get("/", lambda r: web.Response(text="ok"))
    runner = web.AppRunner(app)
    await runner.setup()
    await web.TCPSite(runner, "0.0.0.0", int(port)).start()


async def main():
    bot = Bot(TOKEN, session=AiohttpSession(timeout=300))
    dp = Dispatcher(storage=MemoryStorage())
    dp.include_router(router)
    await health_server()
    await bot.delete_webhook(drop_pending_updates=True)
    await dp.start_polling(bot)


if __name__ == "__main__":
    asyncio.run(main())
