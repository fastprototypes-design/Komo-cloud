import os
import httpx
import json
import time
from contextlib import asynccontextmanager
from fastapi import FastAPI, Request, HTTPException
from supabase import create_client, Client
from openai import AsyncOpenAI

# --- 🔐 CONFIGURACIÓN ---
WHATSAPP_TOKEN = os.environ.get("WHATSAPP_TOKEN")
PHONE_NUMBER_ID = os.environ.get("PHONE_NUMBER_ID")
VERIFY_TOKEN = os.environ.get("VERIFY_TOKEN", "KOMO_TOKEN_2025")
OPENAI_API_KEY = os.environ.get("OPENAI_API_KEY")
SUPABASE_URL = os.environ.get("SUPABASE_URL")
SUPABASE_KEY = os.environ.get("SUPABASE_KEY")

supabase: Client = create_client(SUPABASE_URL, SUPABASE_KEY)
openai_client: AsyncOpenAI = AsyncOpenAI(api_key=OPENAI_API_KEY)

# --- 🛠️ UTILIDADES ---

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

# --- 🧠 CEREBRO DINÁMICO (Conexión al CMS) ---

def get_menu_text():
    """Lee la tabla 'products' y crea el menú para el Prompt"""
    try:
        response = supabase.table("products").select("name, price, description").eq("is_active", True).execute()
        items = response.data
        if not items:
            return "Menú no disponible por el momento."
        
        menu_str = "MENÚ DISPONIBLE HOY:\n"
        for item in items:
            menu_str += f"- {item['name']}: ${item['price']} ({item.get('description', '')})\n"
        return menu_str
    except:
        return "Error leyendo menú."

async def registrar_pedido_db(phone, detalle, total, direccion, metodo_pago):
    order_num = f"ORD-{int(time.time())}"
    try:
        data = {
            "order_number": order_num, "customer_phone": phone, "order_details": detalle,
            "total_price": total, "delivery_address": direccion, "payment_status": "pending",
            "status": "confirmed", "customer_name": "Cliente WhatsApp"
        }
        supabase.table("orders").insert(data).execute()
        # Respuesta NPS 100: Confirmación entusiasta
        return f"🎉 ¡Excelente elección! Tu pedido {order_num} está confirmado.\n\n🍕 {detalle}\n💰 Total: ${total}\n📍 Destino: {direccion}\n\n¡Corremos a la cocina! 🔥"
    except: return "Tuve un pequeño error técnico, pero ya avisé al gerente. Dame un minuto."

async def ask_gpt4(user_message: str, user_phone: str, is_audio=False):
    # 1. Recuperar Menú Actualizado del CMS
    current_menu = get_menu_text()

    # 2. Historial de Chat
    try:
        note = "[AUDIO TRANSCRITO] " if is_audio else ""
        supabase.table("chat_history").insert({"phone_number": user_phone, "role": "user", "content": f"{note}{user_message}"}).execute()
        history_resp = supabase.table("chat_history").select("role, content").eq("phone_number", user_phone).order("created_at", desc=True).limit(8).execute()
        history = history_resp.data[::-1] if history_resp.data else []
    except: history = []

    # 3. Herramientas
    tools = [{
        "type": "function",
        "function": {
            "name": "registrar_pedido",
            "description": "Usar SOLO cuando el cliente confirme productos, total y dirección.",
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

    # 4. EL ALMA DEL PROYECTO (PROMPT NPS 100)
    system_prompt = f"""
    Eres Komo, el asistente de delivery más amable y eficiente del mundo.
    Tu misión es lograr una satisfacción total del cliente (NPS 100).
    
    TUS REGLAS DE ORO:
    1. **Empatía Radical:** Si el cliente está feliz, celebra con él. Si está molesto, discúlpate sinceramente y ofrece ayuda inmediata.
    2. **Brevedad WhatsApp:** Escribe corto, usa espacios y emojis. No mandes bloques de texto gigantes.
    3. **Vendedor Inteligente:** Si piden una pizza, sugiere una bebida (Cross-selling sutil).
    4. **Manejo de Repartidores:** Si el mensaje dice "Ya llegué", "Entregado" o frases de staff, responde militarmente: "Copiado 🫡".
    
    {current_menu}
    
    INSTRUCCIONES DE CONTEXTO:
    - Si te mandan UBICACIÓN (GPS), úsala como dirección de entrega.
    - Si te mandan AUDIO, responde confirmando que los escuchaste ("Te escuché decir...").
    - Antes de cerrar el pedido, confirma el total sumando los precios del menú de arriba.
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

        if reply_text:
            supabase.table("chat_history").insert({"phone_number": user_phone, "role": "assistant", "content": reply_text}).execute()
        
        return reply_text
    except Exception as e:
        print(f"Error GPT: {e}")
        return "Dame un segundo, estoy consultando con cocina..."

# --- 🚀 SERVIDOR ---
@asynccontextmanager
async def lifespan(app: FastAPI):
    print("🚀 KOMO BACKEND v6.0 ONLINE (NPS EDITION)")
    yield

app = FastAPI(lifespan=lifespan)

@app.get("/")
def home(): return {"status": "Komo v6.0 - NPS Ready 🌟"}

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
            
            if msg_type == "text":
                reply = await ask_gpt4(msg["text"]["body"], sender)
                await send_whatsapp_message(sender, reply)
            
            elif msg_type == "audio":
                audio_id = msg["audio"]["id"]
                audio_bytes = await download_whatsapp_media(audio_id)
                if audio_bytes:
                    text = await transcribe_audio(audio_bytes)
                    reply = await ask_gpt4(text, sender, is_audio=True)
                    await send_whatsapp_message(sender, reply)
                else:
                    await send_whatsapp_message(sender, "🎧 No pude escuchar el audio, ¿puedes escribirlo?")

            elif msg_type == "location":
                loc = msg["location"]
                coords = f"📍 Coordenadas GPS: {loc['latitude']}, {loc['longitude']}"
                reply = await ask_gpt4(coords, sender)
                await send_whatsapp_message(sender, reply)
            
            elif msg_type == "image":
                # Lógica básica para imagen (queja o confirmación)
                await send_whatsapp_message(sender, "📷 Imagen recibida. Si es una entrega o reporte, ya lo estoy procesando.")

        return {"status": "ok"}
    except Exception as e:
        print(f"Error Webhook: {e}")
        return {"status": "error"}