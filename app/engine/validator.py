"""Validador determinístico de datas, fusos horários e integridade de propostas."""

from datetime import datetime, timedelta
from typing import Optional
from zoneinfo import ZoneInfo
from pydantic import BaseModel, Field

from app.ai.schemas import AIInterpretationResult
from app.domain.enums import IntentType


class ValidatedProposal(BaseModel):
    """Proposta de evento validada pelo motor determinístico."""
    is_valid: bool
    intent: IntentType
    title: Optional[str] = None
    start_time: Optional[datetime] = None
    end_time: Optional[datetime] = None
    timezone_name: str = "America/Sao_Paulo"
    location_address: Optional[str] = None
    participants: list[dict] = Field(default_factory=list)
    event_reference: Optional[str] = None
    duration_inferred: bool = False
    needs_clarification: bool = False
    clarification_questions: list[str] = Field(default_factory=list)
    errors: list[str] = Field(default_factory=list)

    # Campos do subsistema de lembretes
    reminder_minutes_before: Optional[int] = None
    reminder_trigger_datetime: Optional[datetime] = None
    reminder_message: Optional[str] = None



class DeterministicValidator:
    """Validador estrito de datas, fusos e regras lógicas de agendamento."""

    DEFAULT_EVENT_DURATION_MINUTES = 60

    @classmethod
    def parse_datetime_with_timezone(
        cls,
        dt_str: Optional[str],
        tz_name: str = "America/Sao_Paulo",
    ) -> Optional[datetime]:
        """Converte string ISO para datetime ciente do fuso horário especificado via zoneinfo."""
        if not dt_str:
            return None

        try:
            tz = ZoneInfo(tz_name)
        except Exception:
            tz = ZoneInfo("America/Sao_Paulo")

        try:
            dt = datetime.fromisoformat(dt_str)
            if dt.tzinfo is None:
                return dt.replace(tzinfo=tz)
            return dt.astimezone(tz)
        except (ValueError, TypeError):
            return None

    @classmethod
    def validate_interpretation(
        cls,
        interpretation: AIInterpretationResult,
        user_timezone: str = "America/Sao_Paulo",
    ) -> ValidatedProposal:
        """Valida determinística e matematicamente o resultado retornado pela IA."""
        errors: list[str] = []
        clarification_questions: list[str] = list(interpretation.ambiguity_questions)
        duration_inferred = False

        # Se a IA já apontou dados faltantes ou ambiguidades críticas
        if interpretation.missing_fields:
            return ValidatedProposal(
                is_valid=False,
                intent=interpretation.intent,
                title=interpretation.title,
                timezone_name=user_timezone,
                location_address=interpretation.location_raw,
                needs_clarification=True,
                clarification_questions=clarification_questions,
                errors=[f"Campos ausentes: {', '.join(interpretation.missing_fields)}"],
            )

        # Para intenções que não criam nem alteram horários (ex: consulta)
        if interpretation.intent == IntentType.QUERY_EVENTS:
            start_dt = cls.parse_datetime_with_timezone(interpretation.start_datetime, user_timezone)
            end_dt = cls.parse_datetime_with_timezone(interpretation.end_datetime, user_timezone)
            return ValidatedProposal(
                is_valid=True,
                intent=interpretation.intent,
                title=interpretation.title,
                start_time=start_dt,
                end_time=end_dt,
                timezone_name=user_timezone,
            )

        # Para criação de eventos (CREATE_EVENT)
        if interpretation.intent == IntentType.CREATE_EVENT:
            start_dt = cls.parse_datetime_with_timezone(interpretation.start_datetime, user_timezone)
            end_dt = cls.parse_datetime_with_timezone(interpretation.end_datetime, user_timezone)

            if not start_dt:
                clarification_questions.append("Qual é a data e o horário do compromisso?")
                return ValidatedProposal(
                    is_valid=False,
                    intent=interpretation.intent,
                    title=interpretation.title,
                    needs_clarification=True,
                    clarification_questions=clarification_questions,
                    errors=["Horário de início não informado."],
                )

            # Se informou início mas não término, calcula duração padrão
            if not end_dt:
                end_dt = start_dt + timedelta(minutes=cls.DEFAULT_EVENT_DURATION_MINUTES)
                duration_inferred = True

            # Validação matemática: término DEVE ser posterior ao início
            if end_dt <= start_dt:
                errors.append("O horário de término deve ser posterior ao horário de início.")
                return ValidatedProposal(
                    is_valid=False,
                    intent=interpretation.intent,
                    title=interpretation.title,
                    start_time=start_dt,
                    end_time=end_dt,
                    needs_clarification=True,
                    clarification_questions=["O horário de término informado é anterior ao início. Pode verificar?"],
                    errors=errors,
                )

            # Normalização de participantes
            participants_data = [
                {
                    "name": p.name,
                    "role": p.role,
                    "phone_number": p.phone_number,
                    "replaces_name": p.replaces_name,
                }
                for p in interpretation.participants
            ]

            # Tratamento de lembrete vinculado ao compromisso
            reminder_trigger = cls.parse_datetime_with_timezone(
                interpretation.reminder_trigger_datetime, user_timezone
            )

            return ValidatedProposal(
                is_valid=True,
                intent=interpretation.intent,
                title=interpretation.title or "Novo Compromisso",
                start_time=start_dt,
                end_time=end_dt,
                timezone_name=user_timezone,
                location_address=interpretation.location_raw,
                participants=participants_data,
                duration_inferred=duration_inferred,
                clarification_questions=clarification_questions,
                reminder_minutes_before=interpretation.reminder_minutes_before,
                reminder_trigger_datetime=reminder_trigger,
                reminder_message=interpretation.reminder_message,
            )

        # Para solicitação de lembretes (avulsos ou relativos a evento existente)
        if interpretation.intent == IntentType.REMINDER_REQUEST:
            reminder_trigger = cls.parse_datetime_with_timezone(
                interpretation.reminder_trigger_datetime or interpretation.start_datetime,
                user_timezone,
            )
            is_relative_to_event = bool(
                interpretation.reminder_minutes_before is not None
                or interpretation.event_reference
            )

            if not reminder_trigger and not is_relative_to_event:
                clarification_questions.append("Para quando você gostaria de configurar esse lembrete?")
                return ValidatedProposal(
                    is_valid=False,
                    intent=interpretation.intent,
                    title=interpretation.reminder_message or interpretation.title or "Lembrete",
                    needs_clarification=True,
                    clarification_questions=clarification_questions,
                    errors=["Horário de disparo do lembrete não informado."],
                )

            return ValidatedProposal(
                is_valid=True,
                intent=interpretation.intent,
                title=interpretation.reminder_message or interpretation.title or "Lembrete",
                event_reference=interpretation.event_reference,
                timezone_name=user_timezone,
                reminder_minutes_before=interpretation.reminder_minutes_before,
                reminder_trigger_datetime=reminder_trigger,
                reminder_message=interpretation.reminder_message or interpretation.title or "Lembrete",
            )

        # Para atualização ou cancelamento
        start_dt = cls.parse_datetime_with_timezone(interpretation.start_datetime, user_timezone)
        end_dt = cls.parse_datetime_with_timezone(interpretation.end_datetime, user_timezone)

        return ValidatedProposal(
            is_valid=True,
            intent=interpretation.intent,
            title=interpretation.title,
            start_time=start_dt,
            end_time=end_dt,
            timezone_name=user_timezone,
            location_address=interpretation.location_raw,
            event_reference=interpretation.event_reference,
            participants=[p.model_dump() for p in interpretation.participants],
            needs_clarification=bool(clarification_questions),
            clarification_questions=clarification_questions,
            reminder_minutes_before=interpretation.reminder_minutes_before,
            reminder_trigger_datetime=cls.parse_datetime_with_timezone(
                interpretation.reminder_trigger_datetime, user_timezone
            ),
            reminder_message=interpretation.reminder_message,
        )
