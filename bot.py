import asyncio
import hashlib
import hmac
import html
import json
import logging
import os
import sys
import time
from urllib.parse import parse_qsl

from aiohttp import web
from aiogram import Bot, Dispatcher, F, types
from aiogram.exceptions import TelegramBadRequest
from aiogram.filters import Command, CommandObject
from aiogram.fsm.context import FSMContext
from aiogram.fsm.state import State, StatesGroup
from aiogram.utils.keyboard import InlineKeyboardBuilder

try:
    from dotenv import load_dotenv
    load_dotenv()
except ImportError:
    pass

import database as store  # .env yuklangandan keyin import qilinadi

BASE_DIR = os.path.dirname(os.path.abspath(__file__))
BOT_TOKEN = os.getenv("BOT_TOKEN", "")
WEBAPP_URL = os.getenv("WEBAPP_URL", "")
ADMIN_ID = int(os.getenv("ADMIN_ID", "8352201913"))
PORT = int(os.getenv("PORT", "8080"))

dp = Dispatcher()


class AdminStates(StatesGroup):
    waiting_for_video_url = State()
    waiting_for_video_title = State()


# ======================= Telegram bot =======================

def admin_keyboard():
    b = InlineKeyboardBuilder()
    b.row(types.InlineKeyboardButton(text="➕ Video qo'shish", callback_data="add_video_btn"))
    b.row(types.InlineKeyboardButton(text="🎞 Videolar ro'yxati", callback_data="videos_btn"))
    b.row(types.InlineKeyboardButton(text="📊 Statistika", callback_data="stats_btn"))
    return b.as_markup()


def back_keyboard():
    b = InlineKeyboardBuilder()
    b.row(types.InlineKeyboardButton(text="⬅️ Orqaga", callback_data="admin_panel"))
    return b.as_markup()


async def safe_edit(callback: types.CallbackQuery, text: str, markup):
    try:
        await callback.message.edit_text(text, reply_markup=markup, parse_mode="HTML")
    except TelegramBadRequest:
        pass  # xabar o'zgarmagan bo'lsa Telegram xato qaytaradi — e'tibor bermaymiz


@dp.message(Command("start"))
async def cmd_start(message: types.Message, command: CommandObject):
    user = message.from_user
    referred_by = None
    if command.args and command.args.startswith("ref_"):
        try:
            referred_by = int(command.args[4:])
        except ValueError:
            referred_by = None

    fullname = " ".join(p for p in [user.first_name, user.last_name] if p)
    store.create_user(user.id, user.username, fullname, referred_by)

    text = (
        f"👋 Welcome to VidiPay, {user.first_name}!\n\n"
        "🎬 Watch videos — every second you watch is added to your balance.\n"
        "👥 Invite friends — each active friend unlocks a frame: +1.05$ for every hour you watch "
        "(only when you and your friend are both in Tier 1).\n\n"
        "Tap below to start 👇"
    )

    builder = InlineKeyboardBuilder()
    builder.row(types.InlineKeyboardButton(
        text="▶️ Open VidiPay",
        web_app=types.WebAppInfo(url=WEBAPP_URL),
    ))
    if user.id == ADMIN_ID:
        builder.row(types.InlineKeyboardButton(text="🛠️ Admin Panel", callback_data="admin_panel"))

    await message.answer(text, reply_markup=builder.as_markup())


@dp.message(Command("cancel"))
async def cmd_cancel(message: types.Message, state: FSMContext):
    await state.clear()
    await message.answer("Bekor qilindi.")


@dp.callback_query(F.data == "admin_panel")
async def admin_panel(callback: types.CallbackQuery, state: FSMContext):
    if callback.from_user.id != ADMIN_ID:
        await callback.answer("Sizda bu huquq yo'q!", show_alert=True)
        return
    await state.clear()
    await safe_edit(callback, "🛠️ <b>VidiPay Admin Boshqaruvi</b>\n\nKerakli bo'limni tanlang:", admin_keyboard())
    await callback.answer()


@dp.callback_query(F.data == "stats_btn")
async def stats(callback: types.CallbackQuery):
    if callback.from_user.id != ADMIN_ID:
        await callback.answer("Sizda bu huquq yo'q!", show_alert=True)
        return
    s = store.get_stats()
    text = (
        "📊 <b>Statistika</b>\n\n"
        f"👤 Foydalanuvchilar: {s['users']}\n"
        f"🔥 Oxirgi 24 soatda faol: {s['active_24h']}\n"
        f"🎞 Faol videolar: {s['videos']}\n"
        f"⏱ Jami ko'rilgan: {s['watch_hours']:.1f} soat\n"
        f"💰 Foydalanuvchilar balansi yig'indisi: {s['total_balance']:.2f}$"
    )
    await safe_edit(callback, text, back_keyboard())
    await callback.answer()


@dp.callback_query(F.data == "videos_btn")
async def videos_list(callback: types.CallbackQuery):
    if callback.from_user.id != ADMIN_ID:
        await callback.answer("Sizda bu huquq yo'q!", show_alert=True)
        return
    videos = store.list_videos()
    b = InlineKeyboardBuilder()
    if not videos:
        text = "🎞 Hozircha video yo'q."
    else:
        lines = [f"{v['id']}. {html.escape(v['title'] or '')}" for v in videos]
        text = "🎞 <b>Oxirgi videolar</b> (o'chirish uchun bosing):\n\n" + "\n".join(lines)
        for v in videos:
            b.row(types.InlineKeyboardButton(text=f"🗑 {v['id']}", callback_data=f"del_video_{v['id']}"))
    b.row(types.InlineKeyboardButton(text="⬅️ Orqaga", callback_data="admin_panel"))
    await safe_edit(callback, text, b.as_markup())
    await callback.answer()


@dp.callback_query(F.data.startswith("del_video_"))
async def delete_video(callback: types.CallbackQuery):
    if callback.from_user.id != ADMIN_ID:
        await callback.answer("Sizda bu huquq yo'q!", show_alert=True)
        return
    try:
        store.delete_video(int(callback.data.removeprefix("del_video_")))
    except ValueError:
        pass
    await callback.answer("O'chirildi")
    await videos_list(callback)


@dp.callback_query(F.data == "add_video_btn")
async def start_add_video(callback: types.CallbackQuery, state: FSMContext):
    if callback.from_user.id != ADMIN_ID:
        await callback.answer("Sizda bu huquq yo'q!", show_alert=True)
        return
    await callback.message.answer("Iltimos, YouTube video havolasini yuboring (bekor qilish: /cancel):")
    await state.set_state(AdminStates.waiting_for_video_url)
    await callback.answer()


@dp.message(AdminStates.waiting_for_video_url)
async def receive_video_url(message: types.Message, state: FSMContext):
    if message.from_user.id != ADMIN_ID:
        return
    url = (message.text or "").strip()
    if not store.extract_youtube_id(url):
        await message.answer("❌ Bu to'g'ri YouTube havolasi emas. Qayta yuboring yoki /cancel.")
        return
    await state.update_data(video_url=url)
    await message.answer("Endi video nomini (sarlavhasini) yuboring:")
    await state.set_state(AdminStates.waiting_for_video_title)


@dp.message(AdminStates.waiting_for_video_title)
async def receive_video_title(message: types.Message, state: FSMContext):
    if message.from_user.id != ADMIN_ID:
        return
    title = (message.text or "").strip()
    if not title:
        await message.answer("Sarlavha matn bo'lishi kerak. Qayta yuboring yoki /cancel.")
        return
    data = await state.get_data()
    store.add_video(data["video_url"], title)
    await message.answer("✅ Video bazaga muvaffaqiyatli qo'shildi va random tanlovga tayyor!")
    await state.clear()


# ======================= Mini App serveri =======================

class ApiError(Exception):
    def __init__(self, status, msg):
        self.status, self.msg = status, msg


def verify_init_data(init_data: str, token: str, max_age: int = 2 * 24 * 3600):
    """Telegram Mini App initData imzosini tekshiradi. To'g'ri bo'lsa user dict qaytaradi."""
    try:
        pairs = dict(parse_qsl(init_data, keep_blank_values=True, strict_parsing=True))
        received = pairs.pop("hash", None)
        if not received:
            return None
        check = "\n".join(f"{k}={v}" for k, v in sorted(pairs.items()))
        secret = hmac.new(b"WebAppData", token.encode(), hashlib.sha256).digest()
        calc = hmac.new(secret, check.encode(), hashlib.sha256).hexdigest()
        if not hmac.compare_digest(calc, received):
            return None
        if time.time() - int(pairs.get("auth_date", "0")) > max_age:
            return None
        return json.loads(pairs["user"])
    except (ValueError, KeyError, TypeError):
        return None


async def read_auth(request: web.Request):
    try:
        body = await request.json()
        if not isinstance(body, dict):
            raise ValueError
    except Exception:
        raise ApiError(400, "Noto'g'ri so'rov")
    user = verify_init_data(str(body.get("initData", "")), request.app["token"])
    if not user or "id" not in user:
        raise ApiError(401, "Ilovani Telegram ichidan oching (avtorizatsiya xatosi)")
    fullname = " ".join(p for p in [user.get("first_name"), user.get("last_name")] if p)
    store.create_user(user["id"], user.get("username"), fullname)
    return user["id"], body


@web.middleware
async def error_middleware(request, handler):
    try:
        return await handler(request)
    except ApiError as e:
        return web.json_response({"error": e.msg}, status=e.status)
    except web.HTTPException:
        raise
    except Exception:
        logging.exception("Server xatosi")
        return web.json_response({"error": "Server xatosi"}, status=500)


async def api_me(request):
    uid, _ = await read_auth(request)
    u = store.get_user(uid)
    total, active = store.friends_stats(uid)
    return web.json_response({
        "balance": u["balance_micro"] / store.MICRO,
        "tier": u["tier"],
        "friends_total": total,
        "friends_active": active,
        "ref_link": f"https://t.me/{request.app['bot_username']}?start=ref_{uid}",
    })


async def api_start(request):
    uid, _ = await read_auth(request)
    res = store.start_session(uid)
    if not res:
        raise ApiError(404, "Hozircha videolar yo'q. Keyinroq urinib ko'ring.")
    v = res["video"]
    return web.json_response({
        "session_id": res["session_id"],
        "video": {"youtube_id": v["youtube_id"], "title": v["title"]},
    })


async def api_beat(request):
    uid, body = await read_auth(request)
    res = store.beat_session(uid, str(body.get("session_id", "")), body.get("playing") is True)
    if res is None:
        raise ApiError(409, "Sessiya tugagan")
    return web.json_response(res)


async def api_stop(request):
    uid, body = await read_auth(request)
    res = store.finish_session(uid, str(body.get("session_id", "")))
    if res is None:
        raise ApiError(404, "Sessiya topilmadi")
    return web.json_response(res)


def static_file(name):
    async def handler(request):
        return web.FileResponse(os.path.join(BASE_DIR, name), headers={"Cache-Control": "no-cache"})
    return handler


def make_app(token: str, bot_username: str) -> web.Application:
    app = web.Application(middlewares=[error_middleware])
    app["token"] = token
    app["bot_username"] = bot_username
    app.router.add_get("/", static_file("index.html"))
    app.router.add_get("/app.js", static_file("app.js"))
    app.router.add_post("/api/me", api_me)
    app.router.add_post("/api/start", api_start)
    app.router.add_post("/api/beat", api_beat)
    app.router.add_post("/api/stop", api_stop)
    return app


async def main():
    if not BOT_TOKEN:
        sys.exit("BOT_TOKEN topilmadi. .env faylga BOT_TOKEN=... yozing (BotFather'dan oling).")
    if not WEBAPP_URL.startswith("https://"):
        sys.exit("WEBAPP_URL https:// bilan boshlanishi kerak (masalan ngrok yoki cloudflared manzili).")

    store.init_db()
    bot = Bot(token=BOT_TOKEN)
    me = await bot.get_me()

    runner = web.AppRunner(make_app(BOT_TOKEN, me.username))
    await runner.setup()
    await web.TCPSite(runner, "0.0.0.0", PORT).start()
    logging.info("Mini App serveri: http://0.0.0.0:%s  |  Bot: @%s", PORT, me.username)

    try:
        await dp.start_polling(bot)
    finally:
        await runner.cleanup()
        await bot.session.close()


if __name__ == "__main__":
    logging.basicConfig(level=logging.INFO)
    asyncio.run(main())
