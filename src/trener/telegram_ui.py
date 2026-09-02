"""Telegram handlery – tenká vrstva nad Trainer (app.py). Žiadne skryté režimy:
číslo je vždy hlásenie klikov, nastavenia sa menia len príkazmi s argumentom."""
from __future__ import annotations

import logging

from telegram import InlineKeyboardButton, InlineKeyboardMarkup, Update
from telegram.error import BadRequest, Conflict, NetworkError, TimedOut
from telegram.ext import (Application, CallbackQueryHandler, CommandHandler, ContextTypes,
                          MessageHandler, filters)

from trener import messages as M
from trener.model import EVENING, MORNING, parse_hhmm
from trener.parsing import normalize

log = logging.getLogger("trener.tg")


def keyboard(frozen: bool) -> InlineKeyboardMarkup:
    first = InlineKeyboardButton("▶️ Odmraziť", callback_data="unfreeze") if frozen \
        else InlineKeyboardButton("❄️ Zmraziť", callback_data="freeze")
    return InlineKeyboardMarkup([[first, InlineKeyboardButton("🎯 Cieľ", callback_data="goalpick")],
                                 [InlineKeyboardButton("🔄 Sync", callback_data="sync"),
                                  InlineKeyboardButton("📊 Stav", callback_data="status")]])


def report_keyboard(can_undo: bool, can_goal: bool) -> InlineKeyboardMarkup | None:
    row = []
    if can_undo:
        row.append(InlineKeyboardButton("↩️ Vrátiť", callback_data="undo"))
    if can_goal:
        row.append(InlineKeyboardButton("🎯 Bol to cieľ, nie kliky", callback_data="asgoal"))
    return InlineKeyboardMarkup([row]) if row else None


GOAL_PRESETS = (6, 8, 10, 12, 14, 16, 20, 25, 30, 40)


def goal_keyboard(current: int) -> InlineKeyboardMarkup:
    vals = sorted(set(GOAL_PRESETS) | {max(current - 2, 1), current, current + 2})
    rows, row = [], []
    for v in vals:
        row.append(InlineKeyboardButton(f"{'✅ ' if v == current else ''}{v}", callback_data=f"goal:{v}"))
        if len(row) == 5:
            rows.append(row); row = []
    if row:
        rows.append(row)
    rows.append([InlineKeyboardButton("📊 Stav", callback_data="status")])
    return InlineKeyboardMarkup(rows)


def register(app: Application, trainer, owner_chat_id: int) -> None:
    # len nové správy od majiteľa – editované správy (UpdateType.EDITED_MESSAGE) ignorujeme,
    # inak by opravené číslo prišlo druhýkrát a update.message by bolo None
    own = filters.Chat(chat_id=owner_chat_id) & filters.UpdateType.MESSAGE

    async def start(update: Update, _: ContextTypes.DEFAULT_TYPE) -> None:
        await update.message.reply_text(M.HELLO)

    async def help_(update: Update, _: ContextTypes.DEFAULT_TYPE) -> None:
        s = trainer.settings()
        await update.message.reply_text(M.HELP.format(nag_max=s.nag_max, interval=s.nag_interval_min))

    async def stav(update: Update, _: ContextTypes.DEFAULT_TYPE) -> None:
        text = await trainer.status_text()
        await update.message.reply_text(text, reply_markup=keyboard(trainer.settings().frozen))

    async def zmraz(update: Update, _: ContextTypes.DEFAULT_TYPE) -> None:
        await update.message.reply_text(await trainer.set_frozen(True), reply_markup=keyboard(True))

    async def odmraz(update: Update, _: ContextTypes.DEFAULT_TYPE) -> None:
        await update.message.reply_text(await trainer.set_frozen(False), reply_markup=keyboard(False))

    async def ciel(update: Update, ctx: ContextTypes.DEFAULT_TYPE) -> None:
        if not ctx.args:
            day = trainer.today_day()
            await update.message.reply_text(trainer.goal_prompt(), reply_markup=goal_keyboard(day.goal))
            return
        n = _int_arg(ctx)
        if n is None:
            await update.message.reply_text(M.BAD_INT.format(cmd="ciel"))
            return
        await update.message.reply_text(await trainer.set_goal(n), reply_markup=keyboard(trainer.settings().frozen))

    async def prirastok(update: Update, ctx: ContextTypes.DEFAULT_TYPE) -> None:
        n = _int_arg(ctx)
        if n is None:
            await update.message.reply_text(M.BAD_INT.format(cmd="prirastok"))
            return
        await update.message.reply_text(await trainer.set_increment(n))

    async def rano(update: Update, ctx: ContextTypes.DEFAULT_TYPE) -> None:
        t = parse_hhmm(" ".join(ctx.args)) if ctx.args else None
        if t is None:
            await update.message.reply_text(M.BAD_TIME.format(cmd="rano"))
            return
        await update.message.reply_text(await trainer.set_time(MORNING, t))

    async def vecer(update: Update, ctx: ContextTypes.DEFAULT_TYPE) -> None:
        t = parse_hhmm(" ".join(ctx.args)) if ctx.args else None
        if t is None:
            await update.message.reply_text(M.BAD_TIME.format(cmd="vecer"))
            return
        await update.message.reply_text(await trainer.set_time(EVENING, t))

    async def oprav(update: Update, ctx: ContextTypes.DEFAULT_TYPE) -> None:
        args = [normalize(a) for a in ctx.args]
        if len(args) != 2 or not args[1].isdigit() or int(args[1]) > 100000:
            await update.message.reply_text(M.BAD_FIX)
            return
        if args[0] in ("rano", "r"):
            session = MORNING
        elif args[0] in ("vecer", "v"):
            session = EVENING
        else:
            await update.message.reply_text(M.BAD_FIX)
            return
        await update.message.reply_text(await trainer.fix(session, int(args[1])))

    async def tabulka(update: Update, _: ContextTypes.DEFAULT_TYPE) -> None:
        await update.message.reply_text(await trainer.table_info())

    async def sync(update: Update, _: ContextTypes.DEFAULT_TYPE) -> None:
        await update.message.reply_text(await trainer.force_sync())

    async def stats(update: Update, _: ContextTypes.DEFAULT_TYPE) -> None:
        await update.message.reply_text(await trainer.stats_text())

    async def unknown(update: Update, _: ContextTypes.DEFAULT_TYPE) -> None:
        await update.message.reply_text("Taký príkaz nepoznám. /help")

    async def on_text(update: Update, _: ContextTypes.DEFAULT_TYPE) -> None:
        text = update.message.text or ""
        log.info("Správa: %r", text)
        reply = await trainer.handle_text(text, update.message.date)
        can_undo, can_goal = trainer.undo_available()
        await update.message.reply_text(reply, reply_markup=report_keyboard(can_undo, can_goal)
                                        if reply.startswith("✅") else None)

    async def on_callback(update: Update, _: ContextTypes.DEFAULT_TYPE) -> None:
        q = update.callback_query
        if q is None:
            return
        if update.effective_user is None or update.effective_user.id != owner_chat_id:
            await q.answer()
            return
        data = q.data or ""
        markup = None
        if data == "freeze":
            text = await trainer.set_frozen(True)
        elif data == "unfreeze":
            text = await trainer.set_frozen(False)
        elif data == "sync":
            text = await trainer.force_sync()
        elif data == "undo":
            text = await trainer.undo_last(as_goal=False)
        elif data == "asgoal":
            text = await trainer.undo_last(as_goal=True)
        elif data == "goalpick":
            text = trainer.goal_prompt()
            markup = goal_keyboard(trainer.today_day().goal)
        elif data.startswith("goal:") and data[5:].isdigit():
            text = await trainer.set_goal(int(data[5:]))
        else:
            text = await trainer.status_text()
        if markup is None:
            markup = keyboard(trainer.settings().frozen)
        await q.answer()
        try:
            await q.edit_message_text(text, reply_markup=markup)
        except BadRequest as e:
            if "not modified" in str(e).lower():
                return                      # rovnaký text – nič netreba
            if q.message is not None:
                await q.message.reply_text(text, reply_markup=markup)

    async def on_error(update: object, ctx: ContextTypes.DEFAULT_TYPE) -> None:
        err = ctx.error
        if isinstance(err, Conflict):
            log.error("Telegram Conflict: s týmto tokenom polluje ešte iná inštancia bota! (%s)", err)
            return
        if isinstance(err, BadRequest):
            log.error("Telegram BadRequest: %s", err, exc_info=err)
            return
        if isinstance(err, (NetworkError, TimedOut)):
            log.warning("Telegram sieť: %s", err)   # PTB to sám skúsi znova – bez stack trace
            return
        log.error("Neošetrená chyba pri spracovaní updatu.", exc_info=err)

    app.add_error_handler(on_error)
    for name, fn in (("start", start), ("help", help_), ("stav", stav), ("zmraz", zmraz),
                     ("odmraz", odmraz), ("ciel", ciel), ("prirastok", prirastok), ("rano", rano),
                     ("vecer", vecer), ("oprav", oprav), ("tabulka", tabulka), ("sync", sync),
                     ("stats", stats)):
        app.add_handler(CommandHandler(name, fn, filters=own))
    app.add_handler(CallbackQueryHandler(on_callback))
    app.add_handler(MessageHandler(own & filters.COMMAND, unknown))
    app.add_handler(MessageHandler(own & filters.TEXT & ~filters.COMMAND, on_text))


def _int_arg(ctx: ContextTypes.DEFAULT_TYPE) -> int | None:
    if not ctx.args:
        return None
    a = ctx.args[0].strip()
    if not a.isdigit():
        return None
    n = int(a)
    return n if n <= 100000 else None


BOT_COMMANDS = [
    ("stav", "dnešok, streak, tabuľka, pripomienky"),
    ("zmraz", "pauza – nič nepripomínať"),
    ("odmraz", "pokračovať"),
    ("ciel", "dnešný cieľ – tlačidlá alebo /ciel 10"),
    ("prirastok", "rast cieľa po splnenom dni"),
    ("rano", "ranný čas HH:MM"),
    ("vecer", "večerný čas HH:MM"),
    ("oprav", "/oprav ráno 5 – nastaví presnú hodnotu"),
    ("tabulka", "kde je tabuľka a čo je v nej"),
    ("sync", "načítať tabuľku a pripomienky hneď"),
    ("stats", "štatistika"),
    ("help", "nápoveda"),
]
