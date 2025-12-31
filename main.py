import os
import httpx
import json
import time
from contextlib import asynccontextmanager
from fastapi import FastAPI, Request, HTTPException
from supabase import create_client, Client
from openai import AsyncOpenAI
from datetime import datetime

# --- 🔐 CONFIGURACIÓN ---
WHATSAPP_TOKEN = os.environ.get("WHATSAPP_TOKEN")
PHONE_NUMBER_ID = os.environ.get("PHONE_NUMBER_ID")
VERIFY_TOKEN = os.environ.get("VERIFY_TOKEN", "KOMO_TOKEN_2025")
OPENAI_API_KEY = os.environ.get("OPENAI_API_KEY")
SUPABASE_URL = os.environ.get("SUPABASE_URL")
SUPABASE_KEY = os.environ.get("SUPABASE_KEY")
MANAGER_PHONE = os.environ.get("MANAGER_PHONE")

if not SUPABASE_URL or not SUPABASE_KEY:
    raise ValueError("SUPABASE_URL and SUPABASE_KEY environment variables must be set")
if not OPENAI_API_KEY:
    raise ValueError("OPENAI_API_KEY environment variable must be set")

supabase: Client = create_client(SUPABASE_URL, SUPABASE_KEY)
openai_client: AsyncOpenAI = AsyncOpenAI(api_key=OPENAI_API_KEY)

# --- 🛠️ UTILIDADES WHATSAPP ---

async def send_whatsapp_message(to_number: str, text_body: str):
    if not WHATSAPP_TOKEN: return
    url = f"https://graph.facebook.com/v17.0/{PHONE_NUMBER_ID}/messages"
    headers = {"Authorization": f"Bearer {WHATSAPP_TOKEN}", "Content-Type": "application/json"}
    data = {"messaging_product": "whatsapp", "to": to_number, "type": "text", "text": {"body": text_body}}
    async with httpx.AsyncClient() as client:
        await client.post(url, headers=headers, json=data)

async def download_whatsapp_media(media_id: str):
    try:
        async with httpx.AsyncClient() as client:
            url_info = f"https://graph.facebook.com/v17.0/{media_id}"
            headers = {"Authorization": f"Bearer {WHATSAPP_TOKEN}"}
            resp_info = await client.get(url_info, headers=headers)
            media_url = resp_info.json().get("url")
            if not media_url: return None
            resp_media = await client.get(media_url, headers=headers)
            return resp_media.content
    except Exception as e:
        print(f"Error downloading media: {e}")
        return None

async def transcribe_audio(audio_bytes):
    try:
        transcription = await openai_client.audio.transcriptions.create(
            model="whisper-1", file=("audio.ogg", audio_bytes)
        )
        return transcription.text
    except Exception as e:
        print(f"Error transcribing audio: {e}")
        return ""

# --- 🧠 LÓGICA DE DATOS Y MENÚ ---

def get_menu_text():
    try:
        response = supabase.table("products").select("name, price, description").eq("is_active", True).execute()
        items = response.data
        if not items: return "Menú en actualización."
        menu_str = "MENÚ DE KOMO FAST FOOD:\n"
        for item in items:
            menu_str += f"- {item['name']}: ${item['price']} ({item.get('description','')})\n"
        return menu_str
    except: return "Consultar disponibilidad."

async def registrar_pedido_db(phone, detalle, total, direccion, metodo_pago):
    order_num = f"ORD-{int(time.time())}"
    try:
        data = {
            "order_number": order_num, "customer_phone": phone, "order_details": detalle,
            "total_price": total, "delivery_address": direccion, "status": "confirmed"
        }
        supabase.table("orders").insert(data).execute()
        
        if MANAGER_PHONE:
            msg_g = f"💰 ¡VENTA NUEVA!\nOrden: {order_num}\nTotal: ${total}\nItems: {detalle}\nCliente: {phone}"
            await send_whatsapp_message(MANAGER_PHONE, msg_g)
            
        return f"🎉 ¡Pedido {order_num} confirmado!\n\n🍕 {detalle}\n💰 Total: ${total}\n📍 Dirección: {direccion}\n\n¡En Komo Fast Food ya estamos preparando tu orden! 🔥"
    except Exception as e:
        print(f"Error registering order: {e}")
        return "Perdón, tuve un error al guardar tu pedido. ¿Podemos intentar de nuevo?"

# --- 🏍️ LÓGICA DE REPARTIDOR, EVIDENCIA Y NPS MEJORADO ---

async def handle_driver_action(driver_phone, text_content):
    """Lógica proactiva: Si el repartidor pide ayuda, el bot actúa como puente con el cliente"""
    keywords = ["encuentro", "referencia", "ubicacion", "numero", "afuera", "puerta"]
    if any(word in text_content.lower() for word in keywords):
        resp = supabase.table("orders").select("customer_phone, order_number").eq("driver_phone", driver_phone).eq("status", "delivering").execute()
        if resp.data:
            customer = resp.data[0]['customer_phone']
            order_n = resp.data[0]['order_number']
            msg_ayuda = f"🛵 *AVISO DE TU REPARTIDOR (Orden {order_n}):*\nEstamos cerca de tu domicilio pero tenemos una duda: '{text_content}'.\n\n¿Nos podrías dar una referencia rápida o descripción de tu fachada? Gracias."
            await send_whatsapp_message(customer, msg_ayuda)
            return "Entendido, ya le solicité la referencia al cliente. Te aviso en cuanto me responda. 🫡"
    return "Recibido. Recuerda enviar la foto de entrega para finalizar la orden."

async def handle_driver_media(driver_phone, media_id):
    try:
        resp = supabase.table("orders").select("id, order_number, customer_phone").eq("driver_phone", driver_phone).eq("status", "delivering").execute()
        if not resp.data:
            return "No tienes órdenes pendientes para subir evidencia. 🤔"
        
        order = resp.data[0]
        photo_bytes = await download_whatsapp_media(media_id)
        
        if photo_bytes:
            file_path = f"evidencias/evidencia_{order['order_number']}.jpg"
            supabase.storage.from_("evidencias").upload(file_path, photo_bytes, {"content-type": "image/jpeg"})
            photo_url = supabase.storage.from_("evidencias").get_public_url(file_path)
            
            supabase.table("orders").update({
                "status": "completed", 
                "completed_at": datetime.now().isoformat(),
                "delivery_photo_url": photo_url
            }).eq("id", order['id']).execute()
            
            await send_nps_survey(order['customer_phone'], order['order_number'])
            return f"✅ Evidencia guardada y Orden {order['order_number']} finalizada. ¡Excelente servicio! 🫡"
        return "Error al descargar la foto."
    except Exception as e:
        print(f"Error handling media: {e}")
        return "Error al procesar la evidencia."

async def send_nps_survey(customer_phone, order_num):
    msg = (f"¡Tu pedido {order_num} ha llegado! 🍕\n\n"
           "Ayúdanos a mejorar. ¿Qué tan satisfecho estás? "
           "Responde solo con un número:\n"
           "5 - Excelente ⭐\n1 - Malo 😡")
    await send_whatsapp_message(customer_phone, msg)

async def save_nps_rating(customer_phone, rating_text):
    if rating_text.strip().isdigit() and int(rating_text) in [1,2,3,4,5]:
        score = int(rating_text)
        order = supabase.table("orders").select("id, order_number").eq("customer_phone", customer_phone).eq("status", "completed").order("completed_at", desc=True).limit(1).execute()
        if order.data:
            supabase.table("orders").update({"rating": score}).eq("id", order.data[0]['id']).execute()
            
            if score <= 3:
                if MANAGER_PHONE:
                    await send_whatsapp_message(MANAGER_PHONE, f"🚨 *ALERTA NPS BAJO*\nCliente: {customer_phone}\nCalificación: {score}\nOrden: {order.data[0]['order_number']}\nFavor de revisar el chat.")
                return "Lamentamos mucho que tu experiencia no fuera de 5 estrellas. 😔 He notificado al gerente para revisar tu caso personalmente. ¿Podrías decirme qué sucedió?"
            else:
                return "¡Muchas gracias por tu calificación! 😊 Nos motiva a seguir dándote el mejor servicio."
    return None

# --- 🤖 EL CEREBRO GPT-4 ---

async def ask_gpt4_client(user_message: str, user_phone: str, is_audio=False):
    current_menu = get_menu_text()
    
    try:
        note = "[AUDIO] " if is_audio else ""
        supabase.table("chat_history").insert({"phone_number": user_phone, "role": "user", "content": f"{note}{user_message}"}).execute()
        hist_resp = supabase.table("chat_history").select("role, content").eq("phone_number", user_phone).order("created_at", desc=True).limit(8).execute()
        history = [{"role": h["role"], "content": h["content"]} for h in hist_resp.data][::-1]
    except Exception as e:
        print(f"History error: {e}")
        history = [{"role": "user", "content": user_message}]

    tools = [{"type": "function", "function": {"name": "registrar_pedido", "description": "Cuando el cliente confirme items, precio y dirección.", "parameters": {"type": "object", "properties": {"detalle": {"type": "string"}, "total": {"type": "number"}, "direccion": {"type": "string"}}, "required": ["detalle", "total", "direccion"]}}}]

    system_prompt = f"""
    Eres Komo, el asistente virtual de Komo Fast Food. Tu meta es un NPS de 100.
    - SALUDO: Di "Gracias por comunicarte a Komo Fast Food" SOLO si es el inicio de la charla.
    - AMABILIDAD: Si el cliente está molesto (ej: "ya se tardó"), discúlpate sinceramente y avísale que estás revisando el estatus con el repartidor.
    - LOGÍSTICA: Si el cliente da una referencia, confírmala amablemente.
    - GERENTE: Si ves "GERENTE DICE:", el humano tiene el mando, respétalo y no intentes vender más.
    {current_menu}
    """

    try:
        response = await openai_client.chat.completions.create(model="gpt-4-turbo", messages=[{"role": "system", "content": system_prompt}] + history, tools=tools, tool_choice="auto")
        msg = response.choices[0].message
        reply = msg.content

        if msg.tool_calls:
            args = json.loads(msg.tool_calls[0].function.arguments)
            reply = await registrar_pedido_db(user_phone, args["detalle"], args["total"], args["direccion"], "efectivo")

        if reply:
            supabase.table("chat_history").insert({"phone_number": user_phone, "role": "assistant", "content": reply}).execute()
        return reply
    except:
        return "Gracias por comunicarte a Komo Fast Food. ¿Podrías repetirme eso? 🍕"

# --- 🚀 FASTAPI ---
@asynccontextmanager
async def lifespan(app: FastAPI):
    print("🚀 KOMO MASTER BUILD v9.2 LIVE - GESTIÓN PROACTIVA")
    yield

app = FastAPI(lifespan=lifespan)

@app.get("/webhook")
async def verify(request: Request):
    if request.query_params.get("hub.verify_token") == VERIFY_TOKEN:
        challenge = request.query_params.get("hub.challenge")
        return int(challenge) if challenge else 0
    raise HTTPException(status_code=403)

@app.post("/webhook")
async def webhook(request: Request):
    try:
        body = await request.json()
        entry = body.get("entry", [])[0]
        changes = entry.get("changes", [])[0]
        value = changes.get("value", {})
        messages = value.get("messages", [])
        
        if messages:
            msg = messages[0]
            sender = msg.get("from")
            msg_type = msg.get("type")
            
            dr_check = supabase.table("drivers").select("id").eq("phone_number", sender).execute()
            is_driver = len(dr_check.data) > 0

            # 1. Lógica Repartidor
            if is_driver:
                if msg_type == "image":
                    reply = await handle_driver_media(sender, msg["image"]["id"])
                else:
                    content = msg.get("text", {}).get("body", "")
                    reply = await handle_driver_action(sender, content)
                await send_whatsapp_message(sender, reply)

            # 2. Lógica Cliente
            else:
                if msg_type == "text":
                    content = msg["text"]["body"]
                    nps_reply = await save_nps_rating(sender, content)
                    if nps_reply:
                        await send_whatsapp_message(sender, nps_reply)
                    else:
                        reply = await ask_gpt4_client(content, sender)
                        await send_whatsapp_message(sender, reply)
                
                elif msg_type in ["audio", "location"]:
                    content = ""
                    if msg_type == "audio":
                        audio = await download_whatsapp_media(msg["audio"]["id"])
                        if audio: content = await transcribe_audio(audio)
                    else:
                        loc = msg["location"]
                        content = f"Mi ubicación: https://www.google.com/maps?q={loc['latitude']},{loc['longitude']}"
                    
                    if content:
                        reply = await ask_gpt4_client(content, sender, is_audio=(msg_type=="audio"))
                        await send_whatsapp_message(sender, reply)

        return {"status": "ok"}
    except Exception as e:
        print(f"Webhook error: {e}")
        return {"status": "error"}