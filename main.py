import os
import httpx
import json
import time
from contextlib import asynccontextmanager
from fastapi import FastAPI, Request, HTTPException
from supabase import create_client, Client
from openai import AsyncOpenAI
from config import settings, logger

# --- 🔐 VARIABLES ---
WHATSAPP_TOKEN = os.environ.get("WHATSAPP_TOKEN")
PHONE_NUMBER_ID = os.environ.get("PHONE_NUMBER_ID")
VERIFY_TOKEN = os.environ.get("VERIFY_TOKEN", "KOMO_TOKEN_2025")
OPENAI_API_KEY = os.environ.get("OPENAI_API_KEY")

# --- 🏢 CREDENCIALES ---
BUSINESS_ID = os.environ.get("BUSINESS_ID")
BRANCH_ID = os.environ.get("BRANCH_ID")
MANAGER_PHONE = "5218138734122" # Gerente

supabase: Client = None
openai_client: AsyncOpenAI = None
CURRENT_MENU_TEXT = "Cargando menú..."

# --- 📡 WHATSAPP ---
async def send_whatsapp_message(to_number: str, text_body: str):
    if not WHATSAPP_TOKEN: return
    url = f"https://graph.facebook.com/v17.0/{PHONE_NUMBER_ID}/messages"
    headers = {"Authorization": f"Bearer {WHATSAPP_TOKEN}", "Content-Type": "application/json"}
    data = {"messaging_product": "whatsapp", "to": to_number, "type": "text", "text": {"body": text_body}}
    async with httpx.AsyncClient() as client:
        await client.post(url, headers=headers, json=data)

# --- 🛠️ 1. MEMORIA ---
async def save_message(phone: str, role: str, content: str):
    try:
        supabase.table("chat_history").insert({"phone_number": phone, "role": role, "content": content}).execute()
    except Exception as e: logger.error(f"Error historial: {e}")

async def get_chat_history(phone: str, limit: int = 6):
    try:
        response = supabase.table("chat_history").select("role, content").eq("phone_number", phone).order("created_at", desc=True).limit(limit).execute()
        return response.data[::-1]
    except Exception: return []

# --- 🛠️ 2. MENÚ ---
async def get_menu_from_db():
    try:
        response = supabase.table("products").select("*").eq("is_active", True).execute()
        products = response.data
        if not products: return "Sin productos."
        menu_text = "MENÚ:\n"
        for p in products: menu_text += f"- {p['name']}: ${p['price']}\n"
        return menu_text
    except Exception: return "Error menú."

# --- 🛠️ 3. REGISTRO (AHORA CON GPS) ---
async def registrar_pedido_db(phone: str, detalle: str, total: float, direccion: str, metodo_pago: str, lat: float = None, long: float = None):
    order_num = f"ORD-{int(time.time())}"
    try:
        # Datos a insertar
        data = {
            "business_id": BUSINESS_ID, "branch_id": BRANCH_ID,
            "customer_phone": phone, "order_details": detalle,
            "total_price": total, "status": "confirmed",
            "order_number": order_num, "order_type": "delivery",
            "delivery_address": direccion, 
            "payment_method": metodo_pago,
            "delivery_latitude": lat,   # 📍 Coordenada GPS
            "delivery_longitude": long  # 📍 Coordenada GPS
        }
        
        supabase.table("orders").insert(data).execute()
        
        # 🔔 Notificación Gerente (Con link a Google Maps si hay GPS)
        maps_link = ""
        if lat and long:
            maps_link = f"\n🗺️ Ver en Mapa: https://www.google.com/maps/search/?api=1&query={lat},{long}"

        mensaje_gerente = f"""🔔 *NUEVO PEDIDO GPS* 🛰️
🆔 {order_num}
👤 {phone}
🍕 {detalle}
💰 ${total} ({metodo_pago})
📍 {direccion}{maps_link}"""

        await send_whatsapp_message(MANAGER_PHONE, mensaje_gerente)
        return f"Pedido {order_num} registrado. Enviaremos a: {direccion}."
    except Exception as e:
        logger.error(f"Error DB: {e}")
        return "Error interno."

# --- 🧠 CEREBRO ---
async def ask_gpt4(user_message: str, user_phone: str):
    global CURRENT_MENU_TEXT
    
    # Guardamos mensaje entrante (Si es ubicación, ya viene convertido a texto)
    await save_message(user_phone, "user", user_message)
    history = await get_chat_history(user_phone)
    
    tools = [{
        "type": "function",
        "function": {
            "name": "registrar_pedido",
            "description": "Usa esto con PRODUCTO, DIRECCIÓN y PAGO. Si envió ubicación GPS, úsala.",
            "parameters": {
                "type": "object",
                "properties": {
                    "detalle": {"type": "string"}, "total": {"type": "number"},
                    "direccion": {"type": "string", "description": "Dirección escrita o 'Ubicación GPS'"},
                    "metodo_pago": {"type": "string"},
                    "lat": {"type": "number", "description": "Latitud si está disponible"},
                    "long": {"type": "number", "description": "Longitud si está disponible"}
                },
                "required": ["detalle", "total", "direccion", "metodo_pago"]
            }
        }
    }]

    system_prompt = f"""
    Eres Komo, vendedor de pizzas. MENÚ: {CURRENT_MENU_TEXT}
    REGLAS:
    1. Si el historial dice "UBICACIÓN_GPS_RECIBIDA", ¡ya tienes la dirección! No la pidas de nuevo.
       Usa las coordenadas (Lat/Long) que veas en el mensaje del sistema.
    2. Si tienes coordenadas, en el campo 'direccion' pon "Ubicación GPS compartida".
    3. Pide amablemente lo que falte.
    """
    
    messages = [{"role": "system", "content": system_prompt}] + history

    try:
        response = await openai_client.chat.completions.create(
            model="gpt-4-turbo", messages=messages, tools=tools, tool_choice="auto", temperature=0.7
        )
        msg = response.choices[0].message
        bot_reply = msg.content

        if msg.tool_calls:
            tool_call = msg.tool_calls[0]
            args = json.loads(tool_call.function.arguments)
            # Extraemos lat/long si GPT los encontró en el historial
            lat = args.get("lat")
            long = args.get("long")
            
            resultado = await registrar_pedido_db(user_phone, args["detalle"], args["total"], args["direccion"], args["metodo_pago"], lat, long)
            
            messages.append(msg)
            messages.append({"role": "tool", "tool_call_id": tool_call.id, "content": resultado})
            final = await openai_client.chat.completions.create(model="gpt-4-turbo", messages=messages)
            bot_reply = final.choices[0].message.content
        
        await save_message(user_phone, "assistant", bot_reply)
        return bot_reply

    except Exception as e:
        logger.error(f"Error GPT: {e}")
        return "Un momento..."

# --- 🚀 WEBHOOK HANDLER INTELIGENTE ---
@asynccontextmanager
async def lifespan(app: FastAPI):
    global supabase, openai_client, CURRENT_MENU_TEXT
    supabase = create_client(settings.supabase_url, settings.supabase_key)
    openai_client = AsyncOpenAI(api_key=OPENAI_API_KEY)
    CURRENT_MENU_TEXT = await get_menu_from_db()
    logger.info("🚀 KOMO GPS ACTIVO")
    yield

app = FastAPI(title="Komo GPS", lifespan=lifespan)

@app.get("/")
def home(): return {"status": "GPS Ready 🛰️"}

@app.get("/webhook")
async def verify(request: Request):
    if request.query_params.get("hub.verify_token") == VERIFY_TOKEN: return int(request.query_params.get("hub.challenge"))
    raise HTTPException(status_code=403)

@app.post("/webhook")
async def webhook_handler(request: Request):
    try:
        body = await request.json()
        entry = body.get("entry", [])[0]
        changes = entry.get("changes", [])[0]
        value = changes.get("value", {})
        messages = value.get("messages", [])
        
        if messages:
            msg = messages[0]
            sender = msg["from"]
            
            # --- DETECCIÓN DE TIPO DE MENSAJE ---
            if msg["type"] == "text":
                user_text = msg["text"]["body"]
                await send_whatsapp_message(sender, await ask_gpt4(user_text, sender))
                
            elif msg["type"] == "location":
                # 📍 EL USUARIO ENVIÓ SU UBICACIÓN
                loc = msg["location"]
                lat = loc["latitude"]
                lng = loc["longitude"]
                
                # Truco: Convertimos la ubicación en un mensaje de texto "simulado" para que GPT lo entienda
                # Agregamos instrucciones ocultas para GPT
                system_injection = f"UBICACIÓN_GPS_RECIBIDA: Lat {lat}, Long {lng}. (El usuario compartió su ubicación actual)."
                
                # Procesamos esto como si el usuario lo hubiera escrito
                await send_whatsapp_message(sender, await ask_gpt4(system_injection, sender))
                
        return {"status": "ok"}
    except Exception as e:
        logger.error(f"Error Webhook: {e}")
        return {"status": "error"}