# ============================================================
# Atlas Investimenti — Bot Telegram (Python + Flask)
# ============================================================
import os
import re
import time
from datetime import datetime, timezone
import requests
from flask import Flask, request, jsonify
from supabase import create_client

app = Flask(__name__)

BOT_TOKEN = os.environ["TELEGRAM_BOT_TOKEN"]
WEBHOOK_SECRET = os.environ["TELEGRAM_WEBHOOK_SECRET"]
SUPABASE_URL = os.environ["SUPABASE_URL"]
SUPABASE_SERVICE_ROLE_KEY = os.environ["SUPABASE_SERVICE_ROLE_KEY"]

supabase = create_client(SUPABASE_URL, SUPABASE_SERVICE_ROLE_KEY)
TG_API = f"https://api.telegram.org/bot{BOT_TOKEN}"


# ============================================================
# HELPERS TELEGRAM API
# ============================================================
def tg(method, payload):
    r = requests.post(f"{TG_API}/{method}", json=payload, timeout=10)
    return r.json()


def send_message(chat_id, text, extra=None):
    payload = {"chat_id": chat_id, "text": text, "parse_mode": "HTML"}
    if extra:
        payload.update(extra)
    return tg("sendMessage", payload)


def edit_message(chat_id, message_id, text, extra=None):
    payload = {"chat_id": chat_id, "message_id": message_id, "text": text, "parse_mode": "HTML"}
    if extra:
        payload.update(extra)
    return tg("editMessageText", payload)


def answer_callback(callback_query_id, text=""):
    return tg("answerCallbackQuery", {"callback_query_id": callback_query_id, "text": text})


# ============================================================
# HELPERS PERMESSI
# ============================================================
def is_global_admin(user_id):
    res = supabase.table("global_admins").select("telegram_user_id").eq("telegram_user_id", user_id).execute()
    return len(res.data) > 0


def is_moderator(chat_id, user_id):
    if is_global_admin(user_id):
        return True
    res = supabase.table("group_moderators").select("*").eq("chat_id", chat_id).eq("telegram_user_id", user_id).execute()
    return len(res.data) > 0


def get_target_from_reply(message):
    target = message.get("reply_to_message", {}).get("from")
    if not target:
        return None
    return {"id": target["id"], "username": target.get("username") or target.get("first_name")}


# ============================================================
# SESSIONI (flussi multi-step: candidatura, ecc.)
# ============================================================
def get_session(chat_id):
    res = supabase.table("bot_sessions").select("*").eq("telegram_chat_id", chat_id).execute()
    return res.data[0] if res.data else None


def set_session(chat_id, step, data=None):
    supabase.table("bot_sessions").upsert({
        "telegram_chat_id": chat_id,
        "current_step": step,
        "data_json": data or {},
    }).execute()


def clear_session(chat_id):
    supabase.table("bot_sessions").delete().eq("telegram_chat_id", chat_id).execute()


# ============================================================
# MAPPA REPARTI (usata dai comandi /setXXX e dal routing notifiche)
# ============================================================
DEPARTMENT_COMMANDS = {
    "/setgrafica": "grafica",
    "/setinformatica": "informatica",
    "/setinvestimenti": "investimenti",
    "/setedilizia": "edilizia",
    "/setsubaffitti": "subaffitti",
    "/setlegale": "legale",
}

DEPARTMENT_NAME_TO_GROUP_TYPE = {
    "Grafica": "grafica",
    "Informatica": "informatica",
    "Investimenti": "investimenti",
    "Edile": "edilizia",
    "Subaffitti": "subaffitti",
    "Legale": "legale",
}


# ============================================================
# LISTA COMANDI
# ============================================================
COMMAND_LIST = """
<b>📋 Comandi disponibili</b>

<b>Moderazione (permesso: moderatore)</b>
/setmod (in risposta) — imposta un moderatore nel gruppo
/ban (in risposta) [motivo] — banna un utente
/unban [user_id] — sbanna un utente
/mute (in risposta) [motivo] [durata] — muta un utente
/unmute (in risposta) — smuta un utente
/warn (in risposta) [motivo] — warn (contatore su 3)
/unwarn (in risposta) — rimuove un warn
/personal (in risposta) [comando] — crea risposta automatica
/unpersonal [comando] — rimuove risposta automatica
/setsite [url] — imposta il link del sito

<b>Amministrazione globale</b>
/setdipendenti — imposta questo gruppo come chat dipendenti
/setgrafica — imposta questo gruppo come chat Grafica
/setinformatica — imposta questo gruppo come chat Informatica
/setinvestimenti — imposta questo gruppo come chat Investimenti
/setedilizia — imposta questo gruppo come chat Edile
/setsubaffitti — imposta questo gruppo come chat Subaffitti
/setlegale — imposta questo gruppo come chat Legale
/addprodotto [nome] | [prezzo] | [descrizione] — aggiunge prodotto (privato)
/delprodotto [nome] — rimuove prodotto (privato)

<b>Per tutti</b>
/comandi — questa lista
/sito — link del sito
"""


# ============================================================
# GESTIONE COMANDI TESTUALI
# ============================================================
def handle_command(message):
    chat_id = message["chat"]["id"]
    user_id = message["from"]["id"]
    text = message.get("text", "")
    parts = text.strip().split()
    cmd = parts[0].split("@")[0]  # rimuove @NomeBot se presente
    is_private = message["chat"]["type"] == "private"

    if cmd == "/comandi":
        return send_message(chat_id, COMMAND_LIST)

    if cmd == "/sito":
        res = supabase.table("site_url_setting").select("url").eq("id", 1).execute()
        url = res.data[0]["url"] if res.data else None
        return send_message(chat_id, f"🌐 {url}" if url else "Il sito non è stato ancora impostato.")

    # ---------- Comandi privati ----------
    if is_private:
        if cmd == "/addprodotto":
            if not is_global_admin(user_id):
                return send_message(chat_id, "Non autorizzato.")
            rest = text.replace("/addprodotto", "", 1).strip()
            bits = [p.strip() for p in rest.split("|")]
            if len(bits) < 2:
                return send_message(chat_id, "Formato: /addprodotto Nome | Prezzo | Descrizione")
            name, price_str = bits[0], bits[1]
            desc = bits[2] if len(bits) > 2 else None
            try:
                price = float(price_str)
            except ValueError:
                return send_message(chat_id, "Prezzo non valido.")
            supabase.table("bot_products").insert({"name": name, "price": price, "short_description": desc}).execute()
            return send_message(chat_id, f"✅ Prodotto \"{name}\" aggiunto (€{price}).")

        if cmd == "/delprodotto":
            if not is_global_admin(user_id):
                return send_message(chat_id, "Non autorizzato.")
            name = text.replace("/delprodotto", "", 1).strip()
            supabase.table("bot_products").delete().eq("name", name).execute()
            return send_message(chat_id, f"🗑️ Prodotto \"{name}\" rimosso (se esisteva).")

        if cmd == "/start":
            return send_message(chat_id, "👋 Benvenuto in <b>Atlas Investimenti</b>!", {
                "reply_markup": {
                    "inline_keyboard": [
                        [{"text": "🛍 Acquista ora", "callback_data": "shop:list"}],
                        [{"text": "🛒 Carrello", "callback_data": "cart:view"}],
                        [{"text": "📝 Candidati", "callback_data": "apply:start"}],
                        [{"text": "🌐 Sito", "callback_data": "site:link"}],
                    ]
                }
            })

    # ---------- Comandi di gruppo ----------
    if not is_private:
        if cmd == "/setdipendenti":
            if not is_global_admin(user_id):
                return send_message(chat_id, "Non autorizzato.")
            existing = supabase.table("telegram_groups").select("*").eq("group_type", "dipendenti").execute()
            if existing.data:
                return send_message(chat_id, "Chat dipendenti già impostata.")
            supabase.table("telegram_groups").upsert({
                "chat_id": chat_id, "group_type": "dipendenti", "title": message["chat"].get("title")
            }).execute()
            return send_message(chat_id, "✅ Questa chat è ora impostata come Chat Dipendenti.")

        # ---------- Comandi /setgrafica, /setinformatica, /setinvestimenti,
        # /setedilizia, /setsubaffitti, /setlegale ----------
        if cmd in DEPARTMENT_COMMANDS:
            if not is_global_admin(user_id):
                return send_message(chat_id, "Non autorizzato.")
            group_type = DEPARTMENT_COMMANDS[cmd]
            existing = supabase.table("telegram_groups").select("*").eq("group_type", group_type).execute()
            if existing.data:
                return send_message(chat_id, f"Chat {group_type} già impostata.")
            supabase.table("telegram_groups").upsert({
                "chat_id": chat_id, "group_type": group_type, "title": message["chat"].get("title")
            }).execute()
            return send_message(chat_id, f"✅ Questa chat è ora impostata come Chat {group_type.capitalize()}.")

        if cmd == "/setmod":
            if not (is_global_admin(user_id) or is_moderator(chat_id, user_id)):
                return send_message(chat_id, "Non autorizzato.")
            target = get_target_from_reply(message)
            if not target:
                return send_message(chat_id, "Usa /setmod rispondendo al messaggio della persona da rendere moderatore.")
            supabase.table("group_moderators").upsert({
                "chat_id": chat_id, "telegram_user_id": target["id"], "telegram_username": target["username"]
            }).execute()
            return send_message(chat_id, f"✅ @{target['username']} è ora moderatore di questo gruppo.")

        mod_ok = is_moderator(chat_id, user_id)

        if cmd == "/ban":
            if not mod_ok:
                return send_message(chat_id, "Non autorizzato.")
            target = get_target_from_reply(message)
            if not target:
                return send_message(chat_id, "Usa /ban rispondendo al messaggio della persona da bannare.")
            reason = text.replace("/ban", "", 1).strip() or None
            tg("banChatMember", {"chat_id": chat_id, "user_id": target["id"]})
            supabase.table("bans").insert({
                "chat_id": chat_id, "telegram_user_id": target["id"], "reason": reason, "banned_by": user_id
            }).execute()
            extra = f" Motivo: {reason}" if reason else ""
            return send_message(chat_id, f"🔨 @{target['username']} è stato bannato.{extra}")

        if cmd == "/unban":
            if not mod_ok:
                return send_message(chat_id, "Non autorizzato.")
            if len(parts) < 2 or not parts[1].isdigit():
                return send_message(chat_id, "Usa: /unban [user_id]")
            target_id = int(parts[1])
            tg("unbanChatMember", {"chat_id": chat_id, "user_id": target_id})
            return send_message(chat_id, f"✅ Utente {target_id} sbannato.")

        if cmd == "/mute":
            if not mod_ok:
                return send_message(chat_id, "Non autorizzato.")
            target = get_target_from_reply(message)
            if not target:
                return send_message(chat_id, "Usa /mute rispondendo al messaggio della persona da mutare.")
            rest = text.replace("/mute", "", 1).strip()
            m = re.search(r"(\d+)([mhd])$", rest)
            until_date = None
            reason = rest
            if m:
                amount = int(m.group(1))
                unit = m.group(2)
                seconds = amount * 60 if unit == "m" else amount * 3600 if unit == "h" else amount * 86400
                until_date = int(time.time()) + seconds
                reason = rest[:m.start()].strip()
            payload = {
                "chat_id": chat_id, "user_id": target["id"],
                "permissions": {"can_send_messages": False},
            }
            if until_date:
                payload["until_date"] = until_date
            tg("restrictChatMember", payload)

            expires_at = datetime.fromtimestamp(until_date, tz=timezone.utc).isoformat() if until_date else None
            supabase.table("mutes").insert({
                "chat_id": chat_id, "telegram_user_id": target["id"], "reason": reason or None,
                "muted_by": user_id, "expires_at": expires_at
            }).execute()

            extra = f" Motivo: {reason}" if reason else ""
            duration_text = f" Fino a: {datetime.fromtimestamp(until_date).strftime('%d/%m/%Y %H:%M')}" if until_date else " (indeterminato)"
            return send_message(chat_id, f"🔇 @{target['username']} è stato mutato.{extra}{duration_text}")

        if cmd == "/unmute":
            if not mod_ok:
                return send_message(chat_id, "Non autorizzato.")
            target = get_target_from_reply(message)
            if not target:
                return send_message(chat_id, "Usa /unmute rispondendo al messaggio della persona da smutare.")
            tg("restrictChatMember", {
                "chat_id": chat_id, "user_id": target["id"],
                "permissions": {
                    "can_send_messages": True, "can_send_media_messages": True,
                    "can_send_other_messages": True, "can_add_web_page_previews": True
                }
            })
            return send_message(chat_id, f"🔊 @{target['username']} è stato smutato.")

        if cmd == "/warn":
            if not mod_ok:
                return send_message(chat_id, "Non autorizzato.")
            target = get_target_from_reply(message)
            if not target:
                return send_message(chat_id, "Usa /warn rispondendo al messaggio della persona da warnare.")
            reason = text.replace("/warn", "", 1).strip() or None
            supabase.table("warns").insert({
                "chat_id": chat_id, "telegram_user_id": target["id"], "reason": reason, "warned_by": user_id
            }).execute()
            count_res = supabase.table("warns").select("id", count="exact").eq("chat_id", chat_id).eq("telegram_user_id", target["id"]).execute()
            count = count_res.count or 0
            extra = f" Motivo: {reason}" if reason else ""
            return send_message(chat_id, f"⚠️ @{target['username']} ha ricevuto un warning ({count}/3).{extra}")

        if cmd == "/unwarn":
            if not mod_ok:
                return send_message(chat_id, "Non autorizzato.")
            target = get_target_from_reply(message)
            if not target:
                return send_message(chat_id, "Usa /unwarn rispondendo al messaggio della persona.")
            last = supabase.table("warns").select("id").eq("chat_id", chat_id).eq("telegram_user_id", target["id"]).order("created_at", desc=True).limit(1).execute()
            if not last.data:
                return send_message(chat_id, "Nessun warning da rimuovere.")
            supabase.table("warns").delete().eq("id", last.data[0]["id"]).execute()
            return send_message(chat_id, f"✅ Rimosso un warning da @{target['username']}.")

        if cmd == "/personal":
            if not mod_ok:
                return send_message(chat_id, "Non autorizzato.")
            reply_text = message.get("reply_to_message", {}).get("text")
            command_name = text.replace("/personal", "", 1).strip().lstrip("/")
            if not reply_text or not command_name:
                return send_message(chat_id, "Usa /personal [nomecomando] rispondendo al messaggio con il testo da salvare.")
            supabase.table("personal_commands").upsert({
                "chat_id": chat_id, "command": command_name, "response_text": reply_text, "created_by": user_id
            }).execute()
            return send_message(chat_id, f"✅ Comando /{command_name} impostato.")

        if cmd == "/unpersonal":
            if not mod_ok:
                return send_message(chat_id, "Non autorizzato.")
            command_name = text.replace("/unpersonal", "", 1).strip().lstrip("/")
            supabase.table("personal_commands").delete().eq("chat_id", chat_id).eq("command", command_name).execute()
            return send_message(chat_id, f"🗑️ Comando /{command_name} rimosso.")

        if cmd == "/setsite":
            if not mod_ok:
                return send_message(chat_id, "Non autorizzato.")
            url = text.replace("/setsite", "", 1).strip()
            if not url:
                return send_message(chat_id, "Usa: /setsite https://tuosito.com")
            supabase.table("site_url_setting").update({"url": url, "set_by": user_id}).eq("id", 1).execute()
            return send_message(chat_id, f"✅ Sito impostato: {url}")

        # ---------- Comando personalizzato ----------
        command_name = cmd.lstrip("/")
        personal = supabase.table("personal_commands").select("response_text").eq("chat_id", chat_id).eq("command", command_name).execute()
        if personal.data:
            return send_message(chat_id, personal.data[0]["response_text"])


# ============================================================
# GESTIONE TESTO DURANTE UN FLUSSO (candidatura multi-step)
# ============================================================
def handle_session_text(message):
    chat_id = message["chat"]["id"]
    text = message.get("text", "").strip()
    session = get_session(chat_id)
    if not session or not text:
        return False

    step = session["current_step"]
    data = session["data_json"] or {}

    if step == "apply_sector":
        data["sector"] = text
        set_session(chat_id, "apply_motivation", data)
        send_message(chat_id, "Perché ti stai candidando?")
        return True

    if step == "apply_motivation":
        data["motivation"] = text
        set_session(chat_id, "apply_experience", data)
        send_message(chat_id, "Hai già avuto esperienze in questo settore? (sì/no)")
        return True

    if step == "apply_experience":
        has_exp = text.lower().startswith(("si", "sì"))
        data["has_experience"] = has_exp
        if has_exp:
            set_session(chat_id, "apply_experience_details", data)
            send_message(chat_id, "Quali esperienze hai avuto?")
        else:
            finalize_application(chat_id, message["from"], data)
        return True

    if step == "apply_experience_details":
        data["experience_details"] = text
        finalize_application(chat_id, message["from"], data)
        return True

    return False


def finalize_application(chat_id, from_user, data):
    username = from_user.get("username") or from_user.get("first_name")
    supabase.table("applications").insert({
        "telegram_chat_id": chat_id,
        "telegram_username": username,
        "sector": data.get("sector"),
        "motivation": data.get("motivation"),
        "has_experience": data.get("has_experience"),
        "experience_details": data.get("experience_details") if data.get("has_experience") else None,
    }).execute()
    clear_session(chat_id)
    send_message(chat_id, "✅ Candidatura inviata! Ti contatteremo presto.")

    text = (
        "📝 <b>Avviso Candidatura</b>\n"
        f"Nickname: —\n"
        f"@ telegram: {username}\n"
        f"Settore: {data.get('sector')}\n"
        f"Motivazione: {data.get('motivation')}\n"
        f"Esperienza pregressa: {'Sì — ' + (data.get('experience_details') or '') if data.get('has_experience') else 'No'}"
    )
    notify_department_group(data.get("sector"), text)


# ============================================================
# CALLBACK QUERY (bottoni)
# ============================================================
def handle_callback(callback):
    chat_id = callback["message"]["chat"]["id"]
    message_id = callback["message"]["message_id"]
    data_str = callback["data"]

    answer_callback(callback["id"])

    if data_str == "shop:list":
        products = supabase.table("bot_products").select("*").order("created_at").execute().data
        if not products:
            return send_message(chat_id, "Nessun prodotto disponibile al momento.")
        buttons = [[{"text": f"{p['name']} — €{p['price']}", "callback_data": f"shop:add:{p['id']}"}] for p in products]
        return send_message(chat_id, "🛍 <b>Catalogo prodotti</b>", {"reply_markup": {"inline_keyboard": buttons}})

    if data_str.startswith("shop:add:"):
        product_id = data_str.replace("shop:add:", "")
        existing = supabase.table("bot_cart_items").select("*").eq("telegram_chat_id", chat_id).eq("bot_product_id", product_id).execute()
        if existing.data:
            supabase.table("bot_cart_items").update({"quantity": existing.data[0]["quantity"] + 1}).eq("id", existing.data[0]["id"]).execute()
        else:
            supabase.table("bot_cart_items").insert({"telegram_chat_id": chat_id, "bot_product_id": product_id, "quantity": 1}).execute()
        return send_message(chat_id, "✅ Aggiunto al carrello.")

    if data_str == "cart:view":
        return render_cart(chat_id)

    if data_str.startswith(("cart:inc:", "cart:dec:", "cart:del:")):
        item_id = data_str.split(":")[2]
        item_res = supabase.table("bot_cart_items").select("*").eq("id", item_id).execute()
        if item_res.data:
            item = item_res.data[0]
            if data_str.startswith("cart:inc:"):
                supabase.table("bot_cart_items").update({"quantity": item["quantity"] + 1}).eq("id", item_id).execute()
            elif data_str.startswith("cart:dec:"):
                if item["quantity"] <= 1:
                    supabase.table("bot_cart_items").delete().eq("id", item_id).execute()
                else:
                    supabase.table("bot_cart_items").update({"quantity": item["quantity"] - 1}).eq("id", item_id).execute()
            else:
                supabase.table("bot_cart_items").delete().eq("id", item_id).execute()
        return render_cart(chat_id, message_id)

    if data_str == "cart:checkout":
        items = supabase.table("bot_cart_items").select("*, bot_products(*)").eq("telegram_chat_id", chat_id).execute().data
        if not items:
            return send_message(chat_id, "Il carrello è vuoto.")
        items_json = [{"name": i["bot_products"]["name"], "price": i["bot_products"]["price"], "quantity": i["quantity"]} for i in items]
        total = sum(i["price"] * i["quantity"] for i in items_json)
        username = callback["from"].get("username") or callback["from"].get("first_name")

        supabase.table("bot_orders").insert({
            "telegram_chat_id": chat_id, "telegram_username": username, "items_json": items_json, "total": total
        }).execute()
        supabase.table("bot_cart_items").delete().eq("telegram_chat_id", chat_id).execute()

        send_message(chat_id, "✅ Ordine effettuato! Ti contatteremo a breve.")
        items_list = ", ".join(f"{i['name']} x{i['quantity']}" for i in items_json)
        return

    if data_str == "apply:start":
        set_session(chat_id, "apply_sector", {})
        return send_message(chat_id, "Per quale settore ti vuoi candidare?")

    if data_str == "site:link":
        res = supabase.table("site_url_setting").select("url").eq("id", 1).execute()
        url = res.data[0]["url"] if res.data else None
        return send_message(chat_id, f"🌐 {url}" if url else "Il sito non è stato ancora impostato.")

    if data_str.startswith("order:take:"):
        order_id = data_str.replace("order:take:", "")
        username = callback["from"].get("username") or callback["from"].get("first_name")
        supabase.rpc("take_order", {"p_order_id": order_id, "p_username": username}).execute()
        original_text = callback["message"].get("text", "")
        return edit_message(chat_id, message_id, f"{original_text}\n\n✅ Preso in carico da @{username}")

    if data_str.startswith(("booking:accept:", "booking:reject:")):
        booking_id = data_str.split(":")[2]
        accepted = data_str.startswith("booking:accept:")
        supabase.table("apartment_bookings").update({"accepted": accepted}).eq("id", booking_id).execute()
        original_text = callback["message"].get("text", "")
        outcome = "✅ Prenotazione accettata" if accepted else "❌ Prenotazione rifiutata"
        return edit_message(chat_id, message_id, f"{original_text}\n\n{outcome}")


def render_cart(chat_id, edit_msg_id=None):
    items = supabase.table("bot_cart_items").select("*, bot_products(*)").eq("telegram_chat_id", chat_id).execute().data
    if not items:
        return send_message(chat_id, "🛒 Il carrello è vuoto.")

    text = "🛒 <b>Il tuo carrello</b>\n\n"
    buttons = []
    total = 0
    for i in items:
        p = i["bot_products"]
        total += p["price"] * i["quantity"]
        text += f"{p['name']} — €{p['price']} x{i['quantity']}\n"
        buttons.append([
            {"text": "➖", "callback_data": f"cart:dec:{i['id']}"},
            {"text": str(i["quantity"]), "callback_data": "noop"},
            {"text": "➕", "callback_data": f"cart:inc:{i['id']}"},
            {"text": "🗑️", "callback_data": f"cart:del:{i['id']}"},
        ])
    text += f"\n<b>Totale: €{total:.2f}</b>"
    buttons.append([{"text": "✅ Acquista tutto", "callback_data": "cart:checkout"}])

    payload = {"reply_markup": {"inline_keyboard": buttons}}
    if edit_msg_id:
        return edit_message(chat_id, edit_msg_id, text, payload)
    return send_message(chat_id, text, payload)


# ============================================================
# NOTIFICHE VERSO I GRUPPI
# ============================================================
def notify_employee_group(text, extra=None):
    """Manda alla chat 'dipendenti' generica (usata per casi non legati a un reparto specifico)."""
    res = supabase.table("telegram_groups").select("chat_id").eq("group_type", "dipendenti").execute()
    if not res.data:
        return
    return send_message(res.data[0]["chat_id"], text, extra)


def notify_department_group(department, text, extra=None):
    """Manda la notifica al gruppo del reparto specifico (es. 'Grafica', 'Legale')."""
    group_type = DEPARTMENT_NAME_TO_GROUP_TYPE.get(department)
    if not group_type:
        return  # reparto non mappato (es. Editoriale), nessuna notifica
    res = supabase.table("telegram_groups").select("chat_id").eq("group_type", group_type).execute()
    if not res.data:
        return  # gruppo non ancora impostato per questo reparto
    return send_message(res.data[0]["chat_id"], text, extra)


# ============================================================
# WEBHOOK DAL DATABASE (ordini/prenotazioni/candidature fatti dal sito)
# ============================================================
def handle_database_webhook(body):
    event_type = body.get("type")
    table = body.get("table")
    record = body.get("record", {})

    if event_type != "INSERT":
        return jsonify({"status": "ignored"}), 200

    if table == "orders":
        product = supabase.table("products").select("name, price, category").eq("id", record["product_id"]).execute().data
        product = product[0] if product else {}
        customer = None
        if record.get("customer_id"):
            c = supabase.table("customers").select("nickname, telegram").eq("id", record["customer_id"]).execute().data
            customer = c[0] if c else None
        nickname = customer["nickname"] if customer else "Cliente Telegram"
        telegram_handle = customer["telegram"] if customer else (record.get("telegram_username") or "—")
        total = (product.get("price") or 0) * (record.get("quantity") or 1)

        text = (
            "🛒 <b>Avviso Ordine</b>\n"
            f"Nickname: {nickname}\n"
            f"@ telegram: {telegram_handle}\n"
            f"Prodotto: {product.get('name')}\n"
            f"Quantità: {record.get('quantity', 1)}\n"
            f"Totale: €{total:.2f}"
        )
        notify_department_group(
            product.get("category"), text,
            {"reply_markup": {"inline_keyboard": [[{"text": "✅ Prendi in carico", "callback_data": f"order:take:{record['id']}"}]]}}
        )

    if table == "applications":
        customer = None
        if record.get("customer_id"):
            c = supabase.table("customers").select("nickname, telegram").eq("id", record["customer_id"]).execute().data
            customer = c[0] if c else None
        nickname = customer["nickname"] if customer else "Cliente Telegram"
        telegram_handle = customer["telegram"] if customer else (record.get("telegram_username") or "—")

        text = (
            "📝 <b>Avviso Candidatura</b>\n"
            f"Nickname: {nickname}\n"
            f"@ telegram: {telegram_handle}\n"
            f"Settore: {record.get('sector')}\n"
            f"Motivazione: {record.get('motivation')}\n"
            f"Esperienza pregressa: {'Sì — ' + (record.get('experience_details') or '') if record.get('has_experience') else 'No'}"
        )
        notify_department_group(record.get("sector"), text)

    if table == "apartment_bookings":
        apartment = supabase.table("apartments").select("name").eq("id", record["apartment_id"]).execute().data
        apartment = apartment[0] if apartment else {}
        customer = None
        if record.get("customer_id"):
            c = supabase.table("customers").select("nickname, telegram").eq("id", record["customer_id"]).execute().data
            customer = c[0] if c else None
        who = f"{customer['nickname']} ({customer['telegram']})" if customer else (record.get("telegram_username") or "Cliente Telegram")

        notify_department_group(
            "Subaffitti",
            f"🏠 <b>Nuova prenotazione subaffitto</b>\nCliente: {who}\nAppartamento: {apartment.get('name')}",
            {"reply_markup": {"inline_keyboard": [[
                {"text": "✅ Accetta", "callback_data": f"booking:accept:{record['id']}"},
                {"text": "❌ Rifiuta", "callback_data": f"booking:reject:{record['id']}"},
            ]]}}
        )

    return jsonify({"status": "ok"}), 200


# ============================================================
# ENTRY POINT (Flask routes)
# ============================================================
@app.route("/", methods=["GET"])
def health():
    return "Atlas Investimenti bot is running.", 200


@app.route("/webhook", methods=["POST"])
def webhook():
    body = request.get_json(force=True, silent=True) or {}

    # Caso B: webhook dal database Supabase (non ha "update_id")
    if body.get("type") and body.get("table"):
        return handle_database_webhook(body)

    # Caso A: update di Telegram — verifica il secret token
    secret_header = request.headers.get("X-Telegram-Bot-Api-Secret-Token")
    if secret_header != WEBHOOK_SECRET:
        return jsonify({"error": "unauthorized"}), 401

    try:
        if "message" in body:
            message = body["message"]
            if message.get("text", "").startswith("/"):
                handle_command(message)
            else:
                handle_session_text(message)
        elif "callback_query" in body:
            handle_callback(body["callback_query"])
    except Exception as e:
        print("ERRORE:", e)

    return jsonify({"status": "ok"}), 200


if __name__ == "__main__":
    port = int(os.environ.get("PORT", 5000))
    app.run(host="0.0.0.0", port=port)
