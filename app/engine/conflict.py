"""Detector determinístico de conflitos e sobreposição de horários."""

from datetime import datetime
from enum import Enum
from typing import Sequence
from pydantic import BaseModel, Field

from app.db.models import EventModel


class ConflictSeverity(str, Enum):
    """Gravidade do conflito identificado."""
    NONE = "none"
    ADJACENT = "adjacent"          # Evento colado (sem intervalo de descanso/deslocamento)
    DIRECT_OVERLAP = "overlap"     # Conflito real confirmado (horários colidem)


class ConflictReport(BaseModel):
    """Relatório detalhado de análise de conflitos."""
    has_conflict: bool
    severity: ConflictSeverity
    conflicting_events: list[dict] = Field(default_factory=list)
    warning_message: str | None = None


class ConflictDetector:
    """Motor de cálculo de colisão de intervalos temporais semiabertos [início, fim)."""

    @staticmethod
    def format_time_br(dt: datetime) -> str:
        """Formata horário no padrão brasileiro: 14h ou 14h30."""
        if dt.minute == 0:
            return dt.strftime("%Hh")
        return dt.strftime("%Hh%M")

    @classmethod
    def check_overlap(
        cls,
        start_a: datetime,
        end_a: datetime,
        start_b: datetime,
        end_b: datetime,
    ) -> bool:
        """Verifica se dois intervalos semiabertos [start, end) colidem matematicamente."""
        return (start_a < end_b) and (end_a > start_b)

    @classmethod
    def analyze_conflicts(
        cls,
        candidate_start: datetime,
        candidate_end: datetime,
        existing_events: Sequence[EventModel],
        min_buffer_minutes: int = 0,
    ) -> ConflictReport:
        """Analisa candidatos contra a lista de eventos existentes no banco."""
        conflicting_list = []

        for event in existing_events:
            # Não compara se estiver cancelado
            if event.status == "cancelled":
                continue

            # Verifica sobreposição direta
            is_overlapping = cls.check_overlap(
                candidate_start,
                candidate_end,
                event.start_time,
                event.end_time,
            )

            if is_overlapping:
                conflicting_list.append({
                    "id": event.id,
                    "title": event.title,
                    "start_time": event.start_time,
                    "end_time": event.end_time,
                    "status": event.status,
                })

        if not conflicting_list:
            return ConflictReport(
                has_conflict=False,
                severity=ConflictSeverity.NONE,
                warning_message=None,
            )

        # Conflito confirmado: monta aviso amigável e claro
        first = conflicting_list[0]
        start_fmt = cls.format_time_br(first["start_time"])
        end_fmt = cls.format_time_br(first["end_time"])

        warning = (
            f"Aviso de conflito: você já tem '{first['title']}' marcado das {start_fmt} às {end_fmt} "
            f"nesse mesmo período. Deseja manter mesmo com a sobreposição ou prefere outro horário?"
        )

        return ConflictReport(
            has_conflict=True,
            severity=ConflictSeverity.DIRECT_OVERLAP,
            conflicting_events=conflicting_list,
            warning_message=warning,
        )
