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
        response = supabase.table("products").select("*").eq("is_active", True).execute()
        products = response.data
        if not products: return "Sin productos."
        menu_text = "MENÚ DISPONIBLE:\n"
        for p in products:
            menu_text += f"- {p['name']}: ${p['price']} ({p.get('category','')})\n"
        return menu_text
    except Exception:
        return "Error menú."

# --- 🛠️ HERRAMIENTA 2: GUARDAR PEDIDO COMPLETO ---
async def registrar_pedido_db(phone: str, detalle: str, total: float, direccion: str, metodo_pago: str):
    """
    Inserta el pedido SOLO cuando tenemos todos los datos.
    """
    # Generamos un ID de orden simple
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
            "delivery_address": direccion,   # 📍 DATO REAL
            "payment_method": metodo_pago    # 💵 DATO REAL
        }
        
        supabase.table("orders").insert(data).execute()
        logger.info(f"✅ VENTA CERRADA: {order_num} | Dir: {direccion} | Pago: {metodo_pago}")
        return f"Pedido {order_num} registrado exitosamente. Enviaremos a: {direccion}."
    except Exception as e:
        logger.error(f"💥 Error guardando pedido: {e}")
        return "Error interno al guardar pedido."

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

# --- 🧠 CEREBRO GPT-4 (VENDEDOR AMABLE Y ESTRICTO CON DATOS) ---
async def ask_gpt4(user_message: str, user_phone: str):
    global CURRENT_MENU_TEXT
    
    # 🔧 HERRAMIENTA OBLIGATORIA
    tools = [
        {
            "type": "function",
            "function": {
                "name": "registrar_pedido",
                "description": "Usa esto SOLO cuando tengas PRODUCTO, DIRECCIÓN y FORMA DE PAGO.",
                "parameters": {
                    "type": "object",
                    "properties": {
                        "detalle": {"type": "string", "description": "Resumen del pedido (Ej: 1 Pizza Pepperoni)"},
                        "total": {"type": "number", "description": "Precio total numérico"},
                        "direccion": {"type": "string", "description": "Dirección de entrega completa"},
                        "metodo_pago": {"type": "string", "description": "Efectivo o Tarjeta"}
                    },
                    "required": ["detalle", "total", "direccion", "metodo_pago"]
                }
            }
        }
    ]

    # 🎭 PERSONALIDAD Y REGLAS
    system_prompt = f"""
    Eres Komo, un vendedor de pizzas experto, muy amable y con gran actitud. 🍕✨
    
    TU MENÚ:
    {CURRENT_MENU_TEXT}
    
    📜 REGLAS DE ORO PARA LA VENTA:
    
    1. **NO GUARDES A MEDIAS:**
       Si el cliente dice "Quiero la pizza", **NO** uses la herramienta todavía.
       Responde con entusiasmo: "¡Excelente elección! 🍕 El total es $150. Para enviártela, ¿me ayudas con tu dirección y forma de pago (Efectivo/Tarjeta)?"
       
    2. **RECOPILA TODO:**
       Solo llama a 'registrar_pedido' cuando el cliente te haya dado explícitamente:
       - Qué quiere.
       - Su dirección.
       - Cómo va a pagar.
       
    3. **SEDUCE Y AGRADECE:**
       - Usa emojis (🍕, 🛵, 🎉).
       - Cuando la herramienta confirme el guardado, responde al cliente confirmando que la comida va en camino y agradece la compra.
    """

    messages = [{"role": "system", "content": system_prompt}, {"role": "user", "content": user_message}]

    try:
        response = await openai_client.chat.completions.create(
            model="gpt-4-turbo", messages=messages, tools=tools, tool_choice="auto", temperature=0.7
        )
        msg = response.choices[0].message
        
        # Si GPT decide llamar a la herramienta (tiene todos los datos)
        if msg.tool_calls:
            tool_call = msg.tool_calls[0]
            args = json.loads(tool_call.function.arguments)
            
            # Guardamos en Supabase
            resultado_db = await registrar_pedido_db(user_phone, args["detalle"], args["total"], args["direccion"], args["metodo_pago"])
            
            # Le damos el resultado a GPT para que se despida bonito
            messages.append(msg)
            messages.append({"role": "tool", "tool_call_id": tool_call.id, "content": resultado_db})
            
            final_response = await openai_client.chat.completions.create(
                model="gpt-4-turbo", messages=messages
            )
            return final_response.choices[0].message.content
        
        return msg.content
    except Exception as e:
        logger.error(f"Error GPT: {e}")
        return "Dame un segundo, estoy procesando tu solicitud..."

# --- ARRANQUE ---
@asynccontextmanager
async def lifespan(app: FastAPI):
    global supabase, openai_client, CURRENT_MENU_TEXT
    supabase = create_client(settings.supabase_url, settings.supabase_key)
    openai_client = AsyncOpenAI(api_key=OPENAI_API_KEY)
    CURRENT_MENU_TEXT = await get_menu_from_db()
    
    if not BUSINESS_ID or not BRANCH_ID:
        logger.warning("⚠️ FALTAN IDs EN RENDER")
        
    logger.info("🚀 KOMO V2: MODO AMABLE Y COMPLETO ACTIVO")
    yield

app = FastAPI(title="Komo Enterprise", lifespan=lifespan)

@app.get("/")
def home(): return {"status": "Online 🍕"}

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