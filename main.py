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
    logger.info("--- 🚀 INICIANDO VERSIÓN DEFINITIVA (V3) ---")
    logger.info(f"📁 Entorno: {settings.app_env}")

    global supabase
    supabase = await connect_to_supabase()

    yield

    logger.info("👋 Apagando sistema")

app = FastAPI(title=settings.app_name, lifespan=lifespan)

@app.get("/")
async def root():
    status = "Online"
    db_status = "connected" if supabase else "disconnected"
    return {
        "system": settings.app_name,
        "env": settings.app_env,
        "status": status,
        "supabase": db_status,
        "version_code": "FINAL_NO_PROXY"
    }

@app.get("/health")
async def health_check():
    return {"status": "ok"}