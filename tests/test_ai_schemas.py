"""Testes do esquema estruturado de interpretação e mock do Gemini."""

from datetime import UTC, datetime

import pytest

from app.ai.schemas import AIInterpretationResult
from app.domain.enums import ConfidenceLevel, IntentType


def test_ai_interpretation_schema_complete():
    """Valida serialização e desserialização completa do AIInterpretationResult."""
    data = {
        "intent": "create_event",
        "title": "Consulta Dentista",
        "start_datetime": "2026-10-22T14:00:00",
        "end_datetime": "2026-10-22T15:00:00",
        "timezone": "America/Sao_Paulo",
        "location_raw": "Rua Oscar Freire, 500",
        "participants": [
            {"name": "Dr. Marcelo", "role": "attendee"}
        ],
        "extracted_facts": ["consulta", "quinta às 14h"],
        "inferred_fields": ["duração de 1h"],
        "missing_fields": [],
        "ambiguity_questions": [],
        "proposed_changes": [],
        "confidence": "high",
    }

    result = AIInterpretationResult.model_validate(data)
    assert result.intent == IntentType.CREATE_EVENT
    assert result.title == "Consulta Dentista"
    assert len(result.participants) == 1
    assert result.participants[0].name == "Dr. Marcelo"
    assert result.confidence == ConfidenceLevel.HIGH


def test_ai_interpretation_missing_fields_ambiguity():
    """Valida extração quando faltam campos obrigatórios (ex: sem horário)."""
    data = {
        "intent": "create_event",
        "title": "Consulta Médica",
        "start_datetime": "2026-10-22",  # Apenas data, sem horário
        "missing_fields": ["horário de início"],
        "ambiguity_questions": ["Qual é o horário da consulta médica na quinta?"],
        "confidence": "medium",
        "uncertainty_reason": "Horário não especificado na mensagem",
    }

    result = AIInterpretationResult.model_validate(data)
    assert result.intent == IntentType.CREATE_EVENT
    assert len(result.missing_fields) == 1
    assert "horário de início" in result.missing_fields
    assert len(result.ambiguity_questions) == 1


def test_ai_interpretation_forwarded_message_update():
    """Valida extração para mensagem encaminhada alterando compromisso existente."""
    data = {
        "intent": "update_event",
        "event_reference": "Reunião da escola do Bernardo",
        "start_datetime": "2026-10-24T12:00:00",
        "extracted_facts": ["Reunião passou de terça para quinta-feira ao meio-dia"],
        "proposed_changes": [
            {"field": "start_time", "old_value": "terça", "new_value": "2026-10-24T12:00:00"}
        ],
        "confidence": "high",
    }

    result = AIInterpretationResult.model_validate(data)
    assert result.intent == IntentType.UPDATE_EVENT
    assert result.event_reference == "Reunião da escola do Bernardo"
    assert len(result.proposed_changes) == 1
    assert result.proposed_changes[0].field == "start_time"


def test_ai_interpretation_participant_replacement():
    """Valida extração para substituição de participantes."""
    data = {
        "intent": "replace_participant",
        "title": "Reunião de Equipe",
        "participants": [
            {"name": "Caio", "role": "attendee", "replaces_name": "João"}
        ],
        "extracted_facts": ["João não vai", "Caio irá no lugar"],
        "confidence": "high",
    }

    result = AIInterpretationResult.model_validate(data)
    assert result.intent == IntentType.REPLACE_PARTICIPANT
    assert len(result.participants) == 1
    assert result.participants[0].name == "Caio"
    assert result.participants[0].replaces_name == "João"


@pytest.mark.asyncio
async def test_mock_gemini_interpreter(mock_interpreter):
    """Testa integração com a fixture de mock do interpretador."""
    predefined = AIInterpretationResult(
        intent=IntentType.CREATE_EVENT,
        title="Almoço com Cliente",
        start_datetime="2026-10-23T12:30:00",
        confidence=ConfidenceLevel.HIGH,
    )
    mock_interpreter.set_response(predefined)

    anchor = datetime(2026, 10, 21, 10, 0, tzinfo=UTC)
    result = await mock_interpreter.interpret(
        raw_message="Almoço com cliente sexta às 12h30",
        anchor_datetime=anchor,
    )

    assert result.intent == IntentType.CREATE_EVENT
    assert result.title == "Almoço com Cliente"
    assert len(mock_interpreter.call_history) == 1
    assert mock_interpreter.call_history[0]["raw_message"] == "Almoço com cliente sexta às 12h30"
