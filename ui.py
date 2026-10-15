"""Shared UI: single-window screens with a photo header (same style as the old bot)."""
import logging
from html import escape

from aiogram.exceptions import TelegramBadRequest
from aiogram.types import CallbackQuery, InlineKeyboardMarkup, InputMediaPhoto, Message
from aiogram.utils.keyboard import InlineKeyboardBuilder

import config

log = logging.getLogger(__name__)
_bad_photos: set[str] = set()  # keys whose picture Telegram rejected -> show text only

MEDALS = {1: "🥇", 2: "🥈", 3: "🥉"}
PAGE_SIZE = 20


def main_kb(is_admin: bool = False) -> InlineKeyboardMarkup:
    kb = InlineKeyboardBuilder()
    kb.button(text="⚔️ Батл", callback_data="battle")
    kb.button(text="🏆 Топ", callback_data="top")
    kb.button(text="👤 Профиль", callback_data="profile")
    kb.button(text="📜 Правила", callback_data="rules")
    if is_admin:
        kb.button(text="🛠 Админка", callback_data="adm")
    kb.adjust(2)
    return kb.as_markup()


def back_kb(data: str = "menu", text: str = "‹ Назад") -> InlineKeyboardMarkup:
    kb = InlineKeyboardBuilder()
    kb.button(text=text, callback_data=data)
    return kb.as_markup()


def top_menu_kb() -> InlineKeyboardMarkup:
    kb = InlineKeyboardBuilder()
    kb.button(text="📅 Топ дня", callback_data="top:day:0")
    kb.button(text="📆 Топ недели", callback_data="top:week:0")
    kb.button(text="👑 За всё время", callback_data="top:all:0")
    kb.button(text="‹ Назад", callback_data="menu")
    kb.adjust(1)
    return kb.as_markup()


def top_page_kb(kind: str, page: int, pages: int) -> InlineKeyboardMarkup:
    kb = InlineKeyboardBuilder()
    nav = 0
    if page > 0:
        kb.button(text="‹", callback_data=f"top:{kind}:{page - 1}")
        nav += 1
    if page < pages - 1:
        kb.button(text="›", callback_data=f"top:{kind}:{page + 1}")
        nav += 1
    kb.button(text="‹ К топам", callback_data="top")
    if nav:
        kb.adjust(nav, 1)
    else:
        kb.adjust(1)
    return kb.as_markup()


def player_label(name: str, tg_id: int) -> str:
    return f"{escape(name)} · <code>{tg_id}</code>"


def format_top(title: str, rows, page: int, unit: str, value_key: str) -> tuple[str, int]:
    """Returns (text, pages). `rows` is the full top-100."""
    pages = max(1, -(-len(rows) // PAGE_SIZE))
    page = min(max(page, 0), pages - 1)
    lines = [f"<b>{title}</b>\n"]
    if not rows:
        lines.append("Пока никого нет.")
    start = page * PAGE_SIZE
    for i, r in enumerate(rows[start:start + PAGE_SIZE], start=start + 1):
        value = r[value_key]
        shown = f"+{value}" if unit == "gain" else str(value)
        lines.append(f"{MEDALS.get(i, f'{i}.')} {player_label(r['name'], r['tg_id'])} — <b>{shown}</b>")
    if pages > 1:
        lines.append(f"\nСтраница {page + 1} из {pages}")
    return "\n".join(lines), pages


async def show(event: CallbackQuery | Message, key: str | None, text: str,
               kb: InlineKeyboardMarkup | None = None) -> None:
    """Shows a screen in one window. `key` = picture from config.PHOTOS (None = text only).
    Falls back to plain text if the picture is missing or Telegram rejects it."""
    photo = "" if key is None or key in _bad_photos else config.PHOTOS.get(key, "")
    msg = event.message if isinstance(event, CallbackQuery) else None

    if msg is not None:
        try:
            if photo and msg.photo:
                await msg.edit_media(InputMediaPhoto(media=photo, caption=text), reply_markup=kb)
                return
            if not photo and not msg.photo:
                await msg.edit_text(text, reply_markup=kb)
                return
        except TelegramBadRequest as e:
            if "not modified" in str(e):
                return
            # anything else: drop the old message and send the screen again below
        try:
            await msg.delete()
        except TelegramBadRequest:
            pass
        target = msg
    else:
        target = event

    if photo:
        try:
            await target.answer_photo(photo, caption=text, reply_markup=kb)
            return
        except TelegramBadRequest:
            log.warning("Picture '%s' rejected by Telegram, using text", key)
            _bad_photos.add(key)
    await target.answer(text, reply_markup=kb)
