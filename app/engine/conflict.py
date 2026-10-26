"""Detector determinístico de conflitos e sobreposição de horários."""

from collections.abc import Sequence
from datetime import datetime
from enum import StrEnum

from pydantic import BaseModel, Field

from app.db.models import EventModel


class ConflictSeverity(StrEnum):
    """Gravidade do conflito identificado."""
    NONE = "none"
    ADJACENT = "adjacent"          # Evento colado (sem intervalo de descanso/deslocamento)
    DIRECT_OVERLAP = "overlap"     # Conflito real confirmado (horários colidem)
    LOGISTIC_RISK = "logistic_risk"  # Risco de atraso por deslocamento/trânsito


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
    def normalize_tz(cls, dt: datetime, default_tz_name: str = "America/Sao_Paulo") -> datetime:
        """Garante que o datetime possua fuso horário associado."""
        if dt.tzinfo is None:
            from zoneinfo import ZoneInfo
            return dt.replace(tzinfo=ZoneInfo(default_tz_name))
        return dt

    @classmethod
    def check_overlap(
        cls,
        start_a: datetime,
        end_a: datetime,
        start_b: datetime,
        end_b: datetime,
    ) -> bool:
        """Verifica se dois intervalos semiabertos [start, end) colidem matematicamente."""
        sa = cls.normalize_tz(start_a)
        ea = cls.normalize_tz(end_a)
        sb = cls.normalize_tz(start_b)
        eb = cls.normalize_tz(end_b)
        return (sa < eb) and (ea > sb)

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

    @classmethod
    def check_travel_feasibility(
        cls,
        candidate_start: datetime,
        candidate_end: datetime,
        candidate_location: str | None,
        existing_events: Sequence[EventModel],
        travel_minutes: int = 0,
    ) -> ConflictReport | None:
        """Verifica se há tempo hábil de deslocamento entre o novo evento e os eventos existentes."""
        if not candidate_location or travel_minutes <= 0:
            return None

        cand_start = cls.normalize_tz(candidate_start)
        cand_end = cls.normalize_tz(candidate_end)

        for event in existing_events:
            if getattr(event, "status", None) == "cancelled":
                continue

            ev_start = cls.normalize_tz(event.start_time)
            ev_end = cls.normalize_tz(event.end_time)

            # Caso 1: Evento anterior termina antes do novo começar
            if ev_end <= cand_start:
                gap_minutes = int((cand_start - ev_end).total_seconds() / 60)
                if gap_minutes < travel_minutes:
                    warn = (
                        f"⚠️ Risco de atraso por deslocamento: seu compromisso anterior '{event.title}' termina às "
                        f"{cls.format_time_br(ev_end)} e o novo começa às {cls.format_time_br(cand_start)} "
                        f"(intervalo de {gap_minutes} min, mas o trânsito estimado é de ~{travel_minutes} min)."
                    )
                    return ConflictReport(
                        has_conflict=True,
                        severity=ConflictSeverity.LOGISTIC_RISK,
                        conflicting_events=[{"id": event.id, "title": event.title, "travel_minutes": travel_minutes}],
                        warning_message=warn,
                    )

            # Caso 2: Evento seguinte começa depois que o novo termina
            elif cand_end <= ev_start:
                gap_minutes = int((ev_start - cand_end).total_seconds() / 60)
                if gap_minutes < travel_minutes:
                    warn = (
                        f"⚠️ Risco de atraso por deslocamento: o novo compromisso termina às {cls.format_time_br(cand_end)} "
                        f"e seu compromisso seguinte '{event.title}' começa às {cls.format_time_br(ev_start)} "
                        f"(intervalo de {gap_minutes} min, mas o trânsito estimado é de ~{travel_minutes} min)."
                    )
                    return ConflictReport(
                        has_conflict=True,
                        severity=ConflictSeverity.LOGISTIC_RISK,
                        conflicting_events=[{"id": event.id, "title": event.title, "travel_minutes": travel_minutes}],
                        warning_message=warn,
                    )

        return None
