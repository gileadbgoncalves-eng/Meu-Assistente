"""Aplicação FastAPI e Webhook da WhatsApp Cloud API."""

import logging
from contextlib import asynccontextmanager

from apscheduler.schedulers.asyncio import AsyncIOScheduler
from fastapi import BackgroundTasks, FastAPI, HTTPException, Query, Request, status
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import JSONResponse, PlainTextResponse

from app.channels.schemas import (
    HealthResponse,
    WebhookResponse,
)
from app.channels.web_api import dashboard_router
from app.channels.web_api import router as web_api_router
from app.channels.whatsapp import IncomingWhatsAppMessage, WhatsAppClient
from app.config import settings
from app.db.session import async_session_factory, close_db, init_db
from app.domain.exceptions import (
    DomainError,
    EntityNotFoundError,
    EventConflictError,
    IdempotencyDuplicateError,
    UnauthorizedAccessError,
)
from app.domain.formatters import DataSanitizer
from app.services.assistant import AssistantService
from app.services.reminder_dispatcher import ReminderDispatcherService

logging.basicConfig(level=getattr(logging, settings.LOG_LEVEL.upper(), logging.INFO))
logger = logging.getLogger("meu_assistente.api")

whatsapp_client = WhatsAppClient()
scheduler = AsyncIOScheduler()
reminder_dispatcher = ReminderDispatcherService(whatsapp_client=whatsapp_client)


async def sweep_and_dispatch_reminders() -> None:
    """Varredura periódica de lembretes pendentes e envio via WhatsApp."""
    try:
        count = await reminder_dispatcher.dispatch_due_reminders()
        if count > 0:
            logger.info("Varredura de lembretes executada: %d lembrete(s) disparado(s).", count)
    except Exception as exc:
        logger.error("Erro durante a varredura automática de lembretes: %s", exc)


async def process_and_reply_whatsapp(message: IncomingWhatsAppMessage) -> None:
    """Tarefa em background para processar a mensagem no AssistantService e enviar retorno."""
    masked_phone = DataSanitizer.mask_phone(message.sender_phone)
    logger.info("Processando mensagem em background de %s (wamid: %s)", masked_phone, message.message_id)

    async with async_session_factory() as session:
        service = AssistantService(session=session)
        if getattr(message, "message_type", "text") == "audio" and getattr(message, "media_id", None):
            audio_bytes = await whatsapp_client.download_media(message.media_id)
            reply_text = await service.process_audio_message(
                phone_number=message.sender_phone,
                audio_bytes=audio_bytes,
                mime_type=message.mime_type or "audio/ogg",
                idempotency_key=message.message_id,
                user_name=message.sender_name,
            )
        else:
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
    """Ciclo de vida da aplicação: inicializa conexões e agendador em background."""
    logger.info("Iniciando %s e preparando banco de dados...", settings.PROJECT_NAME)
    await init_db()

    app.state.scheduler = scheduler
    app.state.reminder_dispatcher = reminder_dispatcher

    if settings.REMINDERS_ENABLED:
        scheduler.add_job(
            sweep_and_dispatch_reminders,
            trigger="interval",
            seconds=settings.REMINDER_SWEEP_INTERVAL_SECONDS,
            id="reminder_sweeper_job",
            replace_existing=True,
        )
        scheduler.start()
        logger.info(
            "Agendador de lembretes (APScheduler) iniciado (intervalo: %ds).",
            settings.REMINDER_SWEEP_INTERVAL_SECONDS,
        )

    yield

    if settings.REMINDERS_ENABLED and scheduler.running:
        logger.info("Encerrando agendador de lembretes...")
        scheduler.shutdown(wait=False)

    logger.info("Encerrando conexões de banco de dados...")
    await close_db()


TAGS_METADATA = [
    {
        "name": "Health",
        "description": "Verificação de integridade operacional, parâmetros e metadados do serviço.",
    },
    {
        "name": "WhatsApp",
        "description": "Endpoints de recepção de webhooks e validação de assinatura da Meta WhatsApp Cloud API.",
    },
]

app = FastAPI(
    title=settings.PROJECT_NAME,
    description="Backend do Assistente Pessoal de Agenda via WhatsApp com IA e Validação Determinística",
    version="0.3.0-RC",
    lifespan=lifespan,
    openapi_tags=TAGS_METADATA,
)

app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

app.include_router(web_api_router)
app.include_router(dashboard_router)


@app.exception_handler(DomainError)
async def domain_exception_handler(request: Request, exc: DomainError):
    """Mapeia exceções de domínio para respostas HTTP com códigos e schemas padronizados."""
    status_code = status.HTTP_400_BAD_REQUEST
    if isinstance(exc, EntityNotFoundError):
        status_code = status.HTTP_404_NOT_FOUND
    elif isinstance(exc, (EventConflictError, IdempotencyDuplicateError)):
        status_code = status.HTTP_409_CONFLICT
    elif isinstance(exc, UnauthorizedAccessError):
        status_code = status.HTTP_403_FORBIDDEN

    return JSONResponse(
        status_code=status_code,
        content={"error": {"code": exc.code, "message": exc.message}},
    )


@app.exception_handler(Exception)
async def unhandled_exception_handler(request: Request, exc: Exception):
    """Captura exceções inesperadas, evitando vazamento de stacktrace e formatando saída JSON."""
    logger.error("Erro interno não tratado na rota %s: %s", request.url.path, exc, exc_info=True)
    return JSONResponse(
        status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
        content={"error": {"code": "INTERNAL_SERVER_ERROR", "message": "Ocorreu um erro interno no servidor."}},
    )


@app.get("/", tags=["Health"], response_model=HealthResponse, status_code=status.HTTP_200_OK)
async def root():
    """Endpoint de verificação de integridade e metadados."""
    return HealthResponse(
        project=settings.PROJECT_NAME,
        status="online",
        model=settings.GEMINI_MODEL,
        timezone=settings.DEFAULT_TIMEZONE,
        docs_url="/docs",
    )


@app.get("/webhook", tags=["WhatsApp"])
async def verify_webhook(
    hub_mode: str | None = Query(None, alias="hub.mode"),
    hub_verify_token: str | None = Query(None, alias="hub.verify_token"),
    hub_challenge: str | None = Query(None, alias="hub.challenge"),
):
    """Handshake de verificação obrigatório da Meta para validar o endpoint de webhook."""
    if hub_mode == "subscribe" and hub_verify_token == settings.WHATSAPP_VERIFY_TOKEN:
        logger.info("Handshake do Webhook WhatsApp verificado com sucesso.")
        return PlainTextResponse(content=hub_challenge or "", status_code=status.HTTP_200_OK)

    masked_token = DataSanitizer.mask_secret(hub_verify_token)
    logger.warning("Falha na validação do webhook. Token informado: %s", masked_token)
    raise HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail="Token de verificação incorreto.")


@app.post(
    "/webhook",
    tags=["WhatsApp"],
    response_model=WebhookResponse,
    status_code=status.HTTP_200_OK,
)
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
        raise HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail="Assinatura inválida.")

    # 2. Parse do payload
    try:
        payload = await request.json()
    except Exception:
        payload = {}

    messages = whatsapp_client.parse_webhook_payload(payload)

    # 3. Despacho não-bloqueante para evitar timeout na Meta (que exige resposta em até 3s)
    for msg in messages:
        background_tasks.add_task(process_and_reply_whatsapp, msg)

    return WebhookResponse(status="EVENT_RECEIVED")
