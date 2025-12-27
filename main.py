import os
import httpx
from contextlib import asynccontextmanager
from fastapi import FastAPI, Request, HTTPException
from supabase import create_client, Client
from openai import AsyncOpenAI
from config import settings, logger

# --- 🔐 VARIABLES DE ENTORNO ---
WHATSAPP_TOKEN = os.environ.get("WHATSAPP_TOKEN")
PHONE_NUMBER_ID = os.environ.get("PHONE_NUMBER_ID")
VERIFY_TOKEN = os.environ.get("VERIFY_TOKEN", "KOMO_TOKEN_2025")
OPENAI_API_KEY = os.environ.get("OPENAI_API_KEY")

# Clientes Globales
supabase: Client = None
openai_client: AsyncOpenAI = None
CURRENT_MENU_TEXT = "Cargando menú..." # Variable en memoria para no consultar la DB en cada segundo

# --- 🛠️ FUNCIONES AUXILIARES ---

async def get_menu_from_db():
    """
    Descarga los productos activos de Supabase y los formatea como texto
    para que GPT-4 los pueda leer.
    """
    try:
        # Consultar solo productos ACTIVOS
        response = supabase.table("products").select("*").eq("is_active", True).execute()
        products = response.data
        
        if not products:
            return "No hay productos disponibles por el momento."
        
        # Formatear bonito: "1. Pizza Pepperoni - $150 (Descripción)"
        menu_text = "MENÚ ACTUALIZADO:\n"
        for p in products:
            menu_text += f"- {p['name']}: ${p['price']} ({p.get('description', '')})\n"
            
        logger.info("✅ Menú actualizado desde Supabase")
        return menu_text
    except Exception as e:
        logger.error(f"💥 Error leyendo menú: {e}")
        return "Error cargando menú."

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

# --- 🧠 LÓGICA DE IA ---
async def ask_gpt4(user_message: str):
    global CURRENT_MENU_TEXT
    
    # 1. Definir la personalidad con el MENÚ DINÁMICO
    system_prompt = f"""
    Eres Komo, el asistente virtual de una pizzería.
    
    {CURRENT_MENU_TEXT}
    
    REGLAS:
    - Solo ofrece lo que está en el menú de arriba.
    - Si piden algo que no está, di amablemente que no lo manejamos.
    - Sé breve y conciso (estilo chat).
    - Confirma siempre el precio antes de cerrar.
    """

    try:
        response = await openai_client.chat.completions.create(
            model="gpt-4-turbo",
            messages=[
                {"role": "system", "content": system_prompt},
                {"role": "user", "content": user_message}
            ],
            temperature=0.7,
            max_tokens=150
        )
        return response.choices[0].message.content
    except Exception as e:
        return "Tuve un error procesando tu pedido. ¿Me repites?"

# --- LIFESPAN (ARRANQUE) ---
@asynccontextmanager
async def lifespan(app: FastAPI):
    global supabase, openai_client, CURRENT_MENU_TEXT
    
    # Conectar servicios
    supabase = create_client(settings.supabase_url, settings.supabase_key)
    openai_client = AsyncOpenAI(api_key=OPENAI_API_KEY)
    
    # Cargar el menú por primera vez al encender
    CURRENT_MENU_TEXT = await get_menu_from_db()
    
    logger.info("🚀 KOMO ACTIVO: Menú cargado desde DB")
    yield

app = FastAPI(title="Komo Dynamic AI", lifespan=lifespan)

# --- RUTAS ---
@app.get("/")
def home(): return {"status": "Online", "menu_loaded": len(CURRENT_MENU_TEXT) > 20}

@app.get("/health")
def health(): return {"status": "ok"}

# Endpoint oculto para forzar actualización del menú sin reiniciar
@app.post("/refresh-menu")
async def refresh_menu_manual():
    global CURRENT_MENU_TEXT
    CURRENT_MENU_TEXT = await get_menu_from_db()
    return {"status": "Menú actualizado", "content": CURRENT_MENU_TEXT}

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
            if msg["type"] == "text":
                text_user = msg["text"]["body"]
                logger.info(f"📩 Usuario: {text_user}")
                
                # Pensar con el menú actualizado
                respuesta_ia = await ask_gpt4(text_user)
                await send_whatsapp_message(phone, respuesta_ia)

        return {"status": "ok"}
    except Exception:
        return {"status": "error"}