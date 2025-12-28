import os
import httpx
import json
import time
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

# --- 🏢 CREDENCIALES DEL NEGOCIO ---
BUSINESS_ID = os.environ.get("BUSINESS_ID")
BRANCH_ID = os.environ.get("BRANCH_ID")

# Clientes Globales
supabase: Client = None
openai_client: AsyncOpenAI = None
CURRENT_MENU_TEXT = "Cargando menú..."

# --- 🛠️ HERRAMIENTA 1: MENÚ ---
async def get_menu_from_db():
    try:
        # Filtramos productos activos
        response = supabase.table("products").select("*").eq("is_active", True).execute()
        products = response.data
        if not products: return "Sin productos."
        
        menu_text = "MENÚ DISPONIBLE:\n"
        for p in products:
            menu_text += f"- {p['name']}: ${p['price']} ({p.get('category','')})\n"
        return menu_text
    except Exception:
        return "Error menú."

# --- 🛠️ HERRAMIENTA 2: GUARDAR PEDIDO (ENTERPRISE) ---
async def registrar_pedido_db(phone: str, detalle: str, total: float):
    """
    Inserta el pedido llenando TODOS los campos de la tabla avanzada.
    """
    # Generar un número de orden único simple (Timestamp)
    order_num = f"ORD-{int(time.time())}"
    
    try:
        data = {
            "business_id": BUSINESS_ID,      # 🔗 Vinculado a tu negocio
            "branch_id": BRANCH_ID,          # 🔗 Vinculado a tu sucursal
            "customer_phone": phone,
            "order_details": detalle,
            "total_price": total,
            "status": "confirmed",
            "order_number": order_num,       # 🔢 ID legible #ORD-1765...
            "order_type": "delivery",        # Valor por defecto
            "delivery_address": "Por definir en chat" # Placeholder
        }
        
        # Insertar en la tabla 'orders'
        supabase.table("orders").insert(data).execute()
        
        logger.info(f"✅ PEDIDO COMPLETO GUARDADO: {order_num} - {detalle}")
        return f"Pedido {order_num} registrado correctamente."
    except Exception as e:
        logger.error(f"💥 Error guardando pedido: {e}")
        # Si falla por Foreign Key (IDs mal copiados), avisamos en el log
        if "foreign key constraint" in str(e):
            return "Error: IDs de negocio/sucursal inválidos."
        return "Error interno guardando pedido."

# --- 📡 WHATSAPP ---
async def send_whatsapp_message(to_number: str, text_body: str):
    if not WHATSAPP_TOKEN: return
    url = f"https://graph.facebook.com/v17.0/{PHONE_NUMBER_ID}/messages"
    headers = {"Authorization": f"Bearer {WHATSAPP_TOKEN}", "Content-Type": "application/json"}
    data = {
        "messaging_product": "whatsapp",
        "to": to_number, "type": "text", "text": {"body": text_body},
    }
    async with httpx.AsyncClient() as client:
        await client.post(url, headers=headers, json=data)

# --- 🧠 IA (Function Calling) ---
async def ask_gpt4(user_message: str, user_phone: str):
    global CURRENT_MENU_TEXT
    
    tools = [
        {
            "type": "function",
            "function": {
                "name": "registrar_pedido",
                "description": "Usa esto SOLO cuando el cliente confirme COMPRAR.",
                "parameters": {
                    "type": "object",
                    "properties": {
                        "detalle": {"type": "string", "description": "Resumen productos"},
                        "total": {"type": "number", "description": "Total precio"}
                    },
                    "required": ["detalle", "total"]
                }
            }
        }
    ]

    system_prompt = f"""
    Eres Komo. 
    {CURRENT_MENU_TEXT}
    REGLAS:
    1. Responde corto.
    2. Si confirman venta, EJECUTA 'registrar_pedido'.
    """

    messages = [{"role": "system", "content": system_prompt}, {"role": "user", "content": user_message}]

    try:
        response = await openai_client.chat.completions.create(
            model="gpt-4-turbo", messages=messages, tools=tools, tool_choice="auto", temperature=0.7
        )
        msg = response.choices[0].message
        
        if msg.tool_calls:
            tool_call = msg.tool_calls[0]
            args = json.loads(tool_call.function.arguments)
            resultado_db = await registrar_pedido_db(user_phone, args["detalle"], args["total"])
            
            messages.append(msg)
            messages.append({"role": "tool", "tool_call_id": tool_call.id, "content": resultado_db})
            
            final_response = await openai_client.chat.completions.create(
                model="gpt-4-turbo", messages=messages
            )
            return final_response.choices[0].message.content
        
        return msg.content
    except Exception as e:
        logger.error(f"Error GPT: {e}")
        return "Un momento..."

# --- ARRANQUE ---
@asynccontextmanager
async def lifespan(app: FastAPI):
    global supabase, openai_client, CURRENT_MENU_TEXT
    supabase = create_client(settings.supabase_url, settings.supabase_key)
    openai_client = AsyncOpenAI(api_key=OPENAI_API_KEY)
    CURRENT_MENU_TEXT = await get_menu_from_db()
    
    # Check rápido de IDs
    if not BUSINESS_ID or not BRANCH_ID:
        logger.warning("⚠️ OJO: Faltan BUSINESS_ID o BRANCH_ID en Render")
        
    logger.info("🚀 KOMO ENTERPRISE ACTIVO")
    yield

app = FastAPI(title="Komo Enterprise", lifespan=lifespan)

@app.get("/")
def home(): return {"status": "Enterprise System Online 🏢"}

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
        messages = body.get("entry", [])[0].get("changes", [])[0].get("value", {}).get("messages", [])
        if messages:
            msg = messages[0]
            if msg["type"] == "text":
                await send_whatsapp_message(msg["from"], await ask_gpt4(msg["text"]["body"], msg["from"]))
        return {"status": "ok"}
    except Exception:
        return {"status": "error"}