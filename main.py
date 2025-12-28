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

# --- 🔔 TELÉFONO DEL GERENTE (Para Notificaciones) ---
# Ya configurado con el número que proporcionaste (sin el +)
MANAGER_PHONE = "5218138734122"

# Clientes Globales
supabase: Client = None
openai_client: AsyncOpenAI = None
CURRENT_MENU_TEXT = "Cargando menú..."

# --- 📡 WHATSAPP (Función de Envío) ---
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

# --- 🛠️ 1. GESTIÓN DE MEMORIA (Historial) ---
async def save_message(phone: str, role: str, content: str):
    """Guarda mensajes en Supabase para tener memoria"""
    try:
        supabase.table("chat_history").insert({
            "phone_number": phone, "role": role, "content": content
        }).execute()
    except Exception as e:
        logger.error(f"Error guardando historial: {e}")

async def get_chat_history(phone: str, limit: int = 6):
    """Recupera los últimos mensajes para contexto"""
    try:
        response = supabase.table("chat_history")\
            .select("role, content")\
            .eq("phone_number", phone)\
            .order("created_at", desc=True)\
            .limit(limit)\
            .execute()
        return response.data[::-1] # Invertimos para que sea cronológico
    except Exception:
        return []

# --- 🛠️ 2. MENÚ ---
async def get_menu_from_db():
    try:
        response = supabase.table("products").select("*").eq("is_active", True).execute()
        products = response.data
        if not products: return "Sin productos."
        menu_text = "MENÚ DISPONIBLE:\n"
        for p in products:
            menu_text += f"- {p['name']}: ${p['price']} ({p.get('category','')})\n"
        return menu_text
    except Exception:
        return "Error cargando menú."

# --- 🛠️ 3. REGISTRAR PEDIDO Y NOTIFICAR AL GERENTE ---
async def registrar_pedido_db(phone: str, detalle: str, total: float, direccion: str, metodo_pago: str):
    order_num = f"ORD-{int(time.time())}"
    
    try:
        data = {
            "business_id": BUSINESS_ID,
            "branch_id": BRANCH_ID,
            "customer_phone": phone,
            "order_details": detalle,
            "total_price": total,
            "status": "confirmed",
            "order_number": order_num,
            "order_type": "delivery",
            "delivery_address": direccion,
            "payment_method": metodo_pago
        }
        
        # 1. Guardar en Base de Datos
        supabase.table("orders").insert(data).execute()
        
        # 2. 🔔 NOTIFICACIÓN AUTOMÁTICA AL GERENTE
        mensaje_gerente = f"""🔔 *NUEVO PEDIDO CONFIRMADO*
🆔 Orden: {order_num}
👤 Cliente: {phone}
🍕 Pedido: {detalle}
💰 Total: ${total}
📍 Dirección: {direccion}
💳 Pago: {metodo_pago}

_Revisa Supabase para más detalles._"""

        # Enviamos la alerta al número 8138734122
        await send_whatsapp_message(MANAGER_PHONE, mensaje_gerente)
        
        logger.info(f"✅ Pedido {order_num} guardado y notificado al gerente.")
        return f"Pedido {order_num} registrado exitosamente. Enviaremos a: {direccion}."
        
    except Exception as e:
        logger.error(f"💥 Error guardando pedido: {e}")
        return "Error interno al procesar el pedido."

# --- 🧠 CEREBRO GPT-4 (VENDEDOR) ---
async def ask_gpt4(user_message: str, user_phone: str):
    global CURRENT_MENU_TEXT
    
    # 1. Guardar mensaje del usuario
    await save_message(user_phone, "user", user_message)
    
    # 2. Leer historial reciente
    history = await get_chat_history(user_phone)
    
    tools = [{
        "type": "function",
        "function": {
            "name": "registrar_pedido",
            "description": "Usa esto SOLO cuando tengas: PRODUCTO, DIRECCIÓN y FORMA DE PAGO.",
            "parameters": {
                "type": "object",
                "properties": {
                    "detalle": {"type": "string", "description": "Resumen (ej: 1 Pizza Pepperoni)"},
                    "total": {"type": "number", "description": "Total a pagar"},
                    "direccion": {"type": "string", "description": "Dirección completa"},
                    "metodo_pago": {"type": "string", "description": "Efectivo o Tarjeta"}
                },
                "required": ["detalle", "total", "direccion", "metodo_pago"]
            }
        }
    }]

    system_prompt = f"""
    Eres Komo, un vendedor de pizzas amable y eficiente. 🍕
    MENÚ ACTUAL:
    {CURRENT_MENU_TEXT}
    
    REGLAS:
    1. Usa el historial para recordar qué quería el cliente.
    2. NO registres el pedido hasta tener: PRODUCTO + DIRECCIÓN + MÉTODO DE PAGO.
    3. Si falta algún dato, pídelo amablemente.
    4. Cuando confirmes el registro, despídete agradeciendo.
    """

    messages = [{"role": "system", "content": system_prompt}] + history

    try:
        response = await openai_client.chat.completions.create(
            model="gpt-4-turbo", messages=messages, tools=tools, tool_choice="auto", temperature=0.7
        )
        msg = response.choices[0].message
        
        bot_reply = ""
        
        if msg.tool_calls:
            tool_call = msg.tool_calls[0]
            args = json.loads(tool_call.function.arguments)
            
            # Guardar y Notificar
            resultado_db = await registrar_pedido_db(user_phone, args["detalle"], args["total"], args["direccion"], args["metodo_pago"])
            
            messages.append(msg)
            messages.append({"role": "tool", "tool_call_id": tool_call.id, "content": resultado_db})
            
            final_response = await openai_client.chat.completions.create(
                model="gpt-4-turbo", messages=messages
            )
            bot_reply = final_response.choices[0].message.content
        else:
            bot_reply = msg.content
            
        # 3. Guardar respuesta del bot
        await save_message(user_phone, "assistant", bot_reply)
        return bot_reply

    except Exception as e:
        logger.error(f"Error GPT: {e}")
        return "Dame un segundo, estoy verificando..."

# --- ARRANQUE DEL SERVIDOR ---
@asynccontextmanager
async def lifespan(app: FastAPI):
    global supabase, openai_client, CURRENT_MENU_TEXT
    supabase = create_client(settings.supabase_url, settings.supabase_key)
    openai_client = AsyncOpenAI(api_key=OPENAI_API_KEY)
    CURRENT_MENU_TEXT = await get_menu_from_db()
    
    if not BUSINESS_ID or not BRANCH_ID:
        logger.warning("⚠️ FALTAN IDs EN RENDER")
        
    logger.info(f"🚀 KOMO ACTIVO | Notificaciones al gerente: {MANAGER_PHONE}")
    yield

app = FastAPI(title="Komo Delivery System", lifespan=lifespan)

@app.get("/")
def home(): return {"status": "System Online 🍕"}

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
                # El bot responde al cliente
                await send_whatsapp_message(msg["from"], await ask_gpt4(msg["text"]["body"], msg["from"]))
        return {"status": "ok"}
    except Exception:
        return {"status": "error"}