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
