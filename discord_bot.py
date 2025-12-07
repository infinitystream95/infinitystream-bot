import os
import discord
from discord.ext import commands, tasks
from dotenv import load_dotenv
from datetime import datetime

from db import (
    init_db,
    add_request,
    list_open_requests,
    list_all_requests,
    update_status,
    delete_request,
)

load_dotenv()

# ---------- CONFIG ----------

REQUEST_NOTIFICATION_CHANNEL_ID = int(os.getenv("REQUEST_NOTIFICATION_CHANNEL_ID", "0"))
REQUEST_SEARCH_CHANNEL_ID = int(os.getenv("REQUEST_SEARCH_CHANNEL_ID", "0"))
REQUEST_LIST_CHANNEL_ID = int(os.getenv("REQUEST_LIST_CHANNEL_ID", "0"))
REQUEST_ADD_CHANNEL_ID = int(os.getenv("REQUEST_ADD_CHANNEL_ID", "0"))
REQUEST_ADMIN_CHANNEL_ID = int(os.getenv("REQUEST_ADMIN_CHANNEL_ID", "0"))

# IDs des admins
ADMIN_IDS = {
    1295044197019291791,
    1131644765906141314,
    1442230385265344645,
}

# Statuts possibles en base
VALID_STATUSES = {
    "file_attente": "Dans la file d'attente",
    "en_cours": "En cours de traitement",
    "traitee": "Traité(e)",
    "ajout_dispo": "Ajout disponible",
    "ajout_non_dispo": "Ajout non disponible",
}

intents = discord.Intents.default()
intents.message_content = True

bot = commands.Bot(command_prefix="!", intents=intents)

# ID du message "aperçu des demandes" dans le salon de liste
LIST_OVERVIEW_MESSAGE_ID: int = 0


# ---------- UTILS ----------

def is_admin(user: discord.abc.User) -> bool:
    return user.id in ADMIN_IDS


def is_in_allowed_channel(channel: discord.abc.GuildChannel, allowed_id: int) -> bool:
    """True si aucune restriction (0) ou si le bon salon."""
    if allowed_id == 0:
        return True
    if channel is None:
        return False
    return channel.id == allowed_id


def format_request_row(row) -> str:
    req_id, user_id, platform, title, year, category, status, created_at = row
    status_label = VALID_STATUSES.get(status, status)
    return (
        f"**#{req_id}** • **{title} ({year})** • `{category}` • "
        f"Statut: *{status_label}*"
    )


def format_requests_block(rows, limit: int, title: str, empty_message: str) -> discord.Embed:
    """Crée un embed 'propre' pour une liste de demandes."""
    embed = discord.Embed(
        title=title,
        colour=discord.Colour.blurple(),
    )

    if not rows:
        embed.description = empty_message
        return embed

    total = len(rows)
    shown = rows[:limit]
    lines = [format_request_row(r) for r in shown]
    if total > limit:
        remaining = total - limit
        lines.append(f"… et **{remaining}** autre(s) demande(s).")

    embed.description = "\n".join(lines)
    return embed


def build_list_overview_embed() -> discord.Embed:
    """Embed global qui s'affiche en permanence dans le salon de liste."""
    rows = list_all_requests()
    embed = format_requests_block(
        rows,
        MAX_LIST_RESULTS,
        "📊 Aperçu des demandes",
        "Aucune demande enregistrée pour le moment.",
    )

    # Date / heure de la dernière mise à jour (heure du serveur)
    now_str = datetime.now().strftime("%d/%m/%Y %H:%M:%S")
    embed.set_footer(
        text=f"Mis à jour toutes les 5 minutes • Dernière maj : {now_str}"
    )

    return embed


def find_duplicate_request(title: str, year: int, category: str):
    """Retourne la première demande qui a exactement le même titre + année + type, ou None."""
    rows = list_all_requests()
    normalized_title = title.strip().lower()
    for row in rows:
        req_id, user_id, platform, row_title, row_year, row_category, status, created_at = row
        if (
            row_title.strip().lower() == normalized_title
            and int(row_year) == int(year)
            and row_category == category
        ):
            return row
    return None


def get_request_by_id(request_id: int):
    rows = list_all_requests()
    for row in rows:
        if row[0] == request_id:
            return row
    return None


def list_requests_by_user(user_id: str):
    rows = list_all_requests()
    return [r for r in rows if r[1] == user_id]


def count_user_requests_today(user_id: str) -> int:
    """Retourne le nombre de demandes faites par cet utilisateur aujourd'hui (UTC)."""
    today_str = datetime.utcnow().strftime("%Y-%m-%d")
    rows = list_requests_by_user(user_id)
    count = 0
    for r in rows:
        # r = (req_id, user_id, platform, title, year, category, status, created_at)
        created_at = str(r[7]) if len(r) > 7 else ""
        if created_at.startswith(today_str):
            count += 1
    return count

MAX_SEARCH_RESULTS = 10
MAX_LIST_RESULTS = 30
MAX_ADMIN_RESULTS = 50


# ---------- TASK D'AUTO-REFRESH DANS LE SALON DE LISTE ----------

@tasks.loop(minutes=5)
async def update_list_overview():
    """Met à jour toutes les 5 minutes le message 'Aperçu des demandes' dans le salon de liste."""
    global LIST_OVERVIEW_MESSAGE_ID

    if REQUEST_LIST_CHANNEL_ID == 0:
        return
    if LIST_OVERVIEW_MESSAGE_ID == 0:
        # aucun message à suivre pour le moment (on attend que !panel_list soit utilisé)
        return

    channel = bot.get_channel(REQUEST_LIST_CHANNEL_ID)
    if channel is None:
        return

    try:
        message = await channel.fetch_message(LIST_OVERVIEW_MESSAGE_ID)
    except discord.NotFound:
        # le message a été supprimé, on arrête de le suivre
        LIST_OVERVIEW_MESSAGE_ID = 0
        return

    embed = build_list_overview_embed()
    try:
        await message.edit(embed=embed)
    except discord.HTTPException:
        # en cas d'erreur d'édition, on ne fait rien, on réessaiera au tour suivant
        return


# ---------- MODALS ----------

class NewRequestModal(discord.ui.Modal, title="➕ Nouvelle demande"):

    titre = discord.ui.TextInput(
        label="Titre",
        placeholder="Nom du film / de la série",
        required=True,
        max_length=200,
    )

    annee = discord.ui.TextInput(
        label="Année de sortie",
        placeholder="2023",
        required=True,
        max_length=4,
    )

    type_oeuvre = discord.ui.TextInput(
        label="Type",
        placeholder="film ou serie",
        required=True,
        max_length=15,
    )

    async def on_submit(self, interaction: discord.Interaction):
        # Vérification du salon (salon d'ajout)
        if not is_in_allowed_channel(interaction.channel, REQUEST_ADD_CHANNEL_ID):
            if REQUEST_ADD_CHANNEL_ID:
                await interaction.response.send_message(
                    f"❌ Les demandes doivent être créées dans <#{REQUEST_ADD_CHANNEL_ID}>.",
                    ephemeral=True,
                )
            else:
                await interaction.response.send_message(
                    "❌ Le salon d'ajout de demandes n'est pas configuré.",
                    ephemeral=True,
                )
            return

        # 🔒 Limite : 3 demandes par utilisateur et par jour
        user_id = str(interaction.user.id)
        today_count = count_user_requests_today(user_id)
        if today_count >= 3:
            await interaction.response.send_message(
                "❌ Tu as déjà atteint la limite de **3 demandes pour aujourd'hui**.\n"
                "Réessaie demain 😉",
                ephemeral=True,
            )
            return

        raw_title = str(self.titre.value).strip()
        raw_year = str(self.annee.value).strip()
        raw_cat = str(self.type_oeuvre.value).strip().lower()

        try:
            year = int(raw_year)
        except ValueError:
            await interaction.response.send_message(
                "❌ L'année doit être un nombre (ex : 2023).",
                ephemeral=True,
            )
            return

        if raw_cat in ("film", "films"):
            category = "film"
        elif raw_cat in ("serie", "série", "series"):
            category = "serie"
        else:
            await interaction.response.send_message(
                "❌ Le type doit être `film` ou `serie`.",
                ephemeral=True,
            )
            return

        # Doublon ?
        existing = find_duplicate_request(raw_title, year, category)
        if existing is not None:
            embed = format_requests_block(
                [existing],
                1,
                "⚠️ Demande déjà existante",
                "Une demande similaire existe déjà.",
            )
            await interaction.response.send_message(embed=embed, ephemeral=True)
            return

        request_id = add_request(
            user_id=user_id,
            platform="discord",
            title=raw_title,
            year=year,
            category=category,
        )

        embed = discord.Embed(
            title="✅ Demande enregistrée",
            description=(
                f"ID : **#{request_id}**\n"
                f"Titre : **{raw_title} ({year})**\n"
                f"Type : `{category}`\n"
                f"Statut : *{VALID_STATUSES['file_attente']}*"
            ),
            colour=discord.Colour.green(),
        )

        await interaction.response.send_message(embed=embed, ephemeral=True)


class SearchRequestModal(discord.ui.Modal, title="🔍 Rechercher une demande"):

    query = discord.ui.TextInput(
        label="Titre ou partie du titre",
        placeholder="Ex : matrix",
        required=True,
        max_length=200,
    )

    async def on_submit(self, interaction: discord.Interaction):
        if not is_in_allowed_channel(interaction.channel, REQUEST_SEARCH_CHANNEL_ID):
            if REQUEST_SEARCH_CHANNEL_ID:
                await interaction.response.send_message(
                    f"❌ La recherche doit se faire dans <#{REQUEST_SEARCH_CHANNEL_ID}>.",
                    ephemeral=True,
                )
            else:
                await interaction.response.send_message(
                    "❌ Le salon de recherche n'est pas configuré.",
                    ephemeral=True,
                )
            return

        q = str(self.query.value).strip().lower()
        rows = list_all_requests()
        matching = [r for r in rows if q in str(r[3]).lower()]  # r[3] = title

        embed = format_requests_block(
            matching,
            MAX_SEARCH_RESULTS,
            f"🔍 Résultats pour « {self.query.value} »",
            "Aucune demande trouvée.",
        )
        await interaction.response.send_message(embed=embed, ephemeral=True)


class ChangeStatusModal(discord.ui.Modal, title="✏️ Changer le statut"):

    request_id_input = discord.ui.TextInput(
        label="ID de la demande",
        placeholder="Ex : 12",
        required=True,
        max_length=10,
    )

    async def on_submit(self, interaction: discord.Interaction):
        if not is_admin(interaction.user):
            await interaction.response.send_message(
                "⛔ Tu n'as pas la permission.",
                ephemeral=True,
            )
            return

        if not is_in_allowed_channel(interaction.channel, REQUEST_ADMIN_CHANNEL_ID):
            if REQUEST_ADMIN_CHANNEL_ID:
                await interaction.response.send_message(
                    f"❌ Ce formulaire doit être utilisé dans <#{REQUEST_ADMIN_CHANNEL_ID}>.",
                    ephemeral=True,
                )
            else:
                await interaction.response.send_message(
                    "❌ Le salon admin n'est pas configuré.",
                    ephemeral=True,
                )
            return

        try:
            req_id = int(str(self.request_id_input.value).strip())
        except ValueError:
            await interaction.response.send_message(
                "❌ L'ID doit être un nombre.",
                ephemeral=True,
            )
            return

        row = get_request_by_id(req_id)
        if row is None:
            await interaction.response.send_message(
                f"❌ Aucune demande trouvée avec l'ID #{req_id}.",
                ephemeral=True,
            )
            return

        view = StatusSelectView(req_id)
        embed = discord.Embed(
            title=f"✏️ Changer le statut de #{req_id}",
            description=format_request_row(row),
            colour=discord.Colour.orange(),
        )
        await interaction.response.send_message(
            embed=embed,
            view=view,
            ephemeral=True,
        )


class DeleteRequestModal(discord.ui.Modal, title="🗑 Supprimer une demande"):

    request_id_input = discord.ui.TextInput(
        label="ID de la demande",
        placeholder="Ex : 12",
        required=True,
        max_length=10,
    )

    async def on_submit(self, interaction: discord.Interaction):
        if not is_admin(interaction.user):
            await interaction.response.send_message(
                "⛔ Tu n'as pas la permission.",
                ephemeral=True,
            )
            return

        if not is_in_allowed_channel(interaction.channel, REQUEST_ADMIN_CHANNEL_ID):
            if REQUEST_ADMIN_CHANNEL_ID:
                await interaction.response.send_message(
                    f"❌ Ce formulaire doit être utilisé dans <#{REQUEST_ADMIN_CHANNEL_ID}>.",
                    ephemeral=True,
                )
            else:
                await interaction.response.send_message(
                    "❌ Le salon admin n'est pas configuré.",
                    ephemeral=True,
                )
            return

        try:
            req_id = int(str(self.request_id_input.value).strip())
        except ValueError:
            await interaction.response.send_message(
                "❌ L'ID doit être un nombre.",
                ephemeral=True,
            )
            return

        ok = delete_request(req_id)
        if not ok:
            await interaction.response.send_message(
                f"❌ Aucune demande trouvée avec l'ID #{req_id}.",
                ephemeral=True,
            )
        else:
            await interaction.response.send_message(
                f"🗑 Demande **#{req_id}** supprimée.",
                ephemeral=True,
            )


class ResultRequestModal(discord.ui.Modal):

    def __init__(self, is_available: bool):
        title = "📢 Résultat : ajout disponible" if is_available else "📢 Résultat : ajout non disponible"
        super().__init__(title=title)
        self.is_available = is_available

        self.request_id_input = discord.ui.TextInput(
            label="ID de la demande",
            placeholder="Ex : 12",
            required=True,
            max_length=10,
        )
        self.comment_input = discord.ui.TextInput(
            label="Commentaire (optionnel)",
            style=discord.TextStyle.paragraph,
            required=False,
            max_length=400,
            placeholder="Ex : Ajouté sur le site / Introuvable…",
        )

        self.add_item(self.request_id_input)
        self.add_item(self.comment_input)

    async def on_submit(self, interaction: discord.Interaction):
        if not is_admin(interaction.user):
            await interaction.response.send_message(
                "⛔ Tu n'as pas la permission.",
                ephemeral=True,
            )
            return

        if not is_in_allowed_channel(interaction.channel, REQUEST_ADMIN_CHANNEL_ID):
            if REQUEST_ADMIN_CHANNEL_ID:
                await interaction.response.send_message(
                    f"❌ Ce formulaire doit être utilisé dans <#{REQUEST_ADMIN_CHANNEL_ID}>.",
                    ephemeral=True,
                )
            else:
                await interaction.response.send_message(
                    "❌ Le salon admin n'est pas configuré.",
                    ephemeral=True,
                )
            return

        try:
            req_id = int(str(self.request_id_input.value).strip())
        except ValueError:
            await interaction.response.send_message(
                "❌ L'ID doit être un nombre.",
                ephemeral=True,
            )
            return

        commentaire = str(self.comment_input.value or "").strip()

        row = get_request_by_id(req_id)
        if row is None:
            await interaction.response.send_message(
                f"❌ Aucune demande trouvée avec l'ID #{req_id}.",
                ephemeral=True,
            )
            return

        status_code = "ajout_dispo" if self.is_available else "ajout_non_dispo"
        ok = update_status(req_id, status_code)
        if not ok:
            await interaction.response.send_message(
                f"❌ Impossible de mettre à jour la demande #{req_id}.",
                ephemeral=True,
            )
            return

        # Envoi de la notif dans le salon dédié
        if REQUEST_NOTIFICATION_CHANNEL_ID == 0:
            await interaction.response.send_message(
                "⚠️ Le salon de notifications n'est pas configuré "
                "(variable d'environnement `REQUEST_NOTIFICATION_CHANNEL_ID`).",
                ephemeral=True,
            )
            return

        notif_channel = bot.get_channel(REQUEST_NOTIFICATION_CHANNEL_ID)
        if notif_channel is None:
            await interaction.response.send_message(
                "⚠️ Impossible de trouver le salon de notifications. Vérifie l'ID.",
                ephemeral=True,
            )
            return

        req_id_row, user_id, platform, title, year, category, status, created_at = row
        user_mention = f"<@{user_id}>"
        etat_label = "✅ **Ajout disponible**" if self.is_available else "❌ **Ajout non disponible**"

        description = (
            f"{etat_label} pour ta demande **#{req_id_row}** : **{title} ({year})** • `{category}`\n"
        )
        if commentaire:
            description += f"📝 {commentaire}"

        embed = discord.Embed(
            title="🎬 Notification de demande",
            description=description,
            colour=discord.Colour.green() if self.is_available else discord.Colour.red(),
        )

        await notif_channel.send(content=user_mention, embed=embed)
        await interaction.response.send_message(
            f"📣 Résultat envoyé pour la demande **#{req_id_row}**.",
            ephemeral=True,
        )


# ---------- SELECTS & VIEWS ----------

class StatusSelect(discord.ui.Select):
    def __init__(self, request_id: int):
        self.request_id = request_id

        options = [
            discord.SelectOption(
                label="Dans la file d'attente",
                value="file_attente",
                emoji="⏳",
            ),
            discord.SelectOption(
                label="En cours de traitement",
                value="en_cours",
                emoji="🛠",
            ),
            discord.SelectOption(
                label="Traité(e)",
                value="traitee",
                emoji="✅",
            ),
            discord.SelectOption(
                label="Ajout disponible",
                value="ajout_dispo",
                emoji="📀",
            ),
            discord.SelectOption(
                label="Ajout non disponible",
                value="ajout_non_dispo",
                emoji="❌",
            ),
        ]

        super().__init__(
            placeholder="Choisis un nouveau statut…",
            min_values=1,
            max_values=1,
            options=options,
            custom_id=f"status_select_{request_id}",
        )

    async def callback(self, interaction: discord.Interaction):
        if not is_admin(interaction.user):
            await interaction.response.send_message(
                "⛔ Tu n'as pas la permission.",
                ephemeral=True,
            )
            return

        new_status = self.values[0]
        ok = update_status(self.request_id, new_status)
        if not ok:
            await interaction.response.send_message(
                f"❌ Aucune demande trouvée avec l'ID #{self.request_id}.",
                ephemeral=True,
            )
            return

        label = VALID_STATUSES.get(new_status, new_status)
        await interaction.response.send_message(
            f"✅ Statut de la demande **#{self.request_id}** mis à jour : **{label}**",
            ephemeral=True,
        )


class StatusSelectView(discord.ui.View):
    def __init__(self, request_id: int):
        super().__init__(timeout=60)
        self.add_item(StatusSelect(request_id))


class AdminPanelView(discord.ui.View):
    def __init__(self):
        super().__init__(timeout=120)

    @discord.ui.button(
        label="📚 Toutes les demandes",
        style=discord.ButtonStyle.secondary,
        custom_id="admin_all_requests",
    )
    async def all_requests(
        self,
        interaction: discord.Interaction,
        button: discord.ui.Button,
    ):
        if not is_admin(interaction.user):
            await interaction.response.send_message(
                "⛔ Tu n'as pas la permission.",
                ephemeral=True,
            )
            return

        if not is_in_allowed_channel(interaction.channel, REQUEST_ADMIN_CHANNEL_ID):
            if REQUEST_ADMIN_CHANNEL_ID:
                await interaction.response.send_message(
                    f"❌ Ce panneau admin ne peut être utilisé que dans <#{REQUEST_ADMIN_CHANNEL_ID}>.",
                    ephemeral=True,
                )
            else:
                await interaction.response.send_message(
                    "❌ Le salon admin n'est pas configuré.",
                    ephemeral=True,
                )
            return

        rows = list_all_requests()
        embed = format_requests_block(
            rows,
            MAX_ADMIN_RESULTS,
            "📚 Toutes les demandes",
            "Aucune demande enregistrée.",
        )
        await interaction.response.send_message(embed=embed, ephemeral=True)

    @discord.ui.button(
        label="✏️ Changer un statut",
        style=discord.ButtonStyle.primary,
        custom_id="admin_change_status",
    )
    async def change_status(
        self,
        interaction: discord.Interaction,
        button: discord.ui.Button,
    ):
        if not is_admin(interaction.user):
            await interaction.response.send_message(
                "⛔ Tu n'as pas la permission.",
                ephemeral=True,
            )
            return

        await interaction.response.send_modal(ChangeStatusModal())

    @discord.ui.button(
        label="📢 Résultat dispo",
        style=discord.ButtonStyle.success,
        custom_id="admin_result_dispo",
    )
    async def result_dispo(
        self,
        interaction: discord.Interaction,
        button: discord.ui.Button,
    ):
        if not is_admin(interaction.user):
            await interaction.response.send_message(
                "⛔ Tu n'as pas la permission.",
                ephemeral=True,
            )
            return

        await interaction.response.send_modal(ResultRequestModal(is_available=True))

    @discord.ui.button(
        label="📢 Résultat non dispo",
        style=discord.ButtonStyle.danger,
        custom_id="admin_result_nondispo",
    )
    async def result_nondispo(
        self,
        interaction: discord.Interaction,
        button: discord.ui.Button,
    ):
        if not is_admin(interaction.user):
            await interaction.response.send_message(
                "⛔ Tu n'as pas la permission.",
                ephemeral=True,
            )
            return

        await interaction.response.send_modal(ResultRequestModal(is_available=False))

    @discord.ui.button(
        label="🗑 Supprimer",
        style=discord.ButtonStyle.danger,
        custom_id="admin_delete_request",
    )
    async def delete_request_btn(
        self,
        interaction: discord.Interaction,
        button: discord.ui.Button,
    ):
        if not is_admin(interaction.user):
            await interaction.response.send_message(
                "⛔ Tu n'as pas la permission.",
                ephemeral=True,
            )
            return

        await interaction.response.send_modal(DeleteRequestModal())


# --- PANELS PAR SALON ---

class AddPanelView(discord.ui.View):
    """Panel du salon d'ajout de demandes."""
    def __init__(self):
        super().__init__(timeout=None)

    @discord.ui.button(
        label="➕ Faire une demande",
        style=discord.ButtonStyle.success,
        emoji="🎬",
        custom_id="add_new_request",
    )
    async def new_request(
        self,
        interaction: discord.Interaction,
        button: discord.ui.Button,
    ):
        if not is_in_allowed_channel(interaction.channel, REQUEST_ADD_CHANNEL_ID):
            if REQUEST_ADD_CHANNEL_ID:
                await interaction.response.send_message(
                    f"❌ Les demandes doivent être créées dans <#{REQUEST_ADD_CHANNEL_ID}>.",
                    ephemeral=True,
                )
            else:
                await interaction.response.send_message(
                    "❌ Le salon d'ajout de demandes n'est pas configuré.",
                    ephemeral=True,
                )
            return

        await interaction.response.send_modal(NewRequestModal())


class ListPanelView(discord.ui.View):
    """Panel du salon de liste (mes demandes + demandes en cours)."""
    def __init__(self):
        super().__init__(timeout=None)

    @discord.ui.button(
        label="📋 Mes demandes",
        style=discord.ButtonStyle.secondary,
        emoji="🙋",
        custom_id="list_my_requests",
    )
    async def my_requests(
        self,
        interaction: discord.Interaction,
        button: discord.ui.Button,
    ):
        if not is_in_allowed_channel(interaction.channel, REQUEST_LIST_CHANNEL_ID):
            if REQUEST_LIST_CHANNEL_ID:
                await interaction.response.send_message(
                    f"❌ La liste des demandes doit être consultée dans <#{REQUEST_LIST_CHANNEL_ID}>.",
                    ephemeral=True,
                )
            else:
                await interaction.response.send_message(
                    "❌ Le salon de liste des demandes n'est pas configuré.",
                    ephemeral=True,
                )
            return

        rows = list_requests_by_user(str(interaction.user.id))
        embed = format_requests_block(
            rows,
            MAX_LIST_RESULTS,
            "📋 Tes demandes",
            "Tu n'as encore fait aucune demande.",
        )
        await interaction.response.send_message(embed=embed, ephemeral=True)

    @discord.ui.button(
        label="📂 Demandes en cours",
        style=discord.ButtonStyle.secondary,
        emoji="📂",
        custom_id="list_open_requests",
    )
    async def list_open(
        self,
        interaction: discord.Interaction,
        button: discord.ui.Button,
    ):
        if not is_in_allowed_channel(interaction.channel, REQUEST_LIST_CHANNEL_ID):
            if REQUEST_LIST_CHANNEL_ID:
                await interaction.response.send_message(
                    f"❌ Cette action est disponible seulement dans <#{REQUEST_LIST_CHANNEL_ID}>.",
                    ephemeral=True,
                )
            else:
                await interaction.response.send_message(
                    "❌ Le salon de liste des demandes n'est pas configuré.",
                    ephemeral=True,
                )
            return

        rows = list_open_requests()
        embed = format_requests_block(
            rows,
            MAX_LIST_RESULTS,
            "📂 Demandes en cours",
            "Aucune demande en cours.",
        )
        await interaction.response.send_message(embed=embed, ephemeral=True)


class SearchPanelView(discord.ui.View):
    """Panel du salon de recherche."""
    def __init__(self):
        super().__init__(timeout=None)

    @discord.ui.button(
        label="🔍 Rechercher une demande",
        style=discord.ButtonStyle.primary,
        emoji="🔎",
        custom_id="search_request",
    )
    async def search(
        self,
        interaction: discord.Interaction,
        button: discord.ui.Button,
    ):
        if not is_in_allowed_channel(interaction.channel, REQUEST_SEARCH_CHANNEL_ID):
            if REQUEST_SEARCH_CHANNEL_ID:
                await interaction.response.send_message(
                    f"❌ La recherche doit se faire dans <#{REQUEST_SEARCH_CHANNEL_ID}>.",
                    ephemeral=True,
                )
            else:
                await interaction.response.send_message(
                    "❌ Le salon de recherche n'est pas configuré.",
                    ephemeral=True,
                )
            return

        await interaction.response.send_modal(SearchRequestModal())


# ---------- EVENTS & COMMANDES ----------

@bot.event
async def on_ready():
    init_db()
    print(f"Connecté en tant que {bot.user} (ID: {bot.user.id})")
    # On démarre la tâche d'auto-refresh si elle n'est pas déjà en cours
    if not update_list_overview.is_running():
        update_list_overview.start()


# --- Commandes pour afficher les panels dans CHAQUE salon ---

@bot.command(name="panel_add")
async def panel_add(ctx: commands.Context):
    """Panel du salon d'ajout de demandes."""
    if not is_in_allowed_channel(ctx.channel, REQUEST_ADD_CHANNEL_ID):
        await ctx.send(
            f"❌ Cette commande ne peut être utilisée que dans <#{REQUEST_ADD_CHANNEL_ID}>."
        )
        return

    view = AddPanelView()
    embed = discord.Embed(
        title="🎬 Faire une demande",
        description=(
            "Clique sur **➕ Faire une demande** pour proposer un film ou une série.\n\n"
            "Le bot vérifiera automatiquement s'il existe déjà une demande avec le même "
            "titre / année / type."
        ),
        colour=discord.Colour.green(),
    )
    await ctx.send(embed=embed, view=view)


@bot.command(name="panel_list")
async def panel_list(ctx: commands.Context):
    """Panel du salon de liste des demandes + message auto-mis à jour."""
    global LIST_OVERVIEW_MESSAGE_ID

    if not is_in_allowed_channel(ctx.channel, REQUEST_LIST_CHANNEL_ID):
        await ctx.send(
            f"❌ Cette commande ne peut être utilisée que dans <#{REQUEST_LIST_CHANNEL_ID}>."
        )
        return

    # 1) Panel avec boutons
    view = ListPanelView()
    embed_panel = discord.Embed(
        title="📋 Voir les demandes",
        description=(
            "• **📋 Mes demandes** : tes demandes et leurs statuts\n"
            "• **📂 Demandes en cours** : toutes les demandes ouvertes\n\n"
            "Si la liste est trop longue, le bot affichera `…` à la fin pour éviter de "
            "dépasser la limite de Discord."
        ),
        colour=discord.Colour.blurple(),
    )
    await ctx.send(embed=embed_panel, view=view)

    # 2) Message d'aperçu global (que le bot va éditer toutes les 5 minutes)
    overview_embed = build_list_overview_embed()

    # Si on a déjà un message, on essaie de le réutiliser
    if LIST_OVERVIEW_MESSAGE_ID != 0:
        try:
            msg = await ctx.channel.fetch_message(LIST_OVERVIEW_MESSAGE_ID)
            await msg.edit(embed=overview_embed)
            return
        except discord.NotFound:
            # il a été supprimé -> on recrée plus bas
            LIST_OVERVIEW_MESSAGE_ID = 0

    msg = await ctx.send(embed=overview_embed)
    LIST_OVERVIEW_MESSAGE_ID = msg.id


@bot.command(name="panel_search")
async def panel_search(ctx: commands.Context):
    """Panel du salon de recherche de demandes."""
    if not is_in_allowed_channel(ctx.channel, REQUEST_SEARCH_CHANNEL_ID):
        await ctx.send(
            f"❌ Cette commande ne peut être utilisée que dans <#{REQUEST_SEARCH_CHANNEL_ID}>."
        )
        return

    view = SearchPanelView()
    embed = discord.Embed(
        title="🔍 Rechercher une demande",
        description=(
            "Clique sur **🔍 Rechercher une demande** pour ouvrir un formulaire.\n"
            "Tu peux entrer un titre ou une partie du titre, le bot affichera les "
            "demandes correspondantes."
        ),
        colour=discord.Colour.blue(),
    )
    await ctx.send(embed=embed, view=view)


@bot.command(name="panel_admin")
async def panel_admin(ctx: commands.Context):
    """Panel admin (changer statuts, voir toutes les demandes, envoyer résultats)."""
    if not is_admin(ctx.author):
        await ctx.send("⛔ Tu n'as pas la permission pour cette commande.")
        return

    if not is_in_allowed_channel(ctx.channel, REQUEST_ADMIN_CHANNEL_ID):
        await ctx.send(
            f"❌ Cette commande ne peut être utilisée que dans <#{REQUEST_ADMIN_CHANNEL_ID}>."
        )
        return

    view = AdminPanelView()
    embed = discord.Embed(
        title="🛠 Panel admin des demandes",
        description=(
            "• **📚 Toutes les demandes** : affiche toutes les demandes (avec `...` si trop)\n"
            "• **✏️ Changer un statut** : modifier le statut d'une demande via un select\n"
            "• **📢 Résultat dispo / non dispo** : change le statut et envoie la notification\n"
            f"    → Les notifs partent dans <#{REQUEST_NOTIFICATION_CHANNEL_ID}> avec mention de l'auteur\n"
            "• **🗑 Supprimer** : supprimer une demande\n"
        ),
        colour=discord.Colour.orange(),
    )
    await ctx.send(embed=embed, view=view)