import os
import asyncio
import httpx # Necesario para enviar mensajes a WhatsApp
import json
from typing import Optional
from contextlib import asynccontextmanager
from fastapi import FastAPI, Request, HTTPException
from supabase import create_client, Client
from config import settings, logger

# --- 🔐 SEGURIDAD: LECTURA DE VARIABLES DE ENTORNO ---
# Estas variables se configuran en el panel de Render, NO aquí en el código.
WHATSAPP_TOKEN = os.environ.get("WHATSAPP_TOKEN")
PHONE_NUMBER_ID = os.environ.get("PHONE_NUMBER_ID")
VERIFY_TOKEN = os.environ.get("VERIFY_TOKEN", "KOMO_TOKEN_2025")

# --- 🛠️ FUNCIÓN PARA ENVIAR WHATSAPP ---
async def send_whatsapp_message(to_number: str, text_body: str):
    """Envía un mensaje de texto de vuelta a WhatsApp usando la API oficial"""
    if not WHATSAPP_TOKEN or not PHONE_NUMBER_ID:
        logger.error("❌ Faltan credenciales de WhatsApp en Render")
        return

    url = f"https://graph.facebook.com/v17.0/{PHONE_NUMBER_ID}/messages"
    headers = {
        "Authorization": f"Bearer {WHATSAPP_TOKEN}",
        "Content-Type": "application/json",
    }
    data = {
        "messaging_product": "whatsapp",
        "to": to_number,
        "type": "text",
        "text": {"body": text_body},
    }
    
    async with httpx.AsyncClient() as client:
        try:
            response = await client.post(url, headers=headers, json=data)
            if response.status_code == 200:
                logger.info(f"📤 Mensaje enviado correctamente a {to_number}")
            else:
                logger.error(f"💥 Error de Meta: {response.text}")
        except Exception as e:
            logger.error(f"💥 Error de conexión: {e}")

# --- 🗄️ SUPABASE (Base de Datos) ---
supabase: Optional[Client] = None

async def connect_to_supabase():
    retry_count = 0
    max_retries = 3
    while retry_count < max_retries:
        try:
            client = create_client(settings.supabase_url, settings.supabase_key)
            # Prueba rápida de conexión
            client.table("businesses").select("id", count="exact").limit(1).execute()
            logger.info("✅ Conexión a Supabase establecida")
            return client
        except Exception as e:
            retry_count += 1
            logger.warning(f"⚠️ Reintentando conexión DB ({retry_count}/{max_retries})...")
            await asyncio.sleep(2)
    logger.error("💥 No se pudo conectar a Supabase")
    return None

# --- 🚀 ARRANQUE (LIFESPAN) ---
@asynccontextmanager
async def lifespan(app: FastAPI):
    logger.info("--- 🚀 INICIANDO KOMO BOT (SECURE MODE) ---")
    
    # Validar que tengamos las llaves
    if not WHATSAPP_TOKEN:
        logger.warning("⚠️ ALERTA: No se encontró WHATSAPP_TOKEN en Render")
    
    global supabase
    supabase = await connect_to_supabase()
    yield
    logger.info("👋 Apagando sistema")

app = FastAPI(title=settings.app_name, lifespan=lifespan)

# --- 🌐 RUTAS ---

@app.get("/")
async def root():
    return {
        "system": "Komo Delivery Bot", 
        "status": "Online", 
        "mode": "Echo/Respuesta Automática",
        "db": "Conectada" if supabase else "Desconectada"
    }

@app.get("/health")
async def health_check():
    return {"status": "ok"}

# --- 🔗 WEBHOOK: VERIFICACIÓN (Lo que usa el botón azul de Meta) ---
@app.get("/webhook")
async def verify_webhook(request: Request):
    params = request.query_params
    mode = params.get("hub.mode")
    token = params.get("hub.verify_token")
    challenge = params.get("hub.challenge")

    if mode == "subscribe" and token == VERIFY_TOKEN:
        logger.info("✅ Webhook verificado por Meta")
        return int(challenge)
    
    logger.warning("⛔ Intento de verificación fallido")
    raise HTTPException(status_code=403, detail="Forbidden")

# --- 📩 WEBHOOK: RECEPCIÓN DE MENSAJES ---
@app.post("/webhook")
async def receive_message(request: Request):
    try:
        body = await request.json()
        
        # Estructura compleja de Meta: entry -> changes -> value -> messages
        entry = body.get("entry", [])[0]
        changes = entry.get("changes", [])[0]
        value = changes.get("value", {})
        
        # AQUÍ ESTABA EL ERROR: Asegúrate de que termine en []
        messages = value.get("messages", [])

        if messages:
            message = messages[0]
            phone_number = message["from"] # Número del cliente
            
            # 1. Si es TEXTO
            if message["type"] == "text":
                text_received = message["text"]["body"]
                logger.info(f"📩 Mensaje de {phone_number}: {text_received}")
                
                # RESPUESTA AUTOMÁTICA (ECO)
                mensaje_respuesta = f"🤖 Hola! Soy el servidor de Komo. Dijiste: '{text_received}'"
                await send_whatsapp_message(phone_number, mensaje_respuesta)

            # 2. Si es AUDIO
            elif message["type"] == "audio":
                logger.info(f"🎤 Audio recibido de {phone_number}")
                await send_whatsapp_message(phone_number, "👂 Recibí tu audio, pronto podré escucharlo.")

        return {"status": "processed"}
        
    except Exception as e:
        # A veces Meta manda notificaciones de estado (leído, enviado) que no tienen "messages"
        # No es un error crítico, así que solo devolvemos OK
        return {"status": "ignored"}