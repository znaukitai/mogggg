import asyncio
import logging
from html import escape

from aiogram import F, Router
from aiogram.filters import Command
from aiogram.fsm.context import FSMContext
from aiogram.fsm.state import State, StatesGroup
from aiogram.types import CallbackQuery, Message
from aiogram.utils.keyboard import InlineKeyboardBuilder

import config
import db
from matchmaking import matchmaker
from ui import back_kb, show

log = logging.getLogger(__name__)
router = Router()
# every handler in this router works only for the single admin
router.message.filter(F.from_user.id == config.ADMIN_ID)
router.callback_query.filter(F.from_user.id == config.ADMIN_ID)


class Adm(StatesGroup):
    find = State()
    delete = State()
    set_elo = State()
    broadcast = State()


def panel_kb():
    kb = InlineKeyboardBuilder()
    kb.button(text="📊 Статистика", callback_data="adm_stats")
    kb.button(text="🔎 Найти игрока", callback_data="adm_find")
    kb.button(text="🗑 Удалить игрока", callback_data="adm_del")
    kb.button(text="✏️ Изменить эло", callback_data="adm_elo")
    kb.button(text="📢 Рассылка", callback_data="adm_bc")
    kb.button(text="‹ В меню", callback_data="menu")
    kb.adjust(2)
    return kb.as_markup()


def _panel_back():
    return back_kb("adm", "‹ В админку")


def _row(p) -> str:
    return (f"<b>{escape(p['name'])}</b> · ID <code>{p['tg_id']}</code>\n"
            f"студенческий <code>{escape(p['student_id'])}</code> · эло {p['elo']} · "
            f"{p['wins']}W/{p['losses']}L")


async def _prompt(cb: CallbackQuery, state: FSMContext, new_state: State, text: str) -> None:
    await state.set_state(new_state)
    await show(cb, "admin", text, _panel_back())
    await cb.answer()


@router.message(Command("admin"))
async def cmd_admin(message: Message, state: FSMContext):
    await state.clear()
    await show(message, "admin", "<b>🛠 Админка</b>", panel_kb())


@router.callback_query(F.data == "adm")
async def cb_panel(cb: CallbackQuery, state: FSMContext):
    await state.clear()
    await show(cb, "admin", "<b>🛠 Админка</b>", panel_kb())
    await cb.answer()


@router.callback_query(F.data == "adm_stats")
async def cb_stats(cb: CallbackQuery):
    s = await db.stats()
    text = (f"<b>📊 Статистика</b>\n\nИгроков: <b>{s['players']}</b>\n"
            f"Батлов сыграно: <b>{s['battles']}</b>\n"
            f"Максимальное эло: <b>{s['max_elo']}</b>\n"
            f"Сейчас в поиске: <b>{len(matchmaker.queue)}</b>")
    await show(cb, "admin", text, _panel_back())
    await cb.answer()


# ---- find ----
@router.callback_query(F.data == "adm_find")
async def cb_find(cb: CallbackQuery, state: FSMContext):
    await _prompt(cb, state, Adm.find, "Пришли Telegram ID, студенческий номер или часть имени.")


@router.message(Adm.find, F.text, ~F.text.startswith("/"))
async def find_input(message: Message, state: FSMContext):
    rows = await db.find_players(message.text.strip())
    text = "\n\n".join(_row(p) for p in rows) if rows else "Никого не нашёл."
    await state.clear()
    await message.answer(text, reply_markup=panel_kb())


# ---- delete ----
@router.callback_query(F.data == "adm_del")
async def cb_del(cb: CallbackQuery, state: FSMContext):
    await _prompt(cb, state, Adm.delete, "Пришли Telegram ID игрока, которого удалить.")


@router.message(Adm.delete, F.text, ~F.text.startswith("/"))
async def del_input(message: Message, state: FSMContext):
    raw = message.text.strip()
    player = await db.get_player(int(raw)) if raw.isdigit() else None
    if player is None:
        await message.answer("Игрока с таким Telegram ID нет. Пришли ID ещё раз.", reply_markup=_panel_back())
        return
    await state.clear()
    kb = InlineKeyboardBuilder()
    kb.button(text="✅ Да, удалить", callback_data=f"adm_del_yes:{player['tg_id']}")
    kb.button(text="Отмена", callback_data="adm")
    kb.adjust(1)
    await message.answer("Удалить игрока?\n\n" + _row(player) + "\n\nИстория эло тоже удалится.",
                         reply_markup=kb.as_markup())


@router.callback_query(F.data.regexp(r"^adm_del_yes:\d+$"))
async def cb_del_yes(cb: CallbackQuery):
    tg_id = int(cb.data.split(":")[1])
    matchmaker.remove(tg_id)
    ok = await db.delete_player(tg_id)
    await show(cb, "admin", "🗑 Удалён." if ok else "Игрок уже удалён.", panel_kb())
    await cb.answer()


# ---- manual elo ----
@router.callback_query(F.data == "adm_elo")
async def cb_elo(cb: CallbackQuery, state: FSMContext):
    await _prompt(cb, state, Adm.set_elo, "Пришли «Telegram ID и новое эло», например: <code>123456789 1200</code>")


@router.message(Adm.set_elo, F.text, ~F.text.startswith("/"))
async def elo_input(message: Message, state: FSMContext):
    parts = message.text.split()
    if len(parts) != 2 or not parts[0].isdigit() or not parts[1].isdigit():
        await message.answer("Формат: <code>ID число</code>, например <code>123456789 1200</code>",
                             reply_markup=_panel_back())
        return
    tg_id, elo = int(parts[0]), int(parts[1])
    await state.clear()
    if not await db.set_elo(tg_id, elo):
        await message.answer("Игрока с таким ID нет.", reply_markup=panel_kb())
        return
    if tg_id in matchmaker.queue:
        matchmaker.queue[tg_id].elo = elo  # keep matchmaking in sync
    await message.answer(f"✅ Эло игрока <code>{tg_id}</code> теперь {elo}.", reply_markup=panel_kb())


# ---- broadcast ----
@router.callback_query(F.data == "adm_bc")
async def cb_bc(cb: CallbackQuery, state: FSMContext):
    await _prompt(cb, state, Adm.broadcast, "Пришли текст рассылки (получат все зарегистрированные игроки).")


@router.message(Adm.broadcast, F.text, ~F.text.startswith("/"))
async def bc_input(message: Message, state: FSMContext):
    await state.update_data(text=message.text)
    kb = InlineKeyboardBuilder()
    kb.button(text="📢 Отправить всем", callback_data="adm_bc_yes")
    kb.button(text="Отмена", callback_data="adm")
    kb.adjust(1)
    await message.answer("Предпросмотр:\n\n" + escape(message.text), reply_markup=kb.as_markup())


@router.callback_query(F.data == "adm_bc_yes", Adm.broadcast)
async def cb_bc_yes(cb: CallbackQuery, state: FSMContext):
    text = (await state.get_data()).get("text")
    await state.clear()
    if not text:
        await cb.answer("Текста нет", show_alert=True)
        return
    await cb.answer("Отправляю…")
    ids = await db.all_ids()
    sent = 0
    for uid in ids:
        try:
            await cb.bot.send_message(uid, escape(text))
            sent += 1
        except Exception:  # blocked the bot, deleted chat, etc.
            log.info("broadcast: cannot deliver to %s", uid)
        await asyncio.sleep(0.05)  # stay under Telegram's flood limits
    await cb.message.answer(f"📢 Доставлено {sent} из {len(ids)}.", reply_markup=panel_kb())
