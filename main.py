import os
import httpx
from contextlib import asynccontextmanager
from fastapi import FastAPI, Request, HTTPException
from supabase import create_client, Client
from openai import AsyncOpenAI  # <--- IMPORTANTE: Librería oficial
from config import settings, logger

# --- 🔐 VARIABLES DE ENTORNO ---
WHATSAPP_TOKEN = os.environ.get("WHATSAPP_TOKEN")
PHONE_NUMBER_ID = os.environ.get("PHONE_NUMBER_ID")
VERIFY_TOKEN = os.environ.get("VERIFY_TOKEN", "KOMO_TOKEN_2025")
OPENAI_API_KEY = os.environ.get("OPENAI_API_KEY")

# --- 🍕 EL CEREBRO Y EL MENÚ DE KOMO ---
# Aquí es donde defines la personalidad y precios.
SYSTEM_PROMPT = """
Eres Komo, un asistente virtual amable y eficiente para una pizzería.
Tu objetivo es tomar pedidos y responder dudas.

MENÚ OFICIAL:
1. Pizza Pepperoni: $150 MXN
2. Pizza Hawaiana: $140 MXN
3. Pizza 4 Quesos: $160 MXN
4. Refresco (Coca/Sprite): $30 MXN

REGLAS DE COMPORTAMIENTO:
- Responde de forma corta y concisa (ideal para WhatsApp).
- Si te piden algo que no está en el menú, di que no lo tienes amablemente.
- Siempre confirma el precio final cuando pidan algo.
- Usa emojis con moderación 🍕🥤.
- No saludes en cada mensaje, ve al grano si ya estás hablando.
"""

# Clientes Globales
supabase: Client = None
openai_client: AsyncOpenAI = None

# --- FUNCIONES AUXILIARES ---
async def send_whatsapp_message(to_number: str, text_body: str):
    if not WHATSAPP_TOKEN: return
    url = f"https://graph.facebook.com/v17.0/{PHONE_NUMBER_ID}/messages"
    headers = {"Authorization": f"Bearer {WHATSAPP_TOKEN}", "Content-Type": "application/json"}
    data = {
        "messaging_product": "whatsapp",
        "to": to_number,
        "type": "text",
        "text": {"body": text_body},
    }
    async with httpx.AsyncClient() as client:
        await client.post(url, headers=headers, json=data)

async def ask_gpt4(user_message: str):
    """Envía el mensaje a OpenAI y recibe la respuesta"""
    try:
        if not OPENAI_API_KEY:
            return "Error: Falta configurar el cerebro (API Key)."

        response = await openai_client.chat.completions.create(
            model="gpt-4-turbo", # O gpt-3.5-turbo para ahorrar
            messages=[
                {"role": "system", "content": SYSTEM_PROMPT},
                {"role": "user", "content": user_message}
            ],
            temperature=0.7,
            max_tokens=150
        )
        return response.choices[0].message.content
    except Exception as e:
        logger.error(f"Error OpenAI: {e}")
        return "Lo siento, me distraje un momento. ¿Podrías repetir?"

# --- LIFESPAN (ARRANQUE) ---
@asynccontextmanager
async def lifespan(app: FastAPI):
    global supabase, openai_client
    supabase = create_client(settings.supabase_url, settings.supabase_key)
    openai_client = AsyncOpenAI(api_key=OPENAI_API_KEY) # Inicializamos el cerebro
    logger.info("🚀 KOMO: CEREBRO CONECTADO")
    yield

app = FastAPI(title="Komo AI", lifespan=lifespan)

# --- RUTAS ---
@app.get("/")
def home(): return {"status": "Komo AI Online 🧠"}

@app.get("/health")
def health(): return {"status": "ok"}

@app.get("/webhook")
async def verify(request: Request):
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
            phone = msg["from"]
            
            # PROCESAR TEXTO
            if msg["type"] == "text":
                text_user = msg["text"]["body"]
                logger.info(f"📩 Usuario: {text_user}")
                
                # 1. PENSAR (OpenAI)
                respuesta_ia = await ask_gpt4(text_user)
                
                # 2. RESPONDER (WhatsApp)
                await send_whatsapp_message(phone, respuesta_ia)

            # (Opcional) PROCESAR AUDIO LUEGO AQUÍ...

        return {"status": "ok"}
    except Exception:
        return {"status": "error"}