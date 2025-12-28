import os
import httpx
import json
import time
from contextlib import asynccontextmanager
from fastapi import FastAPI, Request, HTTPException
from supabase import create_client, Client
from openai import AsyncOpenAI
from config import settings, logger

# --- 🔐 VARIABLES ---
WHATSAPP_TOKEN = os.environ.get("WHATSAPP_TOKEN")
PHONE_NUMBER_ID = os.environ.get("PHONE_NUMBER_ID")
VERIFY_TOKEN = os.environ.get("VERIFY_TOKEN", "KOMO_TOKEN_2025")
OPENAI_API_KEY = os.environ.get("OPENAI_API_KEY")
BUSINESS_ID = os.environ.get("BUSINESS_ID")
BRANCH_ID = os.environ.get("BRANCH_ID")

supabase: Client = None
openai_client: AsyncOpenAI = None
CURRENT_MENU_TEXT = "Cargando menú..."

# --- 🛠️ 1. GESTIÓN DE MEMORIA (NUEVO) ---
async def save_message(phone: str, role: str, content: str):
    """Guarda un mensaje en el historial"""
    try:
        supabase.table("chat_history").insert({
            "phone_number": phone,
            "role": role,
            "content": content
        }).execute()
    except Exception as e:
        logger.error(f"Error guardando historial: {e}")

async def get_chat_history(phone: str, limit: int = 6):
    """Recupera los últimos mensajes para dar contexto"""
    try:
        response = supabase.table("chat_history")\
            .select("role, content")\
            .eq("phone_number", phone)\
            .order("created_at", desc=True)\
            .limit(limit)\
            .execute()
        
        # Vienen del más nuevo al más viejo, hay que invertirlos para GPT
        history = response.data[::-1] 
        return history
    except Exception:
        return []

# --- 🛠️ 2. MENÚ Y PEDIDOS ---
async def get_menu_from_db():
    try:
        response = supabase.table("products").select("*").eq("is_active", True).execute()
        products = response.data
        if not products: return "Sin productos."
        menu_text = "MENÚ:\n"
        for p in products:
            menu_text += f"- {p['name']}: ${p['price']}\n"
        return menu_text
    except Exception: return "Error menú."

async def registrar_pedido_db(phone: str, detalle: str, total: float, direccion: str, metodo_pago: str):
    order_num = f"ORD-{int(time.time())}"
    try:
        data = {
            "business_id": BUSINESS_ID, "branch_id": BRANCH_ID,
            "customer_phone": phone, "order_details": detalle,
            "total_price": total, "status": "confirmed",
            "order_number": order_num, "order_type": "delivery",
            "delivery_address": direccion, "payment_method": metodo_pago
        }
        supabase.table("orders").insert(data).execute()
        return f"Pedido {order_num} registrado exitosamente. Enviaremos a: {direccion}."
    except Exception as e:
        return f"Error guardando pedido: {str(e)}"

# --- 📡 WHATSAPP ---
async def send_whatsapp_message(to_number: str, text_body: str):
    if not WHATSAPP_TOKEN: return
    url = f"https://graph.facebook.com/v17.0/{PHONE_NUMBER_ID}/messages"
    headers = {"Authorization": f"Bearer {WHATSAPP_TOKEN}", "Content-Type": "application/json"}
    data = {"messaging_product": "whatsapp", "to": to_number, "type": "text", "text": {"body": text_body}}
    async with httpx.AsyncClient() as client:
        await client.post(url, headers=headers, json=data)

# --- 🧠 CEREBRO CON MEMORIA ---
async def ask_gpt4(user_message: str, user_phone: str):
    global CURRENT_MENU_TEXT
    
    # 1. Guardamos lo que el usuario acaba de decir
    await save_message(user_phone, "user", user_message)
    
    # 2. Recuperamos el historial (Contexto)
    history = await get_chat_history(user_phone)
    
    tools = [{
        "type": "function",
        "function": {
            "name": "registrar_pedido",
            "description": "Usa esto SOLO con: PRODUCTO, DIRECCIÓN y PAGO.",
            "parameters": {
                "type": "object",
                "properties": {
                    "detalle": {"type": "string"},
                    "total": {"type": "number"},
                    "direccion": {"type": "string"},
                    "metodo_pago": {"type": "string"}
                },
                "required": ["detalle", "total", "direccion", "metodo_pago"]
            }
        }
    }]

    system_prompt = f"""
    Eres Komo, vendedor de pizzas amable. 🍕
    MENÚ: {CURRENT_MENU_TEXT}
    
    REGLAS DE MEMORIA Y VENTA:
    1. Tienes acceso al historial de la charla. ÚSALO. 
       Si el cliente dice "Calle 123", busca en los mensajes anteriores qué pizza quería.
    2. NO registres el pedido hasta tener: QUÉ QUIERE + DIRECCIÓN + PAGO.
    3. Si falta algo, pídelo amablemente.
    4. Cuando registres, confirma y agradece.
    """

    # Construimos la conversación: Sistema + Historial Reciente
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
            resultado_db = await registrar_pedido_db(user_phone, args["detalle"], args["total"], args["direccion"], args["metodo_pago"])
            
            messages.append(msg)
            messages.append({"role": "tool", "tool_call_id": tool_call.id, "content": resultado_db})
            
            final_response = await openai_client.chat.completions.create(
                model="gpt-4-turbo", messages=messages
            )
            bot_reply = final_response.choices[0].message.content
        else:
            bot_reply = msg.content

        # 3. Guardamos la respuesta del bot para recordarla después
        await save_message(user_phone, "assistant", bot_reply)
        return bot_reply

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
    logger.info("🚀 KOMO CON MEMORIA ACTIVO")
    yield

app = FastAPI(title="Komo Memory", lifespan=lifespan)

@app.get("/")
def home(): return {"status": "Brain Online 🧠"}

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
                # Nota: ask_gpt4 ahora se encarga de guardar el historial
                await send_whatsapp_message(msg["from"], await ask_gpt4(msg["text"]["body"], msg["from"]))
        return {"status": "ok"}
    except Exception:
        return {"status": "error"}