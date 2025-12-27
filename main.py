import time
import asyncio
from typing import Optional
from contextlib import asynccontextmanager
from fastapi import FastAPI
from supabase import create_client, Client
from config import settings, logger

# Variable global para el cliente de Supabase
supabase: Optional[Client] = None

async def connect_to_supabase():
    """Conecta a Supabase con reintentos inteligentes"""
    retry_count = 0
    max_retries = 3
    
    while retry_count < max_retries:
        try:
            # Creamos el cliente SIN proxy, solo URL y Key
            client = create_client(
                settings.supabase_url,
                settings.supabase_key
            )
            # Prueba de conexión rápida
            try:
                client.table("businesses").select("id", count="exact").limit(1).execute()
            except Exception:
                pass 

            logger.info("✅ Conexión a Supabase establecida correctamente")
            return client
            
        except Exception as e:
            retry_count += 1
            logger.error(f"⚠️ Intento {retry_count}/{max_retries} fallido: {str(e)}")
            await asyncio.sleep(2)
    
    logger.error("💥 No se pudo conectar a Supabase después de varios intentos")
    return None

@asynccontextmanager
async def lifespan(app: FastAPI):
    # --- STARTUP ---
    logger.info(f"🚀 Iniciando {settings.app_name}")
    logger.info(f"📁 Entorno: {settings.app_env}")

    global supabase
    supabase = await connect_to_supabase()

    yield

    # --- SHUTDOWN ---
    logger.info(f"👋 Apagando {settings.app_name}")

app = FastAPI(title=settings.app_name, lifespan=lifespan)

@app.get("/")
async def root():
    status = "Online"
    db_status = "connected" if supabase else "disconnected"
    return {
        "system": settings.app_name,
        "env": settings.app_env,
        "status": status,
        "supabase": db_status
    }

@app.get("/health")
async def health_check():
    return {"status": "ok"}