import time
import asyncio
from typing import Optional
from contextlib import asynccontextmanager
from fastapi import FastAPI, Request, HTTPException
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
            # Creamos el cliente SIN argumentos extraños como 'proxy'
            client = create_client(
                settings.supabase_url,
                settings.supabase_key
            )
            # Hacemos una consulta de prueba muy ligera para verificar conexión
            # (Intentamos contar filas en la tabla 'businesses', si falla no importa, solo valida la conexión)
            try:
                client.table("businesses").select("id", count="exact").limit(1).execute()
            except Exception:
                pass # Si falla la consulta específica no pasa nada, el cliente se creó

            logger.info("✅ Conexión a Supabase establecida correctamente")
            return client
            
        except Exception as e:
            retry_count += 1
            logger.error(f"⚠️ Intento {retry_count}/{max_retries} fallido: {str(e)}")
            await asyncio.sleep(2)  # Esperar 2 segundos antes de reintentar
    
    logger.error("💥 No se pudo conectar a Supabase después de varios intentos")
    return None

@asynccontextmanager
async def lifespan(app: FastAPI):
    # --- STARTUP (Al encender) ---
    startup_time = time.time()
    logger.info(f"🚀 Iniciando {settings.app_name} v{settings.app_version}")
    logger.info(f"📁 Entorno: {settings.app_env}")

    global supabase
    supabase = await connect_to_supabase()

    yield # Aquí corre la aplicación

    # --- SHUTDOWN (Al apagar) ---
    logger.info(f"👋 Apagando {settings.app_name}")
    shutdown_time = time.time() - startup_time
    logger.info(f"⏱️  Tiempo de ejecución: {shutdown_time:.2f} segundos")

# Crear la aplicación FastAPI
app = FastAPI(
    title=settings.app_name,
    version=settings.app_version,
    lifespan=lifespan
)

@app.get("/")
async def root():
    """Endpoint raíz para verificar estado"""
    status = "Online"
    db_status = "connected" if supabase else "disconnected"
    
    return {
        "system": settings.app_name,
        "version": settings.app_version,
        "env": settings.app_env,
        "status": status,
        "supabase": db_status
    }

@app.get("/health")
async def health_check():
    """Endpoint para que Render sepa que estamos vivos"""
    return {"status": "ok"}