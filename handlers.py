import asyncio
import io
import logging
import re
import time
from datetime import datetime, timezone
from html import escape

from aiogram import Bot, F, Router
from aiogram.exceptions import TelegramBadRequest
from aiogram.filters import Command, CommandStart, StateFilter
from aiogram.fsm.context import FSMContext
from aiogram.fsm.state import State, StatesGroup
from aiogram.fsm.storage.base import StorageKey
from aiogram.types import CallbackQuery, Message
from aiogram.utils.keyboard import InlineKeyboardBuilder

import config
import db
import scoring
from elo import elo_step
from matchmaking import Entry, matchmaker
from ui import back_kb, format_top, main_kb, player_label, show, top_menu_kb, top_page_kb

log = logging.getLogger(__name__)
router = Router()

NAME_RE = re.compile(r"^[\w .'\-]{2,40}$")
STUDENT_ID_RE = re.compile(r"^[A-Za-z0-9\-/]{4,20}$")
_locks: dict[int, asyncio.Lock] = {}  # one lock per user: albums arrive as parallel updates


class Reg(StatesGroup):
    name = State()
    student_id = State()


class Battle(StatesGroup):
    photos = State()
    waiting = State()


RULES_TEXT = (
    "<b>📜 Правила</b>\n\n"
    f"• Старт — <b>{config.START_ELO}</b> эло. Победа/поражение: ±{config.ELO_STEP}, "
    f"а если разрыв в оценках больше {config.BIG_GAP} — ±{config.ELO_STEP_BIG}.\n"
    "• Эло не падает ниже 0.\n"
    f"• Для батла нужно {config.PHOTOS_PER_BATTLE} фото лица: анфас, при хорошем свете, "
    "без очков и масок, только ты в кадре.\n"
    "• Оценка (score) считается по пропорциям и симметрии лица. Это не настоящий PSL, "
    "а наша внутренняя метрика.\n"
    "• Соперник подбирается по эло: сначала ±200, каждые 30 секунд диапазон шире на 100.\n"
    "• Фото нигде не сохраняются: бот считает оценку и сразу забывает снимок.\n"
    "• Топ дня и недели — кто сильнее поднял эло за период."
)


def _is_admin(user_id: int) -> bool:
    return user_id == config.ADMIN_ID


async def _try_delete(message: Message) -> None:
    try:
        await message.delete()
    except TelegramBadRequest:
        pass


async def show_menu(event: Message | CallbackQuery) -> None:
    await show(event, "main", "<b>Mog Battle</b>\nГлавное меню. Выбирай.",
               main_kb(_is_admin(event.from_user.id)))


async def _registered(cb: CallbackQuery):
    player = await db.get_player(cb.from_user.id)
    if player is None:
        await cb.answer("Сначала нажми /start и зарегистрируйся", show_alert=True)
    return player


def _cancel_kb():
    return back_kb("menu", "❌ Отмена")


# =================== START / REGISTRATION ===================
@router.message(CommandStart())
async def cmd_start(message: Message, state: FSMContext):
    matchmaker.remove(message.from_user.id)
    await state.clear()
    if await db.get_player(message.from_user.id):
        await show_menu(message)
        return
    await show(message, "welcome",
               "<b>Mog Battle колледжа</b> ⚔️\n\nРегистрация. Как тебя зовут? "
               "Напиши имя и фамилию — они появятся в топе.")
    await state.set_state(Reg.name)


@router.message(Reg.name, F.text, ~F.text.startswith("/"))
async def reg_name(message: Message, state: FSMContext):
    name = " ".join(message.text.split())
    if not NAME_RE.match(name):
        await message.answer("Имя от 2 до 40 символов: буквы, цифры, пробел, точка, дефис. Попробуй ещё раз.")
        return
    await state.update_data(name=name)
    await state.set_state(Reg.student_id)
    await message.answer("Теперь номер студенческого (ID-карты). Он нужен только чтобы не было дублей "
                         "и нигде не показывается.")


@router.message(Reg.student_id, F.text, ~F.text.startswith("/"))
async def reg_student_id(message: Message, state: FSMContext):
    student_id = message.text.strip().upper()
    if not STUDENT_ID_RE.match(student_id):
        await message.answer("Номер: 4–20 символов, только латиница, цифры, «-» и «/». Попробуй ещё раз.")
        return
    data = await state.get_data()
    created = await db.create_player(message.from_user.id, student_id, data["name"])
    await _try_delete(message)  # keep the number out of the chat history
    if not created:
        await message.answer("Этот номер уже зарегистрирован. Если это твой номер — напиши админу.")
        return
    await state.clear()
    await message.answer(f"✅ Готово, {escape(data['name'])}! Ты в игре, старт — {config.START_ELO} эло.")
    await show_menu(message)


@router.message(Command("menu"))
async def cmd_menu(message: Message, state: FSMContext):
    matchmaker.remove(message.from_user.id)
    await state.clear()
    if await db.get_player(message.from_user.id) is None:
        await message.answer("Сначала нажми /start и зарегистрируйся.")
        return
    await show_menu(message)


@router.callback_query(F.data == "menu")
async def cb_menu(cb: CallbackQuery, state: FSMContext):
    matchmaker.remove(cb.from_user.id)  # leaving the menu while searching cancels the search
    await state.clear()
    await show_menu(cb)
    await cb.answer()


# =================== PROFILE / RULES / TOPS ===================
@router.callback_query(F.data == "profile")
async def cb_profile(cb: CallbackQuery):
    p = await _registered(cb)
    if p is None:
        return
    rank = await db.rank_of(p["elo"])
    text = (f"<b>👤 {escape(p['name'])}</b>\n"
            f"Telegram ID: <code>{p['tg_id']}</code>\n"
            f"Студенческий: <code>{escape(p['student_id'])}</code>\n\n"
            f"⭐ Эло: <b>{p['elo']}</b>\n"
            f"🏅 Место в общем рейтинге: <b>#{rank}</b>\n"
            f"✅ Победы: {p['wins']}   ❌ Поражения: {p['losses']}")
    await show(cb, "profile", text, back_kb())
    await cb.answer()


@router.callback_query(F.data == "rules")
async def cb_rules(cb: CallbackQuery):
    await show(cb, "rules", RULES_TEXT, back_kb())
    await cb.answer()


@router.callback_query(F.data == "top")
async def cb_top_menu(cb: CallbackQuery):
    await show(cb, "top", "<b>🏆 Топы</b>\nВыбери период.", top_menu_kb())
    await cb.answer()


@router.callback_query(F.data.regexp(r"^top:(day|week|all):\d+$"))
async def cb_top(cb: CallbackQuery):
    _, kind, page = cb.data.split(":")
    now = datetime.now(timezone.utc)
    if kind == "all":
        rows = await db.top_alltime(100)
        text, pages = format_top("👑 Топ за всё время", rows, int(page), "elo", "elo")
    elif kind == "day":
        rows = await db.top_period(db.day_start(now), 100)
        text, pages = format_top("📅 Топ дня (прирост эло)", rows, int(page), "gain", "gain")
    else:
        rows = await db.top_period(db.week_start(now), 100)
        text, pages = format_top("📆 Топ недели (прирост эло)", rows, int(page), "gain", "gain")
    await show(cb, None, text, top_page_kb(kind, min(int(page), pages - 1), pages))
    await cb.answer()


# =================== BATTLE ===================
def _photo_prompt(n: int) -> str:
    return (f"Пришли фото <b>{n} из {config.PHOTOS_PER_BATTLE}</b>.\n"
            "Лицо анфас, хороший свет, без очков и масок, только ты. Отправляй как обычное фото, не файлом.")


@router.callback_query(F.data == "battle")
async def cb_battle(cb: CallbackQuery, state: FSMContext):
    if await _registered(cb) is None:
        return
    if cb.from_user.id in matchmaker:
        await cb.answer("Ты уже в поиске соперника", show_alert=True)
        return
    await state.set_state(Battle.photos)
    await state.update_data(scores=[], seen=[])
    await show(cb, "battle", "<b>⚔️ Батл</b>\n\n" + _photo_prompt(1), _cancel_kb())
    await cb.answer()


@router.message(Battle.photos, F.photo)
async def battle_photo(message: Message, state: FSMContext):
    uid = message.from_user.id
    async with _locks.setdefault(uid, asyncio.Lock()):
        if await state.get_state() != Battle.photos.state:
            return  # extra photos from an album after we already have enough
        data = await state.get_data()
        scores: list[float] = data.get("scores", [])
        seen: list[str] = data.get("seen", [])
        n = len(scores) + 1

        photo = message.photo[-1]
        if photo.file_unique_id in seen:
            await message.answer("Это то же самое фото. Пришли другое.")
            return

        buf = io.BytesIO()
        await message.bot.download(photo, destination=buf)  # memory only, no file on disk
        try:
            score = await scoring.score_photo_async(buf.getvalue())
        except scoring.PhotoError as e:
            await message.answer(f"❌ {e}\n\nПришли фото {n} из {config.PHOTOS_PER_BATTLE} заново.",
                                 reply_markup=_cancel_kb())
            await _try_delete(message)
            return
        except Exception:
            log.exception("photo scoring failed")
            await message.answer("Не получилось обработать фото. Попробуй другое.", reply_markup=_cancel_kb())
            return
        finally:
            buf.close()

        scores.append(score)
        seen.append(photo.file_unique_id)
        await state.update_data(scores=scores, seen=seen)
        await _try_delete(message)

        if len(scores) < config.PHOTOS_PER_BATTLE:
            await message.answer(f"✅ Фото {len(scores)} принято.\n\n" + _photo_prompt(len(scores) + 1),
                                 reply_markup=_cancel_kb())
            return

        player = await db.get_player(uid)
        if player is None:
            await state.clear()
            await message.answer("Тебя нет в рейтинге. Нажми /start.")
            return
        final = scoring.average_score(scores)
        await state.set_state(Battle.waiting)
        matchmaker.add(Entry(uid, message.chat.id, player["name"], player["elo"], final, time.monotonic()))
        await message.answer(
            "✅ Все фото приняты.\n🔎 Ищу соперника по эло (±200, каждые 30 секунд шире на 100). "
            "Результат пришлю сюда.",
            reply_markup=back_kb("menu", "❌ Отменить поиск"))


@router.message(Battle.photos)
async def battle_not_photo(message: Message):
    await message.answer("Нужно именно фото (не файлом и не текстом).", reply_markup=_cancel_kb())


@router.message(Battle.waiting)
async def battle_waiting(message: Message):
    await message.answer("Ищу соперника… Подожди или нажми «Отменить поиск» выше.")


def _after_battle_kb():
    kb = InlineKeyboardBuilder()
    kb.button(text="⚔️ Ещё батл", callback_data="battle")
    kb.button(text="‹ Меню", callback_data="menu")
    kb.adjust(1)
    return kb.as_markup()


def make_callbacks(bot: Bot, storage):
    """Callbacks for the matchmaking loop (they need the bot and the FSM storage)."""

    async def release(uid: int) -> None:
        key = StorageKey(bot_id=bot.id, chat_id=uid, user_id=uid)
        await FSMContext(storage=storage, key=key).clear()

    async def send(entry: Entry, text: str) -> None:
        await bot.send_message(entry.chat_id, text, reply_markup=_after_battle_kb())

    async def on_match(a: Entry, b: Entry) -> None:
        await release(a.tg_id)
        await release(b.tg_id)

        if a.score == b.score:
            for me, other in ((a, b), (b, a)):
                await _quiet(send(me, f"⚔️ <b>Батл завершён</b>\n\nТы: <b>{me.score}</b>\n"
                                      f"Соперник: {player_label(other.name, other.tg_id)} — <b>{other.score}</b>\n\n"
                                      "🤝 Ничья, эло не меняется."))
            return

        step = elo_step(a.score, b.score)
        win, lose = (a, b) if a.score > b.score else (b, a)
        result = await db.apply_result(win.tg_id, lose.tg_id, step, step)
        if result is None:
            for e in (a, b):
                await _quiet(send(e, "Соперник вышел из рейтинга, батл отменён."))
            return
        new_win, new_lose, real_lose = result

        head = "⚔️ <b>Батл завершён</b>\n\n"
        await _quiet(send(win, head + f"Ты: <b>{win.score}</b>\n"
                                      f"Соперник: {player_label(lose.name, lose.tg_id)} — <b>{lose.score}</b>\n\n"
                                      f"🏆 Победа! <b>+{step}</b> эло → <b>{new_win}</b>"))
        await _quiet(send(lose, head + f"Ты: <b>{lose.score}</b>\n"
                                       f"Соперник: {player_label(win.name, win.tg_id)} — <b>{win.score}</b>\n\n"
                                       f"💀 Поражение. <b>{real_lose}</b> эло → <b>{new_lose}</b>"))

    async def on_timeout(e: Entry) -> None:
        await release(e.tg_id)
        await _quiet(send(e, "😕 За 5 минут соперника в твоём диапазоне не нашлось. "
                             "Попробуй позже, когда в боте будет больше людей."))

    return on_match, on_timeout


async def _quiet(coro) -> None:
    """One failed message (e.g. the user blocked the bot) must not break the rest."""
    try:
        await coro
    except Exception:
        log.exception("failed to send battle message")


# =================== FALLBACK ===================
@router.message(StateFilter(None))
async def fallback(message: Message):
    await message.answer("Открой меню: /menu")
