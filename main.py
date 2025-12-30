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
    except: return None

async def transcribe_audio(audio_bytes):
    try:
        transcription = await openai_client.audio.transcriptions.create(
            model="whisper-1", file=("audio.ogg", audio_bytes)
        )
        return transcription.text
    except: return ""

# --- 🧠 LÓGICA DE DATOS Y MENÚ ---

def get_menu_text():
    try:
        response = supabase.table("products").select("name, price, description").eq("is_active", True).execute()
        items = response.data
        if not items: return "Menú en actualización."
        menu_str = "NUESTRO MENÚ:\n"
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
        
        # Alerta al Gerente
        if MANAGER_PHONE:
            msg_g = f"💰 ¡VENTA NUEVA!\nOrden: {order_num}\nTotal: ${total}\nItems: {detalle}\nCliente: {phone}"
            await send_whatsapp_message(MANAGER_PHONE, msg_g)
            
        return f"🎉 ¡Pedido {order_num} confirmado!\n\n🍕 {detalle}\n💰 Total: ${total}\n📍 Dirección: {direccion}\n\n¡Ya estamos en la cocina! 🔥"
    except: return "Perdón, tuve un error al guardar tu pedido. ¿Podemos intentar de nuevo?"

# --- 🏍️ LÓGICA DE REPARTIDOR ---

async def handle_driver_action(driver_phone):
    """Cierra la orden activa del chofer"""
    try:
        resp = supabase.table("orders").select("*").eq("driver_phone", driver_phone).eq("status", "delivering").execute()
        if resp.data:
            order = resp.data[0]
            supabase.table("orders").update({
                "status": "completed", "completed_at": datetime.now().isoformat()
            }).eq("id", order['id']).execute()
            return f"✅ Orden {order['order_number']} cerrada. ¡Excelente servicio! 🫡"
        return "No tienes órdenes pendientes de entrega. 🤔"
    except: return "Error al procesar el cierre."

# --- 🤖 EL CEREBRO (GPT-4 CON MEMORIA) ---

async def ask_gpt4_client(user_message: str, user_phone: str, is_audio=False):
    current_menu = get_menu_text()
    
    # 1. Guardar y Obtener Memoria
    try:
        note = "[AUDIO] " if is_audio else ""
        supabase.table("chat_history").insert({"phone_number": user_phone, "role": "user", "content": f"{note}{user_message}"}).execute()
        
        hist_resp = supabase.table("chat_history").select("role, content").eq("phone_number", user_phone).order("created_at", desc=True).limit(10).execute()
        history = [{"role": h["role"], "content": h["content"]} for h in hist_resp.data][::-1]
    except: history = [{"role": "user", "content": user_message}]

    # 2. Herramientas
    tools = [{
        "type": "function",
        "function": {
            "name": "registrar_pedido",
            "description": "Solo cuando el cliente confirme items, precio y dirección.",
            "parameters": {
                "type": "object",
                "properties": {
                    "detalle": {"type": "string"}, "total": {"type": "number"},
                    "direccion": {"type": "string"}, "metodo_pago": {"type": "string"}
                },
                "required": ["detalle", "total", "direccion"]
            }
        }
    }]

    # 3. Prompt NPS 100
    system_prompt = f"""
    Eres Komo, el asistente del restaurante. Tu meta es un NPS de 100.
    Sé extremadamente amable, empático y proactivo.
    REGLAS:
    - RECUERDA lo que el cliente pidió antes (mira el historial). 
    - Si manda ubicación (GPS), úsala como dirección.
    - {current_menu}
    """

    messages = [{"role": "system", "content": system_prompt}] + history

    try:
        response = await openai_client.chat.completions.create(
            model="gpt-4-turbo", messages=messages, tools=tools, tool_choice="auto"
        )
        msg = response.choices[0].message
        reply = msg.content

        if msg.tool_calls:
            args = json.loads(msg.tool_calls[0].function.arguments)
            reply = await registrar_pedido_db(user_phone, args["detalle"], args["total"], args["direccion"], args.get("metodo_pago", "efectivo"))

        if reply:
            supabase.table("chat_history").insert({"phone_number": user_phone, "role": "assistant", "content": reply}).execute()
        return reply
    except: return "Lo siento, ¿puedes repetir eso? 🍕"

# --- 🚀 FASTAPI ---
@asynccontextmanager
async def lifespan(app: FastAPI):
    print("🚀 KOMO MASTER BUILD v8.5 LIVE")
    yield

app = FastAPI(lifespan=lifespan)

@app.get("/webhook")
async def verify(request: Request):
    if request.query_params.get("hub.verify_token") == VERIFY_TOKEN:
        return int(request.query_params.get("hub.challenge"))
    raise HTTPException(status_code=403)

@app.post("/webhook")
async def webhook(request: Request):
    try:
        body = await request.json()
        messages = body.get("entry", [])[0].get("changes", [])[0].get("value", {}).get("messages", [])
        
        if messages:
            msg = messages[0]
            sender = msg["from"]
            msg_type = msg["type"]

            # ¿Es Repartidor?
            dr_check = supabase.table("drivers").select("id").eq("phone_number", sender).execute()
            is_driver = len(dr_check.data) > 0

            if is_driver:
                reply = await handle_driver_action(sender)
                await send_whatsapp_message(sender, reply)
            else:
                # Flujo Cliente
                content = ""
                if msg_type == "text": content = msg["text"]["body"]
                elif msg_type == "audio":
                    audio = await download_whatsapp_media(msg["audio"]["id"])
                    content = await transcribe_audio(audio)
                elif msg_type == "location":
                    lat, lon = msg["location"]["latitude"], msg["location"]["longitude"]
                    content = f"Mi ubicación: http://maps.google.com/?q={lat},{lon}"

                if content:
                    reply = await ask_gpt4_client(content, sender, is_audio=(msg_type=="audio"))
                    await send_whatsapp_message(sender, reply)

        return {"status": "ok"}
    except: return {"status": "error"}