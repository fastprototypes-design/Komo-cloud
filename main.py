import os
import httpx
import json
import time
from contextlib import asynccontextmanager
from fastapi import FastAPI, Request, HTTPException
from supabase import create_client, Client
from openai import AsyncOpenAI

# --- 🔐 VARIABLES ---
WHATSAPP_TOKEN = os.environ.get("WHATSAPP_TOKEN")
PHONE_NUMBER_ID = os.environ.get("PHONE_NUMBER_ID")
VERIFY_TOKEN = os.environ.get("VERIFY_TOKEN", "KOMO_TOKEN_2025")
OPENAI_API_KEY = os.environ.get("OPENAI_API_KEY")

SUPABASE_URL = os.environ.get("SUPABASE_URL")
SUPABASE_KEY = os.environ.get("SUPABASE_KEY")

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
    """Descarga audio o imagen de Meta"""
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
        print(f"Error descarga media: {e}")
        return None

# --- 👂 EL OÍDO DIGITAL (WHISPER) ---
async def transcribe_audio(audio_bytes):
    """Convierte nota de voz de WhatsApp a Texto usando Whisper"""
    try:
        # Whisper necesita un nombre de archivo, aunque sea en memoria
        transcription = await openai_client.audio.transcriptions.create(
            model="whisper-1",
            file=("audio.ogg", audio_bytes) 
        )
        return transcription.text
    except Exception as e:
        print(f"Error Whisper: {e}")
        return ""

# --- 🧠 LÓGICA DE NEGOCIO ---

async def registrar_pedido_db(phone, detalle, total, direccion, metodo_pago):
    # (Misma lógica anterior para clientes)
    order_num = f"ORD-{int(time.time())}"
    try:
        data = {
            "order_number": order_num, "customer_phone": phone, "order_details": detalle,
            "total_price": total, "delivery_address": direccion, "payment_status": "pending",
            "status": "confirmed", "customer_name": "Cliente WhatsApp"
        }
        supabase.table("orders").insert(data).execute()
        return f"✅ Pedido {order_num} confirmado."
    except: return "Error registrando pedido."

async def ask_gpt4(user_message: str, user_phone: str, is_audio=False):
    # Si vino por audio, agregamos una etiqueta para que GPT sepa
    context_note = "[TRANSCRIPCIÓN DE AUDIO]: " if is_audio else ""
    full_msg = f"{context_note}{user_message}"

    # Guardar historial
    try:
        supabase.table("chat_history").insert({"phone_number": user_phone, "role": "user", "content": full_msg}).execute()
        history_resp = supabase.table("chat_history").select("role, content").eq("phone_number", user_phone).order("created_at", desc=True).limit(6).execute()
        history = history_resp.data[::-1] if history_resp.data else []
    except: history = []

    # Herramientas para Clientes
    tools = [{
        "type": "function",
        "function": {
            "name": "registrar_pedido",
            "description": "Registra pedido nuevo",
            "parameters": {
                "type": "object",
                "properties": {
                    "detalle": {"type": "string"}, "total": {"type": "number"},
                    "direccion": {"type": "string"}, "metodo_pago": {"type": "string"}
                },
                "required": ["detalle", "total", "direccion", "metodo_pago"]
            }
        }
    }]

    # PROMPT HÍBRIDO (Sirve para Clientes y Repartidores)
    system_prompt = """Eres Komo IA. 
    1. CLIENTES: Vende pizzas ($150) y tacos ($80). Sé amable.
    2. REPARTIDORES: Si detectas frases como "Ya entregué", "Voy en camino", "Cierra la orden":
       - Confírmales con un mensaje corto y militar: "Copiado. 🫡".
       - (Nota: En una versión futura aquí ejecutaremos el cambio de estado automático).
    """

    messages = [{"role": "system", "content": system_prompt}] + history

    try:
        response = await openai_client.chat.completions.create(
            model="gpt-4-turbo", messages=messages, tools=tools, tool_choice="auto"
        )
        msg = response.choices[0].message
        reply_text = msg.content

        if msg.tool_calls:
            tool_call = msg.tool_calls[0]
            args = json.loads(tool_call.function.arguments)
            reply_text = await registrar_pedido_db(user_phone, args["detalle"], args["total"], args["direccion"], args.get("metodo_pago", "efectivo"))

        # Guardar respuesta
        if reply_text:
            supabase.table("chat_history").insert({"phone_number": user_phone, "role": "assistant", "content": reply_text}).execute()
        
        return reply_text
    except Exception as e:
        print(f"Error GPT: {e}")
        return "Un momento..."

# --- 🚀 SERVIDOR ---
@asynccontextmanager
async def lifespan(app: FastAPI):
    print("🚀 KOMO VOZ ACTIVO")
    yield

app = FastAPI(lifespan=lifespan)

@app.get("/")
def home(): return {"status": "Komo Voice Ready 🎤"}

@app.get("/webhook")
async def verify(request: Request):
    if request.query_params.get("hub.verify_token") == VERIFY_TOKEN:
        return int(request.query_params.get("hub.challenge"))
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
            
            # --- MANEJO DE TEXTO ---
            if msg_type == "text":
                reply = await ask_gpt4(msg["text"]["body"], sender)
                await send_whatsapp_message(sender, reply)
            
            # --- MANEJO DE AUDIO (VOZ) ---
            elif msg_type == "audio":
                audio_id = msg["audio"]["id"]
                # 1. Descargamos audio
                audio_bytes = await download_whatsapp_media(audio_id)
                if audio_bytes:
                    # 2. Transcribimos con Whisper
                    text = await transcribe_audio(audio_bytes)
                    # 3. Enviamos el texto a GPT como si el usuario lo hubiera escrito
                    reply = await ask_gpt4(text, sender, is_audio=True)
                    await send_whatsapp_message(sender, reply)
                else:
                    await send_whatsapp_message(sender, "No pude escuchar el audio. 🎧")

            # --- MANEJO DE UBICACIÓN ---
            elif msg_type == "location":
                loc = msg["location"]
                coords = f"Lat: {loc['latitude']}, Long: {loc['longitude']}"
                reply = await ask_gpt4(coords, sender)
                await send_whatsapp_message(sender, reply)

        return {"status": "ok"}
    except Exception as e:
        print(f"Error: {e}")
        return {"status": "error"}