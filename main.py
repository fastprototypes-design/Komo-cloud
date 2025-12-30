import os
import httpx
import json
import time
import uuid
from contextlib import asynccontextmanager
from fastapi import FastAPI, Request, HTTPException
from supabase import create_client, Client
from openai import AsyncOpenAI
from datetime import datetime

# --- 🔐 VARIABLES DE ENTORNO ---
WHATSAPP_TOKEN = os.environ.get("WHATSAPP_TOKEN")
PHONE_NUMBER_ID = os.environ.get("PHONE_NUMBER_ID")
VERIFY_TOKEN = os.environ.get("VERIFY_TOKEN", "KOMO_TOKEN_2025")
OPENAI_API_KEY = os.environ.get("OPENAI_API_KEY")

SUPABASE_URL = os.environ.get("SUPABASE_URL")
SUPABASE_KEY = os.environ.get("SUPABASE_KEY")

# --- CLIENTES GLOBALES ---
# Inicializamos Supabase fuera para evitar problemas de contexto
supabase: Client = create_client(SUPABASE_URL, SUPABASE_KEY)
openai_client: AsyncOpenAI = AsyncOpenAI(api_key=OPENAI_API_KEY)

# --- 🛠️ FUNCIONES AUXILIARES: WHATSAPP ---

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
        print(f"Error media: {e}")
        return None

# --- 🛠️ FUNCIONES AUXILIARES: SUPABASE ---

async def upload_evidence_to_supabase(image_bytes, phone):
    filename = f"{phone}_{int(time.time())}.jpg"
    try:
        # Nota: Storage de supabase suele ser sync en python wrapper, pero httpx por debajo puede variar.
        # Lo usaremos directo sin await para estar seguros.
        supabase.storage.from_("evidencias").upload(
            path=filename,
            file=image_bytes,
            file_options={"content-type": "image/jpeg"}
        )
        return supabase.storage.from_("evidencias").get_public_url(filename)
    except Exception as e:
        print(f"Error subiendo Storage: {e}")
        return None

async def create_complaint_ticket(phone, image_url, caption):
    try:
        data = {
            "customer_phone": phone,
            "issue_description": caption or "Imagen enviada por cliente",
            "image_url": image_url,
            "status": "open"
        }
        # CORRECCIÓN: Quitamos el 'await' aquí
        supabase.table("complaints").insert(data).execute()
        return True
    except Exception as e:
        print(f"Error ticket: {e}")
        return False

# --- 🧠 LÓGICA DE NEGOCIO ---

async def registrar_pedido_db(phone: str, detalle: str, total: float, direccion: str, metodo_pago: str, lat: float = None, long: float = None):
    order_num = f"ORD-{int(time.time())}"
    try:
        data = {
            "order_number": order_num,
            "customer_phone": phone,
            "order_details": detalle,
            "total_price": total,
            "delivery_address": direccion,
            "payment_status": "pending",
            "status": "confirmed",
            "delivery_latitude": lat,
            "delivery_longitude": long,
            "customer_name": "Cliente WhatsApp"
        }
        # CORRECCIÓN: Quitamos el 'await' aquí
        supabase.table("orders").insert(data).execute()
        
        return f"✅ Pedido {order_num} confirmado.\nTotal: ${total}\nDirección: {direccion}"
    except Exception as e:
        print(f"Error DB Pedido: {e}")
        return "Error registrando pedido."

async def ask_gpt4(user_message: str, user_phone: str, is_location_pin=False):
    # 1. Guardar mensaje del usuario (SIN AWAIT)
    try:
        supabase.table("chat_history").insert({"phone_number": user_phone, "role": "user", "content": user_message}).execute()
    except Exception as e:
        print(f"Error guardando user msg: {e}")

    # 2. Recuperar historial (SIN AWAIT)
    try:
        history_resp = supabase.table("chat_history").select("role, content").eq("phone_number", user_phone).order("created_at", desc=True).limit(6).execute()
        history = history_resp.data[::-1] if history_resp.data else []
    except:
        history = []
    
    tools = [{
        "type": "function",
        "function": {
            "name": "registrar_pedido",
            "description": "Registra pedido completo",
            "parameters": {
                "type": "object",
                "properties": {
                    "detalle": {"type": "string"}, "total": {"type": "number"},
                    "direccion": {"type": "string"}, "metodo_pago": {"type": "string"},
                    "lat": {"type": "number"}, "long": {"type": "number"}
                },
                "required": ["detalle", "total", "direccion", "metodo_pago"]
            }
        }
    }]

    system_prompt = """Eres Komo, delivery de Pizzas ($150) y Tacos ($80). 
    Sé breve. Si te mandan ubicación GPS, úsala como dirección."""
    
    messages = [{"role": "system", "content": system_prompt}] + history
    if is_location_pin:
        messages.append({"role": "system", "content": f"GPS RECIBIDO: {user_message}"})

    try:
        # GPT sí requiere await
        response = await openai_client.chat.completions.create(
            model="gpt-4-turbo", messages=messages, tools=tools, tool_choice="auto"
        )
        msg = response.choices[0].message
        
        reply_text = msg.content

        if msg.tool_calls:
            tool_call = msg.tool_calls[0]
            args = json.loads(tool_call.function.arguments)
            reply_text = await registrar_pedido_db(
                user_phone, args["detalle"], args["total"], args["direccion"], 
                args.get("metodo_pago", "efectivo"), args.get("lat"), args.get("long")
            )

        # Guardar respuesta del bot (SIN AWAIT)
        if reply_text:
            supabase.table("chat_history").insert({"phone_number": user_phone, "role": "assistant", "content": reply_text}).execute()
        
        return reply_text

    except Exception as e:
        print(f"Error GPT Real: {e}")
        return "Un momento..."

# --- 🚀 SERVIDOR ---
app = FastAPI()

@app.get("/")
def home(): return {"status": "Online v5.1"}

@app.get("/webhook")
async def verify_webhook(request: Request):
    if request.query_params.get("hub.verify_token") == VERIFY_TOKEN:
        return int(request.query_params.get("hub.challenge"))
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
            msg_type = msg["type"]
            
            if msg_type == "text":
                reply = await ask_gpt4(msg["text"]["body"], sender)
                await send_whatsapp_message(sender, reply)
            
            elif msg_type == "location":
                loc = msg["location"]
                coords = f"{loc['latitude']}, {loc['longitude']}"
                reply = await ask_gpt4(coords, sender, is_location_pin=True)
                await send_whatsapp_message(sender, reply)

            elif msg_type == "image":
                # Lógica de imagen simplificada
                img_id = msg["image"]["id"]
                img_bytes = await download_whatsapp_media(img_id)
                if img_bytes:
                    url = await upload_evidence_to_supabase(img_bytes, sender)
                    await create_complaint_ticket(sender, url, "Imagen WhatsApp")
                    await send_whatsapp_message(sender, "📷 Imagen recibida.")

        return {"status": "ok"}
    except Exception as e:
        print(f"Error Webhook: {e}")
        return {"status": "error"}