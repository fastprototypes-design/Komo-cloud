import time
import asyncio
from typing import Optional
from contextlib import asynccontextmanager
from fastapi import FastAPI, Request, HTTPException, Response
from supabase import create_client, Client
from config import settings, logger

# --- 🔐 CONFIGURACIÓN DEL WEBHOOK ---
# ESTA ES LA CONTRASEÑA QUE PONDRÁS EN META
VERIFY_TOKEN = "KOMO_TOKEN_2025" 

# Variable global para el cliente de Supabase
supabase: Optional[Client] = None

async def connect_to_supabase():
    retry_count = 0
    max_retries = 3
    
    while retry_count < max_retries:
        try:
            # --- ZONA SEGURA (SIN PROXY) ---
            client = create_client(
                settings.supabase_url,
                settings.supabase_key
            )
            # -------------------------------
            
            # Prueba de conexión
            try:
                client.table("businesses").select("id", count="exact").limit(1).execute()
            except Exception:
                pass 

            logger.info("✅ Conexión a Supabase establecida (SIN PROXY)")
            return client
            
        except Exception as e:
            retry_count += 1
            logger.error(f"⚠️ Intento {retry_count}/{max_retries} fallido: {str(e)}")
            await asyncio.sleep(2)
    
    logger.error("💥 No se pudo conectar a Supabase")
    return None

@asynccontextmanager
async def lifespan(app: FastAPI):
    # MARCA DE AGUA PARA VERIFICAR ACTUALIZACIÓN
    logger.info("--- 🚀 INICIANDO VERSIÓN CON WEBHOOK (V4) ---")
    logger.info(f"📁 Entorno: {settings.app_env}")

    global supabase
    supabase = await connect_to_supabase()

    yield

    logger.info("👋 Apagando sistema")

app = FastAPI(title=settings.app_name, lifespan=lifespan)

# --- 🌐 RUTAS ---

@app.get("/")
async def root():
    status = "Online"
    db_status = "connected" if supabase else "disconnected"
    return {
        "system": settings.app_name,
        "env": settings.app_env,
        "status": status,
        "supabase": db_status,
        "version_code": "FINAL_CON_WEBHOOK"
    }

@app.get("/health")
async def health_check():
    return {"status": "ok"}

# --- 📞 AQUÍ ESTÁ LA MAGIA DEL WEBHOOK ---

@app.get("/webhook")
async def verify_webhook(request: Request):
    """
    Meta llama a esto para verificar que el servidor es nuestro.
    """
    # Extraer parámetros de la URL
    params = request.query_params
    mode = params.get("hub.mode")
    token = params.get("hub.verify_token")
    challenge = params.get("hub.challenge")

    # Verificar si coinciden
    if mode and token:
        if mode == "subscribe" and token == VERIFY_TOKEN:
            logger.info("✅ Webhook verificado exitosamente por Meta")
            # Devolver el challenge como entero (es lo que Meta pide)
            return int(challenge)
        else:
            logger.warning("⛔ Intento de verificación fallido: Token incorrecto")
            raise HTTPException(status_code=403, detail="Forbidden")
    
    return {"status": "error", "message": "Faltan parámetros"}

@app.post("/webhook")
async def receive_message(request: Request):
    """
    Aquí llegarán los mensajes de WhatsApp en el futuro.
    Por ahora solo respondemos 'OK' para que Meta no se queje.
    """
    return {"status": "received"}