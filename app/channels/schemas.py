"""Esquemas Pydantic v2 para as respostas da API FastAPI e Webhook."""

from typing import Any

from pydantic import BaseModel, Field


class HealthResponse(BaseModel):
    """Resposta estruturada do endpoint de healthcheck / status da aplicação."""
    project: str = Field(description="Nome do projeto")
    status: str = Field(description="Status de operação do serviço", examples=["online"])
    model: str = Field(description="Modelo do Gemini configurado", examples=["gemini-3.5-flash"])
    timezone: str = Field(description="Fuso horário padrão", examples=["America/Sao_Paulo"])
    docs_url: str = Field(description="Caminho relativo para documentação Swagger OpenAPI", examples=["/docs"])


class WebhookResponse(BaseModel):
    """Resposta de confirmação de recepção de eventos do webhook da Meta WhatsApp Cloud API."""
    status: str = Field(default="EVENT_RECEIVED", description="Status de recepção do evento")


class ErrorDetail(BaseModel):
    """Detalhes de erro padronizados para respostas RFC-7807 da API."""
    code: str = Field(description="Código do erro (ex: ENTITY_NOT_FOUND, EVENT_CONFLICT)")
    message: str = Field(description="Descrição amigável do erro")
    details: Any | None = Field(default=None, description="Informações adicionais de depuração se aplicável")


class ErrorResponse(BaseModel):
    """Envelope padrão de resposta para erros de API."""
    error: ErrorDetail
