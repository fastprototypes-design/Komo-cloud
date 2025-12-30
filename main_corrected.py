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

# Validate required environment variables
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
    except Exception as e:
        print(f"Error fetching menu: {e}")
        return "Consultar disponibilidad."

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
            
        return f"🎉 ¡Pedido {order_num} confirmado!\n\n🍕 {detalle}\n💰 Total: ${total}\n📍 Dirección: {direccion}\n\n¡En Komo Fast Food ya estamos preparando tu orden! 🔥"
    except Exception as e:
        print(f"Error registering order: {e}")
        return "Perdón, tuve un error al guardar tu pedido. ¿Podemos intentar de nuevo?"

# --- 🏍️ LÓGICA DE REPARTIDOR ---

async def handle_driver_action(driver_phone):
    try:
        resp = supabase.table("orders").select("*").eq("driver_phone", driver_phone).eq("status", "delivering").execute()
        if resp.data:
            order = resp.data[0]
            supabase.table("orders").update({
                "status": "completed", "completed_at": datetime.now().isoformat()
            }).eq("id", order['id']).execute()
            return f"✅ Orden {order['order_number']} cerrada. ¡Excelente servicio! 🫡"
        return "No tienes órdenes pendientes de entrega. 🤔"
    except Exception as e:
        print(f"Error handling driver action: {e}")
        return "Error al procesar el cierre."

# --- 🤖 EL CEREBRO (GPT-4 CON MEMORIA DE 5 PASOS) ---

async def ask_gpt4_client(user_message: str, user_phone: str, is_audio=False):
    current_menu = get_menu_text()
    
    # 1. Guardar y Obtener Memoria Limitada (Últimas 5-8 interacciones)
    try:
        note = "[AUDIO] " if is_audio else ""
        supabase.table("chat_history").insert({"phone_number": user_phone, "role": "user", "content": f"{note}{user_message}"}).execute()
        
        # Leemos los últimos 8 registros para un contexto fluido pero ligero
        hist_resp = supabase.table("chat_history").select("role, content").eq("phone_number", user_phone).order("created_at", desc=True).limit(8).execute()
        history = [{"role": h["role"], "content": h["content"]} for h in hist_resp.data][::-1]
    except Exception as e:
        print(f"Error retrieving chat history: {e}")
        history = [{"role": "user", "content": user_message}]

    # 2. Herramientas
    tools = [{
        "type": "function",
        "function": {
            "name": "registrar_pedido",
            "description": "Solo cuando el cliente confirme items, precio y dirección de entrega.",
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

    # 3. Prompt Personalizado NPS 100
    system_prompt = f"""
    Eres Komo, el asistente virtual de Komo Fast Food. Tu meta es un NPS de 100.
    - Saludo obligatorio: "Gracias por comunicarte a Komo Fast Food".
    - Sé extremadamente amable, empático y proactivo.
    - REVISA EL HISTORIAL RECIENTE para no pedir datos que el cliente ya dio.
    - Si manda un link de ubicación o coordenadas, úsalo como la dirección de entrega.
    - Si el cliente añade productos (ej: "también una coca"), súmalo a lo anterior.
    
    {current_menu}
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
    except Exception as e:
        print(f"Error calling GPT-4: {e}")
        return "Gracias por comunicarte a Komo Fast Food. Tuve un pequeño error, ¿puedes repetirme lo último? 🍕"

# --- 🚀 FASTAPI ---
@asynccontextmanager
async def lifespan(app: FastAPI):
    print("🚀 KOMO MASTER BUILD v8.6 LIVE - KOMO FAST FOOD")
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
        entry = body.get("entry", [])
        if not entry:
            return {"status": "ok"}
        
        messages = entry[0].get("changes", [])[0].get("value", {}).get("messages", [])
        
        if messages:
            msg = messages[0]
            sender = msg.get("from")
            msg_type = msg.get("type")
            
            if not sender or not msg_type:
                return {"status": "ok"}

            # ¿Es Repartidor?
            dr_check = supabase.table("drivers").select("id").eq("phone_number", sender).execute()
            is_driver = len(dr_check.data) > 0

            if is_driver:
                reply = await handle_driver_action(sender)
                await send_whatsapp_message(sender, reply)
            else:
                # Flujo Cliente
                content = ""
                if msg_type == "text":
                    content = msg.get("text", {}).get("body", "")
                elif msg_type == "audio":
                    audio_id = msg.get("audio", {}).get("id")
                    if audio_id:
                        audio = await download_whatsapp_media(audio_id)
                        if audio:
                            content = await transcribe_audio(audio)
                elif msg_type == "location":
                    loc = msg.get("location", {})
                    lat = loc.get("latitude")
                    lon = loc.get("longitude")
                    if lat is not None and lon is not None:
                        content = f"Mi ubicación: https://www.google.com/maps?q={lat},{lon}"

                if content:
                    reply = await ask_gpt4_client(content, sender, is_audio=(msg_type=="audio"))
                    await send_whatsapp_message(sender, reply)

        return {"status": "ok"}
    except Exception as e:
        print(f"Error processing webhook: {e}")
        return {"status": "error"}
