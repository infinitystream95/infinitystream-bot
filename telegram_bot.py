import os
from dotenv import load_dotenv
from enum import Enum
from telegram.ext import Application

from telegram import (
    Update,
    InlineKeyboardMarkup,
    InlineKeyboardButton,
)
from telegram.ext import (
    ApplicationBuilder,
    CommandHandler,
    ContextTypes,
    CallbackQueryHandler,
    MessageHandler,
    filters,
)

from db import (
    init_db,
    add_request,
    list_open_requests,
    list_all_requests,
    update_status,
    delete_request,
)

load_dotenv()

# ⚠️ remplace par tes IDs Telegram admin (entiers)
ADMIN_IDS = {
    7215183563,
}

VALID_STATUSES = {
    "file_attente": "Dans la file d'attente",
    "en_cours": "En cours de traitement",
    "traitee": "Traité(e)",
}


def is_admin_telegram(user_id: int) -> bool:
    return user_id in ADMIN_IDS


def format_request_row(row) -> str:
    req_id, user_id, platform, title, year, category, status, created_at = row
    status_label = VALID_STATUSES.get(status, status)
    return f"#{req_id} • {title} ({year}) • {category} • {status_label} • {platform}"


class Flow(str, Enum):
    NONE = "none"
    CREATE = "create"
    ADMIN_CHANGE_STATUS_WAIT_ID = "admin_change_status_wait_id"
    ADMIN_DELETE_WAIT_ID = "admin_delete_wait_id"


async def send_main_menu(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user = update.effective_user
    buttons = [
        [InlineKeyboardButton("➕ Nouvelle demande", callback_data="new_request")],
        [InlineKeyboardButton("📋 Demandes en cours", callback_data="list_open")],
    ]
    if is_admin_telegram(user.id):
        buttons.append(
            [InlineKeyboardButton("⚙️ Admin", callback_data="admin_panel")]
        )

    keyboard = InlineKeyboardMarkup(buttons)

    if update.message:
        await update.message.reply_text(
            "🎬 Menu des demandes films/séries :",
            reply_markup=keyboard,
        )
    elif update.callback_query:
        await update.callback_query.edit_message_text(
            "🎬 Menu des demandes films/séries :",
            reply_markup=keyboard,
        )


# ---------- HANDLERS ----------

async def start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    context.user_data.clear()
    context.user_data["flow"] = Flow.NONE.value
    await send_main_menu(update, context)


async def callback_handler(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    data = query.data
    user = query.from_user

    await query.answer()  # stop le "chargement" Telegram

    # Nouveau formulaire
    if data == "new_request":
        context.user_data.clear()
        context.user_data["flow"] = Flow.CREATE.value
        context.user_data["step"] = "title"
        await query.message.reply_text("📋 Envoie le **titre** du film/de la série :")
        return

    # Liste demandes en cours
    if data == "list_open":
        rows = list_open_requests()
        if not rows:
            await query.message.reply_text("📭 Aucune demande en cours.")
            return

        lines = [format_request_row(r) for r in rows[:30]]
        text = "📋 *Demandes en cours* (max 30) :\n" + "\n".join(lines)
        await query.message.reply_text(text, parse_mode="Markdown")
        return

    # Panneau admin
    if data == "admin_panel":
        if not is_admin_telegram(user.id):
            await query.message.reply_text("⛔ Tu n'as pas la permission.")
            return

        buttons = [
            [InlineKeyboardButton("📚 Toutes les demandes", callback_data="admin_all")],
            [InlineKeyboardButton("✏️ Changer statut", callback_data="admin_change_status")],
            [InlineKeyboardButton("🗑 Supprimer demande", callback_data="admin_delete")],
        ]
        await query.message.reply_text(
            "🔧 Panneau admin :",
            reply_markup=InlineKeyboardMarkup(buttons),
        )
        return

    # Admin : toutes les demandes
    if data == "admin_all":
        if not is_admin_telegram(user.id):
            await query.message.reply_text("⛔ Tu n'as pas la permission.")
            return

        rows = list_all_requests()
        if not rows:
            await query.message.reply_text("📭 Aucune demande enregistrée.")
            return

        lines = [format_request_row(r) for r in rows[:50]]
        text = "📚 *Toutes les demandes* (max 50) :\n" + "\n".join(lines)
        await query.message.reply_text(text, parse_mode="Markdown")
        return

    # Admin : changer statut (demande l'ID)
    if data == "admin_change_status":
        if not is_admin_telegram(user.id):
            await query.message.reply_text("⛔ Tu n'as pas la permission.")
            return

        context.user_data.clear()
        context.user_data["flow"] = Flow.ADMIN_CHANGE_STATUS_WAIT_ID.value
        await query.message.reply_text(
            "✏️ Envoie l'**ID** de la demande dont tu veux changer le statut."
        )
        return

    # Admin : supprimer (demande l'ID)
    if data == "admin_delete":
        if not is_admin_telegram(user.id):
            await query.message.reply_text("⛔ Tu n'as pas la permission.")
            return

        context.user_data.clear()
        context.user_data["flow"] = Flow.ADMIN_DELETE_WAIT_ID.value
        await query.message.reply_text(
            "🗑 Envoie l'**ID** de la demande à supprimer."
        )
        return

    # Choix de la catégorie pour la création
    if data.startswith("category:"):
        category = data.split(":", 1)[1]
        flow = context.user_data.get("flow")
        step = context.user_data.get("step")

        if flow != Flow.CREATE.value or step != "category":
            return

        title = context.user_data.get("title")
        year = context.user_data.get("year")

        request_id = add_request(
            user_id=str(user.id),
            platform="telegram",
            title=title,
            year=year,
            category=category,
        )

        await query.message.reply_text(
            f"✅ Demande enregistrée !\n"
            f"ID: #{request_id}\n"
            f"Titre: {title} ({year})\n"
            f"Type: {category}\n"
            f"Statut: {VALID_STATUSES['file_attente']}"
        )
        context.user_data.clear()
        context.user_data["flow"] = Flow.NONE.value
        return

    # Choix du statut (admin)
    if data.startswith("status:"):
        parts = data.split(":")
        if len(parts) != 3:
            return
        _, req_id_str, status = parts
        if not is_admin_telegram(user.id):
            await query.message.reply_text("⛔ Tu n'as pas la permission.")
            return
        try:
            req_id = int(req_id_str)
        except ValueError:
            await query.message.reply_text("❌ ID invalide.")
            return

        if status not in VALID_STATUSES:
            await query.message.reply_text("❌ Statut invalide.")
            return

        ok = update_status(req_id, status)
        if not ok:
            await query.message.reply_text(f"❌ Aucune demande trouvée avec l'ID #{req_id}.")
        else:
            await query.message.reply_text(
                f"✅ Statut de la demande #{req_id} mis à jour : {VALID_STATUSES[status]}"
            )
        return

    # Confirmation suppression
    if data.startswith("confirm_delete:"):
        _, req_id_str, choice = data.split(":")
        if not is_admin_telegram(user.id):
            await query.message.reply_text("⛔ Tu n'as pas la permission.")
            return
        try:
            req_id = int(req_id_str)
        except ValueError:
            await query.message.reply_text("❌ ID invalide.")
            return

        if choice == "no":
            await query.message.reply_text("❎ Suppression annulée.")
            return

        if choice == "yes":
            ok = delete_request(req_id)
            if not ok:
                await query.message.reply_text(f"❌ Aucune demande trouvée avec l'ID #{req_id}.")
            else:
                await query.message.reply_text(f"🗑 Demande #{req_id} supprimée.")
            return


async def text_handler(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Gère les réponses texte (titre, année, IDs admin, etc.)."""
    if not update.message:
        return

    user = update.effective_user
    text = update.message.text.strip()
    flow = context.user_data.get("flow", Flow.NONE.value)

    # ----- Création de demande -----
    if flow == Flow.CREATE.value:
        step = context.user_data.get("step")

        # 1) Titre
        if step == "title":
            context.user_data["title"] = text
            context.user_data["step"] = "year"
            await update.message.reply_text("🗓 Envoie l'**année de sortie** (ex : 2023).")
            return

        # 2) Année
        if step == "year":
            try:
                year = int(text)
            except ValueError:
                await update.message.reply_text("❌ Ce n'est pas une année valide. Réessaie (ex : 2023).")
                return

            context.user_data["year"] = year
            context.user_data["step"] = "category"

            buttons = [
                [
                    InlineKeyboardButton("🎬 Film", callback_data="category:film"),
                    InlineKeyboardButton("📺 Série", callback_data="category:serie"),
                ]
            ]
            await update.message.reply_text(
                "Choisis le type :",
                reply_markup=InlineKeyboardMarkup(buttons),
            )
            return

    # ----- Admin : changer statut (ID) -----
    if flow == Flow.ADMIN_CHANGE_STATUS_WAIT_ID.value:
        if not is_admin_telegram(user.id):
            await update.message.reply_text("⛔ Tu n'as pas la permission.")
            context.user_data["flow"] = Flow.NONE.value
            return

        try:
            req_id = int(text)
        except ValueError:
            await update.message.reply_text("❌ L'ID doit être un nombre. Réessaie.")
            return

        # On propose les statuts en boutons
        buttons = [
            [
                InlineKeyboardButton("File d'attente", callback_data=f"status:{req_id}:file_attente")
            ],
            [
                InlineKeyboardButton("En cours", callback_data=f"status:{req_id}:en_cours")
            ],
            [
                InlineKeyboardButton("Traité(e)", callback_data=f"status:{req_id}:traitee")
            ],
        ]
        await update.message.reply_text(
            f"Choisis le nouveau statut pour la demande #{req_id} :",
            reply_markup=InlineKeyboardMarkup(buttons),
        )
        context.user_data["flow"] = Flow.NONE.value
        return

    # ----- Admin : suppression (ID) -----
    if flow == Flow.ADMIN_DELETE_WAIT_ID.value:
        if not is_admin_telegram(user.id):
            await update.message.reply_text("⛔ Tu n'as pas la permission.")
            context.user_data["flow"] = Flow.NONE.value
            return

        try:
            req_id = int(text)
        except ValueError:
            await update.message.reply_text("❌ L'ID doit être un nombre. Réessaie.")
            return

        buttons = [
            [
                InlineKeyboardButton("✅ Oui", callback_data=f"confirm_delete:{req_id}:yes"),
                InlineKeyboardButton("❌ Non", callback_data=f"confirm_delete:{req_id}:no"),
            ]
        ]
        await update.message.reply_text(
            f"Confirmer la suppression de la demande #{req_id} ?",
            reply_markup=InlineKeyboardMarkup(buttons),
        )
        context.user_data["flow"] = Flow.NONE.value
        return

    # Sinon : texte random, on peut renvoyer le menu
    await send_main_menu(update, context)


def build_telegram_app() -> "Application":
    """
    Construit et configure l'application Telegram,
    sans la démarrer. Utilisé par server.py.
    """
    token = os.getenv("TELEGRAM_TOKEN")
    if not token:
        raise RuntimeError("La variable d'environnement TELEGRAM_TOKEN est manquante.")

    # On garde l'init DB ici, c'est safe même si Discord l'appelle aussi
    init_db()

    app = ApplicationBuilder().token(token).build()

    # Handlers exactement comme avant
    app.add_handler(CommandHandler("start", start))
    app.add_handler(CallbackQueryHandler(callback_handler))
    app.add_handler(MessageHandler(filters.TEXT & ~filters.COMMAND, text_handler))

    return app


def main():
    """
    Mode 'standalone' si tu lances directement telegram_bot.py en local.
    """
    app = build_telegram_app()
    app.run_polling()


if __name__ == "__main__":
    main()
