# ============================================================
# PATCH: aggiungi questo blocco dentro handle_command(),
# nella sezione "if not is_private:" insieme agli altri comandi
# di gruppo come /setdipendenti
# ============================================================

DEPARTMENT_COMMANDS = {
    "/setgrafica": "grafica",
    "/setinformatica": "informatica",
    "/setinvestimenti": "investimenti",
    "/setedilizia": "edilizia",
    "/setsubaffitti": "subaffitti",
    "/setlegale": "legale",
}

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


# ============================================================
# PATCH: sostituisci la funzione notify_employee_group esistente
# con queste due funzioni (routing per reparto)
# ============================================================

def notify_department_group(department, text, extra=None):
    """Manda la notifica al gruppo del reparto specifico (es. 'Grafica', 'Legale')."""
    dept_map = {
        "Grafica": "grafica",
        "Informatica": "informatica",
        "Investimenti": "investimenti",
        "Edile": "edilizia",
        "Subaffitti": "subaffitti",
        "Legale": "legale",
    }
    group_type = dept_map.get(department)
    if not group_type:
        return  # reparto non mappato (es. Editoriale), nessuna notifica
    res = supabase.table("telegram_groups").select("chat_id").eq("group_type", group_type).execute()
    if not res.data:
        return  # gruppo non ancora impostato per questo reparto
    return send_message(res.data[0]["chat_id"], text, extra)


# ============================================================
# PATCH: sostituisci il blocco "if table == 'applications':"
# dentro handle_database_webhook() con questo
# ============================================================

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
        f"Esperienza pregressa: {'Sì — ' + record.get('experience_details', '') if record.get('has_experience') else 'No'}"
    )
    notify_department_group(record.get("sector"), text)


# ============================================================
# PATCH: sostituisci il blocco "if table == 'orders':"
# dentro handle_database_webhook() con questo
# ============================================================

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
