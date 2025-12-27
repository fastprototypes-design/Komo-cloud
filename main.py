from fastapi import FastAPI, Request, HTTPException, BackgroundTasks
from supabase import create_client, Client
from config import settings, logger
import asyncio
import time
from typing import Optional
from contextlib import asynccontextmanager

# Lifespan manager para manejo de conexiones
@asynccontextmanager
async def lifespan(app: FastAPI):
    # Startup
    startup_time = time.time()
    logger.info(f"🚀 Iniciando {settings.app_name} v{settings.app_version}")
    logger.info(f"📁 Entorno: {settings.app_env}")
    
    # Conectar a Supabase de forma asíncrona
    global supabase
    supabase = await connect_to_supabase()
    
    yield
    
    # Shutdown
    logger.info(f"👋 Apagando {settings.app_name}")
    shutdown_time = time.time() - startup_time
    logger.info(f"⏱️  Tiempo de ejecución: {shutdown_time:.2f} segundos")

app = FastAPI(
    title=settings.app_name, 
    version=settings.app_version,
    lifespan=lifespan
)

# Variable global para Supabase
supabase: Optional[Client] = None

async def connect_to_supabase() -> Optional[Client]:
    """Conectar a Supabase de forma asíncrona y segura"""
    try:
        if not settings.supabase_url or not settings.supabase_key:
            logger.warning("⚠️ Credenciales de Supabase no configuradas")
            return None
        
        client = create_client(settings.supabase_url, settings.supabase_key)
        
        # Test de conexión
        response = client.table("businesses").select("*").limit(1).execute()
        
        if hasattr(response, 'data'):
            logger.info("✅ Conexión a Supabase exitosa")
            return client
        else:
            logger.error("❌ No se pudo verificar conexión a Supabase")
            return None
            
    except Exception as e:
        logger.error(f"💥 Error conectando a Supabase: {e}")
        
        if settings.app_env == "production":
            # En producción, podemos continuar pero con funcionalidad limitada
            logger.warning("Modo degradado: Supabase no disponible")
        return None

@app.get("/")
def home():
    return {
        "system": settings.app_name,
        "version": settings.app_version,
        "env": settings.app_env,
        "status": "Online",
        "supabase": "connected" if supabase else "disconnected"
    }

@app.get("/health")
async def health_check():
    """Endpoint de salud para Render"""
    health_status = {
        "status": "healthy" if supabase else "degraded",
        "timestamp": time.time(),
        "services": {
            "api": "healthy",
            "supabase": "healthy" if supabase else "unhealthy"
        }
    }
    return health_status

# --- 1. VERIFICACIÓN DE SEGURIDAD (El Portero) ---
@app.get("/webhook")
async def verify_webhook(request: Request):
    token = request.query_params.get("hub.verify_token")
    challenge = request.query_params.get("hub.challenge")
    
    if not token or not challenge:
        logger.warning("⚠️ Parámetros faltantes en verificación")
        raise HTTPException(status_code=400, detail="Parámetros faltantes")
    
    if token == settings.whatsapp_verify_token:
        logger.info("🔐 Verificación de Webhook exitosa")
        return int(challenge)
    
    logger.warning(f"⚠️ Intento de verificación fallido. Token recibido: {token[:10]}...")
    raise HTTPException(status_code=403, detail="Token inválido")

# --- 2. EL ROUTER DE CLIENTES (El Núcleo) ---
@app.post("/webhook")
async def receive_message(request: Request, background_tasks: BackgroundTasks):
    try:
        data = await request.json()
        
        # Log de recepción
        logger.info("📨 Webhook recibido de WhatsApp")
        
        # Estructura segura
        entries = data.get('entry', [])
        if not entries:
            logger.warning("Webhook sin entries")
            return {"status": "no_entries"}
        
        entry = entries[0]
        changes = entry.get('changes', [])
        if not changes:
            logger.warning("Webhook sin changes")
            return {"status": "no_changes"}
        
        value = changes[0].get('value', {})
        
        # Extraer metadata
        metadata = value.get('metadata', {})
        phone_id_destino = metadata.get('phone_number_id', 'Desconocido')
        
        logger.info(f"📱 Mensaje para negocio ID: {phone_id_destino}")
        
        # Verificar si hay mensajes
        if 'messages' in value:
            mensajes = value['messages']
            logger.info(f"📩 {len(mensajes)} mensaje(s) recibido(s)")
            
            for mensaje in mensajes:
                usuario_celular = mensaje.get('from')
                tipo_mensaje = mensaje.get('type')
                
                logger.info(f"👤 Usuario: {usuario_celular} | Tipo: {tipo_mensaje}")
                
                # Procesar en background para responder rápido
                background_tasks.add_task(
                    process_message_async,
                    mensaje,
                    phone_id_destino,
                    usuario_celular
                )
        
        # Responder inmediatamente a WhatsApp
        return {"status": "received", "processed": "background"}
        
    except Exception as e:
        logger.error(f"💥 Error procesando webhook: {e}", exc_info=True)
        # Retornamos éxito para que WhatsApp no reintente
        return {"status": "error_handled"}

async def process_message_async(mensaje: dict, phone_id: str, user: str):
    """Procesar mensaje en segundo plano"""
    try:
        logger.info(f"🔄 Procesando mensaje async de {user}")
        
        # 1. Buscar negocio en Supabase
        business_info = await get_business_info(phone_id)
        
        if business_info:
            logger.info(f"🏪 Negocio encontrado: {business_info.get('business_name')}")
            
            # 2. Procesar tipo de mensaje
            tipo = mensaje.get('type')
            
            if tipo == 'text':
                texto = mensaje.get('text', {}).get('body', '')
                await process_text_message(texto, business_info, user)
            elif tipo == 'audio':
                logger.info("🎤 Audio recibido (procesamiento pendiente)")
            elif tipo == 'image':
                logger.info("🖼️ Imagen recibida (procesamiento pendiente)")
                
        else:
            logger.warning(f"⚠️ Negocio no encontrado para phone_id: {phone_id}")
            
    except Exception as e:
        logger.error(f"💥 Error en procesamiento async: {e}")

async def get_business_info(phone_id: str) -> Optional[dict]:
    """Obtener información del negocio desde Supabase"""
    if not supabase:
        logger.warning("Supabase no disponible para buscar negocio")
        return None
    
    try:
        response = supabase.table("businesses")\
            .select("*")\
            .eq("phone_number_id", phone_id)\
            .eq("is_active", True)\
            .execute()
        
        if response.data and len(response.data) > 0:
            return response.data[0]
        return None
        
    except Exception as e:
        logger.error(f"Error al buscar negocio: {e}")
        return None

async def process_text_message(texto: str, business_info: dict, user: str):
    """Procesar mensaje de texto con IA"""
    # TODO: Integrar con OpenAI
    logger.info(f"💬 Procesando texto: '{texto[:50]}...'")
    
    # Mock response por ahora
    mock_response = f"Gracias por tu mensaje. Pronto te atenderemos."
    logger.info(f"🤖 Respuesta mock: {mock_response}")
    
    # TODO: Enviar respuesta a WhatsApp