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

# --- 🔐 CONFIGURACIÓN Y VARIABLES DE ENTORNO ---
# Asegúrate de que estas variables estén en tu panel de Render o .env
WHATSAPP_TOKEN = os.environ.get("WHATSAPP_TOKEN")
PHONE_NUMBER_ID = os.environ.get("PHONE_NUMBER_ID")
VERIFY_TOKEN = os.environ.get("VERIFY_TOKEN", "KOMO_TOKEN_2025")
OPENAI_API_KEY = os.environ.get("OPENAI_API_KEY")

SUPABASE_URL = os.environ.get("SUPABASE_URL")
SUPABASE_KEY = os.environ.get("SUPABASE_KEY")

# --- CLIENTES GLOBALES ---
supabase: Client = None
openai_client: AsyncOpenAI = None

# --- 🛠️ FUNCIONES AUXILIARES: WHATSAPP ---

async def send_whatsapp_message(to_number: str, text_body: str):
    """Envía mensajes de texto de regreso al usuario"""
    if not WHATSAPP_TOKEN: return
    url = f"https://graph.facebook.com/v17.0/{PHONE_NUMBER_ID}/messages"
    headers = {"Authorization": f"Bearer {WHATSAPP_TOKEN}", "Content-Type": "application/json"}
    data = {"messaging_product": "whatsapp", "to": to_number, "type": "text", "text": {"body": text_body}}
    
    async with httpx.AsyncClient() as client:
        await client.post(url, headers=headers, json=data)

async def download_whatsapp_media(media_id: str):
    """Descarga la imagen de los servidores de Meta"""
    try:
        async with httpx.AsyncClient() as client:
            # 1. Obtener la URL de descarga
            url_info = f"https://graph.facebook.com/v17.0/{media_id}"
            headers = {"Authorization": f"Bearer {WHATSAPP_TOKEN}"}
            resp_info = await client.get(url_info, headers=headers)
            media_url = resp_info.json().get("url")
            
            if not media_url: return None

            # 2. Descargar el binario (la imagen real)
            resp_media = await client.get(media_url, headers=headers)
            return resp_media.content # Retorna bytes
    except Exception as e:
        print(f"Error descargando media: {e}")
        return None

# --- 🛠️ FUNCIONES AUXILIARES: SUPABASE ---

async def upload_evidence_to_supabase(image_bytes, phone):
    """Sube la imagen al bucket 'evidencias' y retorna la URL pública"""
    filename = f"{phone}_{int(time.time())}.jpg"
    try:
        # Subir al bucket 'evidencias'
        supabase.storage.from_("evidencias").upload(
            path=filename,
            file=image_bytes,
            file_options={"content-type": "image/jpeg"}
        )
        # Obtener URL pública
        public_url = supabase.storage.from_("evidencias").get_public_url(filename)
        return public_url
    except Exception as e:
        print(f"Error subiendo a Supabase: {e}")
        return None

async def create_complaint_ticket(phone, image_url, caption):
    """Crea un ticket en la tabla complaints"""
    try:
        data = {
            "customer_phone": phone,
            "issue_description": caption or "Imagen enviada por cliente (Posible evidencia)",
            "image_url": image_url,
            "status": "open"
        }
        supabase.table("complaints").insert(data).execute()
        return True
    except Exception as e:
        print(f"Error creando ticket: {e}")
        return False

# --- 🧠 LÓGICA DE NEGOCIO (GPT Y PEDIDOS) ---

async def registrar_pedido_db(phone: str, detalle: str, total: float, direccion: str, metodo_pago: str, lat: float = None, long: float = None):
    """Guarda la orden en la tabla 'orders'"""
    order_num = f"ORD-{int(time.time())}"
    try:
        data = {
            "order_number": order_num,
            "customer_phone": phone,
            "order_details": detalle,
            "total_price": total,
            "delivery_address": direccion,
            "payment_status": "pending", # Asumimos pendiente hasta confirmar
            "status": "confirmed",       # Entra directo como confirmado para cocina
            "delivery_latitude": lat,
            "delivery_longitude": long,
            "customer_name": "Cliente WhatsApp" # Podríamos pedir el nombre luego
        }
        supabase.table("orders").insert(data).execute()
        
        # Notificar éxito
        msg = f"✅ ¡Listo! Tu pedido {order_num} está confirmado.\nTotal: ${total}\nEnviaremos a: {direccion}"
        return msg
    except Exception as e:
        print(f"Error DB: {e}")
        return "Tuve un error registrando el pedido. Por favor intenta de nuevo."

async def ask_gpt4(user_message: str, user_phone: str, is_location_pin=False):
    """El cerebro que decide qué hacer"""
    
    # Historial de chat
    history_resp = supabase.table("chat_history").select("role, content").eq("phone_number", user_phone).order("created_at", desc=True).limit(6).execute()
    history = history_resp.data[::-1] if history_resp.data else []
    
    # Definición de Herramientas (Function Calling)
    tools = [{
        "type": "function",
        "function": {
            "name": "registrar_pedido",
            "description": "Registra un pedido cuando el usuario confirma productos, dirección y total.",
            "parameters": {
                "type": "object",
                "properties": {
                    "detalle": {"type": "string", "description": "Lista de productos"},
                    "total": {"type": "number", "description": "Costo total estimado"},
                    "direccion": {"type": "string", "description": "Dirección de entrega"},
                    "metodo_pago": {"type": "string", "enum": ["efectivo", "tarjeta"]},
                    "lat": {"type": "number"},
                    "long": {"type": "number"}
                },
                "required": ["detalle", "total", "direccion", "metodo_pago"]
            }
        }
    }]

    # Prompt del Sistema
    system_prompt = """
    Eres Komo, un asistente de delivery eficiente y amable.
    Tu menú básico: Pizzas $150, Tacos $80, Refrescos $20.
    
    1. Si recibes coordenadas (Lat/Long), asume que es la dirección de entrega.
    2. Antes de pedir, confirma el total.
    3. Si el usuario envía una foto, diles que un humano revisará su caso.
    """
    
    messages = [{"role": "system", "content": system_prompt}] + history
    
    # Si es un pin de ubicación, lo inyectamos como mensaje de sistema para que GPT lo sepa usar
    if is_location_pin:
        messages.append({"role": "system", "content": f"SISTEMA: El usuario envió su ubicación GPS actual: {user_message}"})
    else:
        messages.append({"role": "user", "content": user_message})

    # Llamada a OpenAI
    try:
        response = await openai_client.chat.completions.create(
            model="gpt-4-turbo", messages=messages, tools=tools, tool_choice="auto"
        )
        msg = response.choices[0].message
        
        # Si GPT quiere ejecutar la función (Registrar Pedido)
        if msg.tool_calls:
            tool_call = msg.tool_calls[0]
            args = json.loads(tool_call.function.arguments)
            
            # Ejecutamos la función
            reply_text = await registrar_pedido_db(
                user_phone, args["detalle"], args["total"], args["direccion"], 
                args.get("metodo_pago", "efectivo"), args.get("lat"), args.get("long")
            )
            
            # Guardamos la respuesta final
            await supabase.table("chat_history").insert({"phone_number": user_phone, "role": "assistant", "content": reply_text}).execute()
            return reply_text
        
        # Si es solo charla normal
        bot_reply = msg.content
        await supabase.table("chat_history").insert({"phone_number": user_phone, "role": "user", "content": user_message}).execute()
        await supabase.table("chat_history").insert({"phone_number": user_phone, "role": "assistant", "content": bot_reply}).execute()
        return bot_reply

    except Exception as e:
        print(f"Error GPT: {e}")
        return "Un momento, estoy procesando..."

# --- 🚀 CONFIGURACIÓN DEL SERVIDOR (LIFESPAN) ---

@asynccontextmanager
async def lifespan(app: FastAPI):
    global supabase, openai_client
    supabase = create_client(SUPABASE_URL, SUPABASE_KEY)
    openai_client = AsyncOpenAI(api_key=OPENAI_API_KEY)
    print("🚀 KOMO BACKEND V4.0 ONLINE")
    yield

app = FastAPI(title="Komo Backend", lifespan=lifespan)

# --- RUTAS ---

@app.get("/")
def home(): return {"status": "Online", "version": "4.0 Anti-Rappi"}

@app.get("/webhook")
async def verify_webhook(request: Request):
    """Verificación de Meta para conectar el Webhook"""
    if request.query_params.get("hub.verify_token") == VERIFY_TOKEN:
        return int(request.query_params.get("hub.challenge"))
    raise HTTPException(status_code=403, detail="Token inválido")

@app.post("/webhook")
async def webhook_handler(request: Request):
    """Recibe TODOS los mensajes de WhatsApp"""
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
            
            # CASO 1: TEXTO NORMAL
            if msg_type == "text":
                text = msg["text"]["body"]
                reply = await ask_gpt4(text, sender)
                await send_whatsapp_message(sender, reply)
            
            # CASO 2: UBICACIÓN (GPS)
            elif msg_type == "location":
                loc = msg["location"]
                coords_text = f"Lat: {loc['latitude']}, Long: {loc['longitude']}"
                # Pasamos flag True para indicar que es un Pin
                reply = await ask_gpt4(coords_text, sender, is_location_pin=True)
                await send_whatsapp_message(sender, reply)
            
            # CASO 3: IMAGEN (NUEVO - EVIDENCIA/QUEJA)
            elif msg_type == "image":
                image_id = msg["image"]["id"]
                caption = msg["image"].get("caption", "")
                
                # Descargar y subir
                image_bytes = await download_whatsapp_media(image_id)
                if image_bytes:
                    public_url = await upload_evidence_to_supabase(image_bytes, sender)
                    if public_url:
                        # Crear Ticket
                        await create_complaint_ticket(sender, public_url, caption)
                        await send_whatsapp_message(sender, "📷 Imagen recibida. Hemos abierto un ticket de soporte y el gerente revisará la evidencia.")
                    else:
                        await send_whatsapp_message(sender, "Error procesando la imagen.")
            
        return {"status": "ok"}
        
    except Exception as e:
        print(f"Error procesando webhook: {e}")
        return {"status": "error"}