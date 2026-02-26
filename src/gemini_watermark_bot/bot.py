"""Telegram bot handlers."""

from __future__ import annotations

import asyncio
import datetime
import io
import logging
import os
import time
import zipfile
from collections import defaultdict

from PIL import Image
from telegram import (
    InlineKeyboardButton,
    InlineKeyboardMarkup,
    InlineQueryResultCachedDocument,
    InlineQueryResultsButton,
    Update,
)
from telegram.ext import (
    Application,
    CallbackQueryHandler,
    CommandHandler,
    ContextTypes,
    InlineQueryHandler,
    MessageHandler,
    filters,
)

from .config import ADMIN_ID, HISTORY_SIZE, MAX_IMAGES_PER_DAY
from .i18n import lang, t
from .watermark import remove_watermark

logger = logging.getLogger(__name__)

# Buffer media-group messages so we can process batches together.
_group_buffers: dict[str, list[Update]] = defaultdict(list)
_group_locks: dict[str, asyncio.Event] = {}

_IMAGE_EXTENSIONS = {".png", ".jpg", ".jpeg", ".gif", ".bmp", ".webp", ".tiff"}


# ---------------------------------------------------------------------------
# Helpers: rate limiting
# ---------------------------------------------------------------------------

def _check_rate_limit(context: ContextTypes.DEFAULT_TYPE, count: int = 1) -> tuple[bool, int]:
    """Check if user can process *count* more images today."""
    today = str(datetime.date.today())
    rate = context.user_data.get("rate", {})
    if rate.get("date") != today:
        rate = {"date": today, "count": 0}
        context.user_data["rate"] = rate

    remaining = MAX_IMAGES_PER_DAY - rate["count"]
    return (count <= remaining), remaining


def _increment_rate(context: ContextTypes.DEFAULT_TYPE, count: int = 1) -> None:
    """Record that *count* images were processed."""
    today = str(datetime.date.today())
    rate = context.user_data.get("rate", {})
    if rate.get("date") != today:
        rate = {"date": today, "count": 0}
    rate["count"] += count
    context.user_data["rate"] = rate


# ---------------------------------------------------------------------------
# Helpers: history
# ---------------------------------------------------------------------------

def _add_to_history(context: ContextTypes.DEFAULT_TYPE, entry: dict) -> None:
    """Prepend a processed-image record, capping at HISTORY_SIZE."""
    history = context.user_data.get("history", [])
    history.insert(0, entry)
    context.user_data["history"] = history[:HISTORY_SIZE]


def _get_history(context: ContextTypes.DEFAULT_TYPE) -> list[dict]:
    return context.user_data.get("history", [])


def _extract_original_name(message) -> str:
    if message.document and message.document.file_name:
        return message.document.file_name
    return "photo.jpg"


def _format_timestamp(ts: int) -> str:
    return datetime.datetime.fromtimestamp(ts).strftime("%Y-%m-%d %H:%M")


# ---------------------------------------------------------------------------
# Helpers: global stats (admin)
# ---------------------------------------------------------------------------

_STATS_DAYS_KEPT = 30


def _init_stats(context: ContextTypes.DEFAULT_TYPE) -> dict:
    """Ensure bot_data['stats'] exists with proper structure."""
    stats = context.bot_data.setdefault("stats", {})
    stats.setdefault("total_images", 0)
    stats.setdefault("users", set())
    stats.setdefault("user_counts", {})
    stats.setdefault("daily", {})
    return stats


def _record_usage(context: ContextTypes.DEFAULT_TYPE, user_id: int, count: int = 1) -> None:
    """Record *count* processed images in global stats."""
    stats = _init_stats(context)
    stats["total_images"] += count
    stats["users"].add(user_id)
    stats["user_counts"][user_id] = stats["user_counts"].get(user_id, 0) + count

    today = str(datetime.date.today())
    day = stats["daily"].setdefault(today, {"images": 0, "users": set()})
    day["images"] += count
    day["users"].add(user_id)

    # Prune entries older than _STATS_DAYS_KEPT.
    cutoff = str(datetime.date.today() - datetime.timedelta(days=_STATS_DAYS_KEPT))
    stats["daily"] = {d: v for d, v in stats["daily"].items() if d >= cutoff}


# ---------------------------------------------------------------------------
# Core processing
# ---------------------------------------------------------------------------

async def _process_and_reply(
    message,
    photo_file,
    context: ContextTypes.DEFAULT_TYPE,
    filename: str = "cleaned.png",
) -> dict | None:
    """Download, remove watermark, send photo preview + full-res document.

    Returns a history entry dict, or None on failure.
    """
    raw = await photo_file.download_as_bytearray()
    img = Image.open(io.BytesIO(raw))
    cleaned = remove_watermark(img)

    # Compressed JPEG preview
    photo_buf = io.BytesIO()
    cleaned.save(photo_buf, format="JPEG", quality=85)
    photo_buf.seek(0)

    # Full-res PNG document
    doc_buf = io.BytesIO()
    cleaned.save(doc_buf, format="PNG")
    doc_buf.seek(0)

    await message.reply_photo(
        photo=photo_buf,
        reply_to_message_id=message.message_id,
    )
    doc_msg = await message.reply_document(
        document=doc_buf,
        filename=filename,
        reply_to_message_id=message.message_id,
    )

    return {
        "file_id": doc_msg.document.file_id,
        "filename": filename,
        "timestamp": int(time.time()),
        "original_name": _extract_original_name(message),
    }


# ---------------------------------------------------------------------------
# Command handlers
# ---------------------------------------------------------------------------

async def start(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    await update.message.reply_text(t("welcome", lang(update)))


async def help_command(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    await update.message.reply_text(
        t("help", lang(update), limit=MAX_IMAGES_PER_DAY),
        parse_mode="MarkdownV2",
    )


async def history_command(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    lc = lang(update)
    history = _get_history(context)

    if not history:
        await update.message.reply_text(t("history_empty", lc))
        return

    buttons = []
    for i, entry in enumerate(history):
        label = entry.get("original_name", entry["filename"])
        if len(label) > 40:
            label = label[:37] + "..."
        buttons.append([
            InlineKeyboardButton(
                text=f"{i + 1}. {label}",
                callback_data=f"history:{i}",
            )
        ])

    await update.message.reply_text(
        t("history_title", lc, count=len(history)),
        reply_markup=InlineKeyboardMarkup(buttons),
    )


async def history_callback(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    query = update.callback_query
    await query.answer()

    _, idx_str = query.data.split(":", 1)
    idx = int(idx_str)

    history = _get_history(context)
    if idx < 0 or idx >= len(history):
        return

    entry = history[idx]
    await query.message.reply_document(
        document=entry["file_id"],
        filename=entry["filename"],
    )


async def stats_command(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """Show bot statistics (admin only)."""
    if not ADMIN_ID or update.effective_user.id != ADMIN_ID:
        return

    stats = _init_stats(context)
    today = str(datetime.date.today())

    total = stats["total_images"]
    unique = len(stats["users"])

    today_data = stats["daily"].get(today, {"images": 0, "users": set()})
    today_imgs = today_data["images"]
    today_users = len(today_data["users"])

    lines = [
        "\U0001f4ca Bot Statistics",
        "\u2500" * 20,
        f"Total images:  {total:,}",
        f"Unique users:  {unique:,}",
        f"Today:         {today_imgs} images, {today_users} users",
        "",
    ]

    # --- Last 7 days bar chart ---
    last_7 = []
    for i in range(6, -1, -1):
        d = datetime.date.today() - datetime.timedelta(days=i)
        ds = str(d)
        day_data = stats["daily"].get(ds, {"images": 0, "users": set()})
        last_7.append((d, day_data["images"]))

    max_val = max((v for _, v in last_7), default=0)
    if max_val > 0:
        lines.append("Last 7 days:")
        for d, count in last_7:
            bar_len = round(count / max_val * 8) if max_val else 0
            bar = "\u2588" * bar_len
            label = d.strftime("%b %d")
            lines.append(f"  {label}  {bar} {count}")
        lines.append("")

    # --- Top 10 users ---
    user_counts = stats.get("user_counts", {})
    if user_counts:
        top = sorted(user_counts.items(), key=lambda x: x[1], reverse=True)[:10]
        lines.append("Top users (all-time):")
        for rank, (uid, cnt) in enumerate(top, 1):
            lines.append(f"  {rank}. #{uid}  \u2192  {cnt:,} images")

    await update.message.reply_text(
        f"<pre>{chr(10).join(lines)}</pre>",
        parse_mode="HTML",
    )


# ---------------------------------------------------------------------------
# Image handlers
# ---------------------------------------------------------------------------

async def _handle_single(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """Handle a single image (photo or document)."""
    msg = update.message
    lc = lang(update)

    allowed, _remaining = _check_rate_limit(context)
    if not allowed:
        await msg.reply_text(t("rate_limit_reached", lc, limit=MAX_IMAGES_PER_DAY))
        return

    status = await msg.reply_text(t("processing", lc))

    try:
        if msg.document:
            if not (msg.document.mime_type or "").startswith("image/"):
                await status.edit_text(t("not_an_image", lc))
                return
            photo_file = await msg.document.get_file()
        else:
            photo_file = await msg.photo[-1].get_file()

        result = await _process_and_reply(msg, photo_file, context)
        await status.delete()

        if result:
            _increment_rate(context)
            _record_usage(context, msg.from_user.id)
            _add_to_history(context, result)
    except Exception:
        logger.exception("Failed to process image")
        await status.edit_text(t("error", lc))


async def _flush_group(media_group_id: str, context: ContextTypes.DEFAULT_TYPE) -> None:
    """Process a collected batch of media-group messages."""
    updates = _group_buffers.pop(media_group_id, [])
    _group_locks.pop(media_group_id, None)
    if not updates:
        return

    first_msg = updates[0].message
    lc = lang(updates[0])

    allowed, _remaining = _check_rate_limit(context, count=len(updates))
    if not allowed:
        await first_msg.reply_text(t("rate_limit_reached", lc, limit=MAX_IMAGES_PER_DAY))
        return

    status = await first_msg.reply_text(
        t("processing_batch", lc, count=len(updates))
    )

    success = 0
    for i, upd in enumerate(updates):
        msg = upd.message
        try:
            if len(updates) > 1:
                await status.edit_text(
                    t("progress", lc, current=i + 1, total=len(updates))
                )

            if msg.document:
                if not (msg.document.mime_type or "").startswith("image/"):
                    continue
                photo_file = await msg.document.get_file()
            else:
                photo_file = await msg.photo[-1].get_file()

            result = await _process_and_reply(msg, photo_file, context)
            success += 1

            if result:
                _add_to_history(context, result)
        except Exception:
            logger.exception("Failed to process image in group")

    _increment_rate(context, success)
    if success:
        _record_usage(context, first_msg.from_user.id, success)

    if success == len(updates):
        await status.delete()
    else:
        await status.edit_text(
            t("done_batch", lc, success=success, total=len(updates))
        )


async def handle_image(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """Entry point for photos and image documents."""
    msg = update.message
    group_id = msg.media_group_id

    if group_id is None:
        await _handle_single(update, context)
        return

    # Media group — collect messages and schedule a flush.
    _group_buffers[group_id].append(update)
    if group_id not in _group_locks:
        _group_locks[group_id] = asyncio.Event()
        context.application.job_queue.run_once(
            lambda ctx: asyncio.ensure_future(_flush_group(group_id, ctx)),
            when=1.0,
            name=f"flush_{group_id}",
        )


# ---------------------------------------------------------------------------
# ZIP handler
# ---------------------------------------------------------------------------

async def handle_zip(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """Accept a ZIP archive, process all images inside, return cleaned ZIP."""
    msg = update.message
    lc = lang(update)

    zip_tg_file = await msg.document.get_file()
    raw = await zip_tg_file.download_as_bytearray()

    try:
        zf = zipfile.ZipFile(io.BytesIO(raw))
    except zipfile.BadZipFile:
        await msg.reply_text(t("not_a_zip", lc))
        return

    image_names = [
        name for name in zf.namelist()
        if not name.endswith("/")
        and os.path.splitext(name)[1].lower() in _IMAGE_EXTENSIONS
    ]

    if not image_names:
        await msg.reply_text(t("zip_no_images", lc))
        return

    allowed, _remaining = _check_rate_limit(context, count=len(image_names))
    if not allowed:
        await msg.reply_text(t("rate_limit_reached", lc, limit=MAX_IMAGES_PER_DAY))
        return

    status = await msg.reply_text(
        t("processing_zip", lc, count=len(image_names))
    )

    out_buf = io.BytesIO()
    success = 0

    with zipfile.ZipFile(out_buf, "w", zipfile.ZIP_DEFLATED) as out_zip:
        for i, name in enumerate(image_names):
            try:
                await status.edit_text(
                    t("progress", lc, current=i + 1, total=len(image_names))
                )

                img = Image.open(io.BytesIO(zf.read(name)))
                cleaned = remove_watermark(img)

                cleaned_buf = io.BytesIO()
                ext = os.path.splitext(name)[1].lower()
                fmt = "PNG" if ext == ".png" else "JPEG"
                cleaned.save(cleaned_buf, format=fmt)

                out_zip.writestr(name, cleaned_buf.getvalue())
                success += 1
            except Exception:
                logger.exception("Failed to process %s from ZIP", name)

    out_buf.seek(0)
    _increment_rate(context, success)
    if success:
        _record_usage(context, msg.from_user.id, success)

    original_name = msg.document.file_name or "archive.zip"
    result_name = f"cleaned_{original_name}"

    doc_msg = await msg.reply_document(
        document=out_buf,
        filename=result_name,
        reply_to_message_id=msg.message_id,
    )

    _add_to_history(context, {
        "file_id": doc_msg.document.file_id,
        "filename": result_name,
        "timestamp": int(time.time()),
        "original_name": original_name,
    })

    if success == len(image_names):
        await status.delete()
    else:
        await status.edit_text(
            t("zip_done", lc, success=success, total=len(image_names))
        )


# ---------------------------------------------------------------------------
# Inline mode
# ---------------------------------------------------------------------------

async def inline_query(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """Show user's history when they type @bot in any chat."""
    query = update.inline_query
    history = _get_history(context)

    if not history:
        await query.answer(
            results=[],
            button=InlineQueryResultsButton(
                text=t("inline_open_bot", lang(update)),
                start_parameter="inline",
            ),
            cache_time=5,
            is_personal=True,
        )
        return

    search = query.query.strip().lower()
    results = []
    for i, entry in enumerate(history):
        name = entry.get("original_name", entry["filename"])
        if search and search not in name.lower():
            continue
        results.append(
            InlineQueryResultCachedDocument(
                id=str(i),
                title=name,
                document_file_id=entry["file_id"],
                description=_format_timestamp(entry["timestamp"]),
            )
        )
        if len(results) >= 20:
            break

    await query.answer(results=results, cache_time=5, is_personal=True)


# ---------------------------------------------------------------------------
# Application builder
# ---------------------------------------------------------------------------

def build_app(token: str, persistence=None) -> Application:
    """Create and configure the bot application."""
    builder = Application.builder().token(token)
    if persistence:
        builder = builder.persistence(persistence)
    app = builder.build()

    # Commands
    app.add_handler(CommandHandler("start", start))
    app.add_handler(CommandHandler("help", help_command))
    app.add_handler(CommandHandler("history", history_command))
    app.add_handler(CommandHandler("stats", stats_command))

    # Inline mode
    app.add_handler(InlineQueryHandler(inline_query))

    # History re-download buttons
    app.add_handler(CallbackQueryHandler(history_callback, pattern=r"^history:\d+$"))

    # Images
    app.add_handler(MessageHandler(filters.PHOTO | filters.Document.IMAGE, handle_image))

    # ZIP archives (before generic Document.ALL)
    app.add_handler(MessageHandler(
        filters.Document.MimeType("application/zip"),
        handle_zip,
    ))

    # Catch remaining documents
    app.add_handler(MessageHandler(filters.Document.ALL, handle_image))

    return app
