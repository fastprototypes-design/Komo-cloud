import os
import httpx
import json
import time
from contextlib import asynccontextmanager
from fastapi import FastAPI, Request, HTTPException
from supabase import create_client, Client
from openai import AsyncOpenAI
from datetime import datetime

# --- CONFIG ---
WHATSAPP_TOKEN = os.environ.get("WHATSAPP_TOKEN")
PHONE_NUMBER_ID = os.environ.get("PHONE_NUMBER_ID")
VERIFY_TOKEN = os.environ.get("VERIFY_TOKEN", "KOMO_TOKEN_2025")
OPENAI_API_KEY = os.environ.get("OPENAI_API_KEY")
SUPABASE_URL = os.environ.get("SUPABASE_URL")
SUPABASE_KEY = os.environ.get("SUPABASE_KEY")
MANAGER_PHONE = os.environ.get("MANAGER_PHONE")

supabase: Client = create_client(SUPABASE_URL, SUPABASE_KEY)
openai_client: AsyncOpenAI = AsyncOpenAI(api_key=OPENAI_API_KEY)

# --- UTILIDADES ---
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
        return (await openai_client.audio.transcriptions.create(model="whisper-1", file=("audio.ogg", audio_bytes))).text
    except: return ""

# --- LÓGICA DE REPARTIDOR (NUEVO) ---
async def handle_driver_action(driver_phone, message_type):
    """Si es chofer y manda evidencia, cierra la orden activa"""
    try:
        # Buscar orden activa de este chofer
        response = supabase.table("orders").select("*").eq("driver_phone", driver_phone).eq("status", "delivering").execute()
        orders = response.data
        
        if orders:
            order = orders[0]
            # CERRAR ORDEN
            supabase.table("orders").update({
                "status": "completed",
                "completed_at": datetime.now().isoformat()
            }).eq("id", order['id']).execute()
            
            return f"✅ Orden {order['order_number']} cerrada correctamente. ¡Buen trabajo! 🫡"
        else:
            return "No tienes órdenes activas asignadas. 🤔"
    except Exception as e:
        print(f"Error Driver: {e}")
        return "Error cerrando orden."

# --- LÓGICA DE CLIENTE ---
def get_menu_text():
    try:
        response = supabase.table("products").select("name, price, description").eq("is_active", True).execute()
        items = response.data
        menu_str = "MENÚ:\n"
        for item in items: menu_str += f"- {item['name']}: ${item['price']}\n"
        return menu_str
    except: return "Consultar menú."

async def registrar_pedido_db(phone, detalle, total, direccion, metodo_pago):
    order_num = f"ORD-{int(time.time())}"
    try:
        data = {"order_number": order_num, "customer_phone": phone, "order_details": detalle, "total_price": total, "delivery_address": direccion, "status": "confirmed"}
        supabase.table("orders").insert(data).execute()
        if MANAGER_PHONE: await send_whatsapp_message(MANAGER_PHONE, f"💰 VENTA NUEVA: {order_num} (${total})")
        return f"🎉 Pedido {order_num} Confirmado. ¡Gracias!"
    except: return "Error técnico."

async def ask_gpt4_client(user_message, user_phone, is_audio=False):
    # Lógica normal de cliente (igual que v7)
    current_menu = get_menu_text()
    tools = [{"type": "function", "function": {"name": "registrar_pedido", "parameters": {"type": "object", "properties": {"detalle": {"type": "string"}, "total": {"type": "number"}, "direccion": {"type": "string"}, "metodo_pago": {"type": "string"}}, "required": ["detalle", "total", "direccion", "metodo_pago"]}}}]
    
    messages = [{"role": "system", "content": f"Eres Komo. Vende amable. NPS 100. {current_menu}"}, {"role": "user", "content": user_message}]
    
    response = await openai_client.chat.completions.create(model="gpt-4-turbo", messages=messages, tools=tools)
    msg = response.choices[0].message
    reply = msg.content
    if msg.tool_calls:
        args = json.loads(msg.tool_calls[0].function.arguments)
        reply = await registrar_pedido_db(user_phone, args["detalle"], args["total"], args["direccion"], args.get("metodo_pago", "efectivo"))
    return reply

# --- SERVER ---
app = FastAPI()

@app.get("/webhook")
async def verify(request: Request):
    if request.query_params.get("hub.verify_token") == VERIFY_TOKEN: return int(request.query_params.get("hub.challenge"))
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
            sender = msg["from"]
            msg_type = msg["type"]
            
            # 1. VERIFICAR SI ES CHOFER
            is_driver = False
            try:
                # Checamos si el teléfono está en la tabla 'drivers'
                driver_check = supabase.table("drivers").select("id").eq("phone_number", sender).execute()
                if driver_check.data: is_driver = True
            except: pass

            # 2. SI ES CHOFER, CUALQUIER INPUT CIERRA LA ORDEN
            if is_driver:
                reply = await handle_driver_action(sender, msg_type)
                await send_whatsapp_message(sender, reply)
            
            # 3. SI ES CLIENTE, FLUJO NORMAL IA
            else:
                text_content = ""
                if msg_type == "text": text_content = msg["text"]["body"]
                elif msg_type == "audio":
                    bytes_ = await download_whatsapp_media(msg["audio"]["id"])
                    text_content = await transcribe_audio(bytes_)
                elif msg_type == "location":
                     text_content = f"Ubicación: {msg['location']['latitude']}, {msg['location']['longitude']}"

                if text_content:
                    reply = await ask_gpt4_client(text_content, sender)
                    await send_whatsapp_message(sender, reply)

        return {"status": "ok"}
    except Exception as e:
        print(f"Error: {e}")
        return {"status": "error"}