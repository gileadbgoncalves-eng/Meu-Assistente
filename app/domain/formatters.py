"""Utilitários de formatação de data/hora no padrão brasileiro e sanitização de dados sensíveis."""

from datetime import datetime
from typing import ClassVar


class BrazilianDateTimeFormatter:
    """Formatador de datas, horários e intervalos segundo os padrões e convenções brasileiras."""

    DIAS_SEMANA: ClassVar[dict[int, str]] = {
        0: "Segunda-feira",
        1: "Terça-feira",
        2: "Quarta-feira",
        3: "Quinta-feira",
        4: "Sexta-feira",
        5: "Sábado",
        6: "Domingo",
    }

    @classmethod
    def format_date(cls, dt: datetime) -> str:
        """Formata data no padrão brasileiro: ex 'Quinta-feira, 22/10/2026'."""
        dia_nome = cls.DIAS_SEMANA.get(dt.weekday(), "")
        return f"{dia_nome}, {dt.strftime('%d/%m/%Y')}"

    @classmethod
    def format_time(cls, dt: datetime) -> str:
        """Formata horário: '14h' ou '14h30'."""
        if dt.minute == 0:
            return dt.strftime("%Hh")
        return dt.strftime("%Hh%M")

    @classmethod
    def format_offset(cls, minutes: int) -> str:
        """Formata intervalo de antecedência: ex '1 dia', '2 dias', '1h', '2h', '15 minutos'."""
        if minutes >= 1440 and minutes % 1440 == 0:
            days = minutes // 1440
            return f"{days} dia{'s' if days > 1 else ''}"
        if minutes >= 60 and minutes % 60 == 0:
            hours = minutes // 60
            return f"{hours}h"
        return f"{minutes} minutos"

    @classmethod
    def format_range(cls, start_dt: datetime, end_dt: datetime) -> str:
        """Formata intervalo completo: ex 'Quinta-feira, 22/10/2026, das 14h às 15h'."""
        data_str = cls.format_date(start_dt)
        inicio_str = cls.format_time(start_dt)
        fim_str = cls.format_time(end_dt)
        return f"{data_str}, das {inicio_str} às {fim_str}"


class DataSanitizer:
    """Sanitização e mascaramento de dados sensíveis (PII, tokens, chaves) para logs de observabilidade."""

    @staticmethod
    def mask_phone(phone: str | None) -> str:
        """Mascara o telefone para proteção de dados pessoais (LGPD/GDPR).
        Exemplo: '5511999990001' -> '5511*****0001'.
        """
        if not phone:
            return "N/A"
        clean = "".join(filter(str.isalnum, phone))
        if len(clean) <= 6:
            return "***"
        return f"{clean[:4]}*****{clean[-4:]}"

    @staticmethod
    def mask_secret(secret: str | None, show_prefix: int = 4, show_suffix: int = 4) -> str:
        """Mascara chaves de API e tokens para prevenção de vazamentos em logs."""
        if not secret:
            return "N/A"
        if len(secret) <= (show_prefix + show_suffix):
            return "***"
        return f"{secret[:show_prefix]}...{secret[-show_suffix:]}"


class ExecutiveBriefingFormatter:
    """Formatador de briefing executivo diário e resumo estruturado da agenda."""

    @classmethod
    def format_briefing(
        cls,
        user_name: str,
        target_date: datetime,
        events: list,
    ) -> str:
        """Gera texto amigável e profissional com o briefing diário."""
        date_str = BrazilianDateTimeFormatter.format_date(target_date)
        if not events:
            return f"☀️ *Bom dia, {user_name}!* 🗓️ *{date_str}*\n\n🎉 Você não tem compromissos agendados para este dia. Sua agenda está 100% livre!"

        count = len(events)
        header = f"☀️ *Briefing Executivo do Dia* | {user_name}\n📅 *{date_str}* — {count} compromisso{'s' if count > 1 else ''}:\n"
        lines = [header]

        for i, ev in enumerate(events, 1):
            h_start = BrazilianDateTimeFormatter.format_time(ev.start_time)
            h_end = BrazilianDateTimeFormatter.format_time(ev.end_time)
            loc = getattr(ev, "location_name", None) or (
                ev.location.raw_address if getattr(ev, "location", None) and hasattr(ev.location, "raw_address") else None
            )
            loc_str = f" 📍 {loc}" if loc else ""
            travel = getattr(ev, "travel_time_minutes", None) or getattr(ev, "estimated_travel_minutes", None)
            travel_str = f" [🚗 ~{travel} min]" if travel else ""
            lines.append(f"{i}. *{h_start} - {h_end}*: {ev.title}{loc_str}{travel_str}")

        lines.append("\n💡 Tenha um excelente dia!")
        return "\n".join(lines)

