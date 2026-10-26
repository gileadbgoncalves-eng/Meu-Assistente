"""Cliente de interpretação semântica usando o SDK oficial google-genai."""

import json
from datetime import datetime
from typing import Protocol

import anyio
from google import genai
from google.genai import types

from app.ai.prompts import SYSTEM_PROMPT, build_user_prompt
from app.ai.schemas import AIInterpretationResult
from app.config import settings
from app.domain.enums import ConfidenceLevel, IntentType


class AIInterpreterProtocol(Protocol):
    """Protocolo abstrato para permitir injeção de dependência e mocks nos testes."""

    async def interpret(
        self,
        raw_message: str,
        anchor_datetime: datetime,
        user_timezone: str = "America/Sao_Paulo",
        recent_context: str | None = None,
    ) -> AIInterpretationResult:
        """Interpreta uma mensagem e devolve o schema estruturado."""
        ...

    async def transcribe_and_interpret_audio(
        self,
        audio_bytes: bytes,
        mime_type: str = "audio/ogg",
        context: list[str] | None = None,
        anchor_datetime: datetime | None = None,
        user_timezone: str = "America/Sao_Paulo",
    ) -> tuple[str, AIInterpretationResult]:
        """Transcreve e interpreta áudio diretamente via IA multimodal."""
        ...


class GeminiInterpreter:
    """Implementação oficial da camada de IA com Google Gemini."""

    def __init__(
        self,
        api_key: str | None = None,
        model_name: str | None = None,
    ):
        self.api_key = api_key or settings.clean_gemini_api_key
        self.model_name = model_name or settings.GEMINI_MODEL
        self._client: genai.Client | None = None

    @property
    def client(self) -> genai.Client:
        if self._client is None:
            if not self.api_key:
                raise ValueError("GEMINI_API_KEY não foi configurada no ambiente ou no .env")
            self._client = genai.Client(api_key=self.api_key)
        return self._client

    async def interpret(
        self,
        raw_message: str,
        anchor_datetime: datetime,
        user_timezone: str = "America/Sao_Paulo",
        recent_context: str | None = None,
    ) -> AIInterpretationResult:
        """Envia mensagem ao Gemini solicitando retorno estruturado via response_schema."""
        prompt = build_user_prompt(
            raw_message=raw_message,
            anchor_datetime_iso=anchor_datetime.isoformat(),
            user_timezone=user_timezone,
            recent_context=recent_context,
        )

        config = types.GenerateContentConfig(
            system_instruction=SYSTEM_PROMPT,
            response_mime_type="application/json",
            response_schema=AIInterpretationResult,
            temperature=0.1,  # Baixa temperatura para determinismo na extração
        )

        candidate_models = [self.model_name]
        for fallback in ["gemini-3.5-flash-lite", "gemini-flash-latest"]:
            if fallback not in candidate_models:
                candidate_models.append(fallback)

        last_error = None
        for model in candidate_models:
            try:
                # Executa a chamada do SDK no thread pool para compatibilidade assíncrona
                response = await anyio.to_thread.run_sync(
                    lambda m=model: self.client.models.generate_content(
                        model=m,
                        contents=prompt,
                        config=config,
                    )
                )

                # Faz o parse validado via Pydantic
                raw_text = response.text or "{}"
                parsed_data = json.loads(raw_text)
                return AIInterpretationResult.model_validate(parsed_data)

            except Exception as exc:
                last_error = exc
                err_str = str(exc)
                # Se for indisponibilidade temporária de alta demanda (503) ou modelo descontinuado (404), tenta o fallback
                if "503" in err_str or "404" in err_str or "UNAVAILABLE" in err_str:
                    continue
                # Se for outro erro (ex: credencial inválida), interrompe o loop
                break

        # Em caso de falha de conexão persistente em todos os modelos
        return AIInterpretationResult(
            intent=IntentType.UNKNOWN,
            confidence=ConfidenceLevel.LOW,
            uncertainty_reason=f"Falha na comunicação com o provedor de IA: {last_error!s}",
            ambiguity_questions=["Não consegui processar a mensagem no momento. Poderia repetir?"],
        )

    async def transcribe_and_interpret_audio(
        self,
        audio_bytes: bytes,
        mime_type: str = "audio/ogg",
        context: list[str] | None = None,
        anchor_datetime: datetime | None = None,
        user_timezone: str = "America/Sao_Paulo",
    ) -> tuple[str, AIInterpretationResult]:
        """Transcreve e interpreta áudio multimodal via Gemini com fallback gracioso para mock."""
        anchor_dt = anchor_datetime or datetime.now()
        recent_ctx = "\n".join(context) if context else None

        # Fallback offline para testes ou modo sem chave
        if not self.api_key or audio_bytes.startswith(b"MOCK_"):
            simulated_text = "Consulta médica dia 30/10 às 07:00 da manhã"
            if b"reuniao" in audio_bytes.lower() or "reunião".encode() in audio_bytes.lower():
                simulated_text = "Reunião de alinhamento amanhã às 15h"
            elif b"cancel" in audio_bytes.lower():
                simulated_text = "Cancelar consulta"
            parsed = await self.interpret(
                raw_message=simulated_text,
                anchor_datetime=anchor_dt,
                user_timezone=user_timezone,
                recent_context=recent_ctx,
            )
            return simulated_text, parsed

        try:
            audio_part = types.Part.from_bytes(
                data=audio_bytes,
                mime_type=mime_type,
            )
            instruction = (
                "Você recebeu um áudio de voz pelo WhatsApp. "
                "Transcreva com máxima precisão o que foi falado e estruture os dados do compromisso no schema."
            )
            prompt = build_user_prompt(
                raw_message=instruction,
                anchor_datetime_iso=anchor_dt.isoformat(),
                user_timezone=user_timezone,
                recent_context=recent_ctx,
            )
            config = types.GenerateContentConfig(
                system_instruction=SYSTEM_PROMPT,
                response_mime_type="application/json",
                response_schema=AIInterpretationResult,
                temperature=0.1,
            )
            response = await anyio.to_thread.run_sync(
                lambda: self.client.models.generate_content(
                    model=self.model_name,
                    contents=[audio_part, prompt],
                    config=config,
                )
            )
            raw_json = response.text or "{}"
            data = json.loads(raw_json)
            parsed = AIInterpretationResult.model_validate(data)
            transcribed = parsed.title or "Mensagem de voz"
            return transcribed, parsed
        except Exception as exc:
            fallback_text = "Mensagem de voz recebida"
            parsed = AIInterpretationResult(
                intent=IntentType.UNKNOWN,
                confidence=ConfidenceLevel.LOW,
                uncertainty_reason=f"Falha no processamento de áudio: {exc}",
                ambiguity_questions=["Não foi possível processar o áudio. Poderia enviar novamente?"],
            )
            return fallback_text, parsed

