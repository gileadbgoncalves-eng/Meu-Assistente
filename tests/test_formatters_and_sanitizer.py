"""Testes para o formatador brasileiro de datas/horários e sanitizador de PII."""

from datetime import datetime

from app.domain.formatters import BrazilianDateTimeFormatter, DataSanitizer


def test_brazilian_date_time_formatter():
    dt = datetime(2026, 10, 22, 14, 30)

    # Data
    assert BrazilianDateTimeFormatter.format_date(dt) == "Quinta-feira, 22/10/2026"

    # Horários
    assert BrazilianDateTimeFormatter.format_time(dt) == "14h30"
    assert BrazilianDateTimeFormatter.format_time(datetime(2026, 10, 22, 9, 0)) == "09h"

    # Intervalos
    end_dt = datetime(2026, 10, 22, 15, 30)
    assert (
        BrazilianDateTimeFormatter.format_range(dt, end_dt)
        == "Quinta-feira, 22/10/2026, das 14h30 às 15h30"
    )

    # Offsets de antecedência
    assert BrazilianDateTimeFormatter.format_offset(15) == "15 minutos"
    assert BrazilianDateTimeFormatter.format_offset(60) == "1h"
    assert BrazilianDateTimeFormatter.format_offset(120) == "2h"
    assert BrazilianDateTimeFormatter.format_offset(1440) == "1 dia"
    assert BrazilianDateTimeFormatter.format_offset(2880) == "2 dias"


def test_data_sanitizer_masking():
    # Telefones
    assert DataSanitizer.mask_phone("5511999990001") == "5511*****0001"
    assert DataSanitizer.mask_phone(None) == "N/A"
    assert DataSanitizer.mask_phone("123") == "***"

    # Chaves e Segredos
    assert DataSanitizer.mask_secret("AIzaSyD-1234567890-AbCdEfGh") == "AIza...EfGh"
    assert DataSanitizer.mask_secret(None) == "N/A"
    assert DataSanitizer.mask_secret("short") == "***"


def test_brazilian_formatter_all_weekdays():
    """Garante que todos os dias da semana são traduzidos corretamente em português."""
    expected_days = [
        (datetime(2026, 10, 19), "Segunda-feira"),
        (datetime(2026, 10, 20), "Terça-feira"),
        (datetime(2026, 10, 21), "Quarta-feira"),
        (datetime(2026, 10, 22), "Quinta-feira"),
        (datetime(2026, 10, 23), "Sexta-feira"),
        (datetime(2026, 10, 24), "Sábado"),
        (datetime(2026, 10, 25), "Domingo"),
    ]
    for dt, expected in expected_days:
        assert expected in BrazilianDateTimeFormatter.format_date(dt)


def test_data_sanitizer_empty_and_special_chars():
    """Valida mascaramento com caracteres não-alfanuméricos no telefone."""
    assert DataSanitizer.mask_phone("+55 (11) 99999-0001") == "5511*****0001"
    assert DataSanitizer.mask_phone("") == "N/A"

