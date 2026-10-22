"""Testes determinísticos para validação de datas, fusos horários e detecção de conflitos."""

from datetime import datetime, timedelta
from zoneinfo import ZoneInfo

from app.ai.schemas import AIInterpretationResult
from app.db.models import EventModel
from app.domain.enums import IntentType
from app.engine.conflict import ConflictDetector, ConflictSeverity
from app.engine.validator import DeterministicValidator


def test_validator_with_zoneinfo_sao_paulo():
    """Valida conversão e preservação correta de fuso horário com zoneinfo."""
    dt_str = "2026-10-22T14:30:00"
    parsed = DeterministicValidator.parse_datetime_with_timezone(dt_str, "America/Sao_Paulo")

    assert parsed is not None
    assert parsed.year == 2026
    assert parsed.month == 10
    assert parsed.day == 22
    assert parsed.hour == 14
    assert parsed.minute == 30
    assert str(parsed.tzinfo) == "America/Sao_Paulo"


def test_validator_infers_default_duration():
    """Valida que se o horário de término não for informado, infere 60 minutos."""
    interpretation = AIInterpretationResult(
        intent=IntentType.CREATE_EVENT,
        title="Alinhamento",
        start_datetime="2026-10-22T10:00:00",
        end_datetime=None,  # Faltando término
    )

    validated = DeterministicValidator.validate_interpretation(interpretation, "America/Sao_Paulo")
    assert validated.is_valid is True
    assert validated.duration_inferred is True
    assert validated.start_time is not None
    assert validated.end_time is not None
    assert validated.end_time - validated.start_time == timedelta(minutes=60)


def test_validator_flags_missing_fields_needs_clarification():
    """Valida que campos faltantes geram pedido de esclarecimento determinístico."""
    interpretation = AIInterpretationResult(
        intent=IntentType.CREATE_EVENT,
        title="Médico",
        start_datetime=None,
        missing_fields=["horário de início"],
        ambiguity_questions=["Qual é o horário da consulta?"],
    )

    validated = DeterministicValidator.validate_interpretation(interpretation)
    assert validated.is_valid is False
    assert validated.needs_clarification is True
    assert len(validated.clarification_questions) == 1
    assert "Qual é o horário" in validated.clarification_questions[0]


def test_validator_rejects_end_before_start():
    """Valida rejeição de horário de término anterior ou igual ao início."""
    interpretation = AIInterpretationResult(
        intent=IntentType.CREATE_EVENT,
        title="Reunião Invertida",
        start_datetime="2026-10-22T15:00:00",
        end_datetime="2026-10-22T14:00:00",
    )

    validated = DeterministicValidator.validate_interpretation(interpretation)
    assert validated.is_valid is False
    assert validated.needs_clarification is True
    assert any("posterior" in err for err in validated.errors)


def test_conflict_detector_overlap_and_adjacent():
    """Valida detecção precisa de intervalos [início, fim) e não-conflito em adjacência."""
    tz = ZoneInfo("America/Sao_Paulo")
    t14 = datetime(2026, 10, 22, 14, 0, tzinfo=tz)
    t15 = datetime(2026, 10, 22, 15, 0, tzinfo=tz)
    t16 = datetime(2026, 10, 22, 16, 0, tzinfo=tz)

    existing_event = EventModel(
        id=1,
        owner_user_id=1,
        title="Reunião com Diretoria",
        start_time=t14,
        end_time=t15,
        status="confirmed",
    )

    # 1. Colisão direta parcial (14:30 às 15:30)
    rep_overlap = ConflictDetector.analyze_conflicts(
        candidate_start=datetime(2026, 10, 22, 14, 30, tzinfo=tz),
        candidate_end=datetime(2026, 10, 22, 15, 30, tzinfo=tz),
        existing_events=[existing_event],
    )
    assert rep_overlap.has_conflict is True
    assert rep_overlap.severity == ConflictSeverity.DIRECT_OVERLAP
    assert "Aviso de conflito" in rep_overlap.warning_message
    assert "14h às 15h" in rep_overlap.warning_message

    # 2. Adjacente logo depois (15:00 às 16:00) -> NÃO é conflito
    rep_adjacent = ConflictDetector.analyze_conflicts(
        candidate_start=t15,
        candidate_end=t16,
        existing_events=[existing_event],
    )
    assert rep_adjacent.has_conflict is False
    assert rep_adjacent.severity == ConflictSeverity.NONE
