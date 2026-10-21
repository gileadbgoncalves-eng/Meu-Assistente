"""Aplicação FastAPI e Webhook da WhatsApp Cloud API."""

from contextlib import asynccontextmanager
import logging
from typing import Optional
from fastapi import FastAPI, Request, Response, HTTPException, Query, BackgroundTasks
from fastapi.responses import PlainTextResponse

from app.config import settings
from app.db.session import init_db, close_db, async_session_factory
from app.channels.whatsapp import WhatsAppClient, IncomingWhatsAppMessage
from app.services.assistant import AssistantService

logging.basicConfig(level=getattr(logging, settings.LOG_LEVEL.upper(), logging.INFO))
logger = logging.getLogger("meu_assistente.api")

whatsapp_client = WhatsAppClient()


async def process_and_reply_whatsapp(message: IncomingWhatsAppMessage) -> None:
    """Tarefa em background para processar a mensagem no AssistantService e enviar retorno."""
    async with async_session_factory() as session:
        service = AssistantService(session=session)
        reply_text = await service.process_message(
            phone_number=message.sender_phone,
            raw_text=message.text,
            idempotency_key=message.message_id,
            user_name=message.sender_name,
        )
        # Despacha a resposta de volta ao WhatsApp
        await whatsapp_client.send_text_message(
            recipient_phone=message.sender_phone,
            text=reply_text,
        )


@asynccontextmanager
async def lifespan(app: FastAPI):
    """Ciclo de vida da aplicação: inicializa e encerra conexões."""
    logger.info("Iniciando %s e preparando banco de dados...", settings.PROJECT_NAME)
    await init_db()
    yield
    logger.info("Encerrando conexões de banco de dados...")
    await close_db()


app = FastAPI(
    title=settings.PROJECT_NAME,
    description="Backend do Assistente Pessoal de Agenda via WhatsApp com IA e Validação Determinística",
    version="0.1.0",
    lifespan=lifespan,
)


@app.get("/", tags=["Health"])
async def root():
    """Endpoint de verificação de integridade e metadados."""
    return {
        "project": settings.PROJECT_NAME,
        "status": "online",
        "model": settings.GEMINI_MODEL,
        "timezone": settings.DEFAULT_TIMEZONE,
        "docs_url": "/docs",
    }


@app.get("/webhook", tags=["WhatsApp"])
async def verify_webhook(
    hub_mode: Optional[str] = Query(None, alias="hub.mode"),
    hub_verify_token: Optional[str] = Query(None, alias="hub.verify_token"),
    hub_challenge: Optional[str] = Query(None, alias="hub.challenge"),
):
    """Handshake de verificação obrigatório da Meta para validar o endpoint de webhook."""
    if hub_mode == "subscribe" and hub_verify_token == settings.WHATSAPP_VERIFY_TOKEN:
        logger.info("Handshake do Webhook WhatsApp verificado com sucesso.")
        return PlainTextResponse(content=hub_challenge or "", status_code=200)

    logger.warning("Falha na validação do webhook. Token informado: %s", hub_verify_token)
    raise HTTPException(status_code=403, detail="Token de verificação incorreto.")


@app.post("/webhook", tags=["WhatsApp"])
async def receive_webhook(
    request: Request,
    background_tasks: BackgroundTasks,
):
    """Recebe eventos da Meta Cloud API, valida assinatura e despacha processamento assíncrono."""
    body_bytes = await request.body()
    signature_header = request.headers.get("X-Hub-Signature-256")

    # 1. Validação de assinatura HMAC SHA-256 se o segredo estiver configurado
    if not whatsapp_client.verify_signature(body_bytes, signature_header):
        logger.error("Assinatura X-Hub-Signature-256 inválida recebida no webhook.")
        raise HTTPException(status_code=403, detail="Assinatura inválida.")

    # 2. Parse do payload
    try:
        payload = await request.json()
    except Exception:
        payload = {}

    messages = whatsapp_client.parse_webhook_payload(payload)

    # 3. Despacho não-bloqueante para evitar timeout na Meta (que exige resposta em até 3s)
    for msg in messages:
        background_tasks.add_task(process_and_reply_whatsapp, msg)

    return {"status": "EVENT_RECEIVED"}
