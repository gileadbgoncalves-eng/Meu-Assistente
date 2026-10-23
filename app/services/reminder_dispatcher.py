"""Serviço assíncrono de varredura e despacho de lembretes via WhatsApp."""

import logging
from datetime import datetime
from zoneinfo import ZoneInfo

from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from app.channels.whatsapp import WhatsAppClient
from app.config import settings
from app.db.models import ReminderModel
from app.db.repository import ReminderRepository
from app.db.session import async_session_factory
from app.domain.enums import ReminderStatus
from app.domain.formatters import DataSanitizer

logger = logging.getLogger("meu_assistente.reminder_dispatcher")


class ReminderDispatcherService:
    """Orquestrador responsável por consultar lembretes pendentes e enviá-los via WhatsApp."""

    def __init__(
        self,
        session_factory: async_sessionmaker[AsyncSession] | None = None,
        whatsapp_client: WhatsAppClient | None = None,
    ):
        self.session_factory = session_factory or async_session_factory
        self.whatsapp_client = whatsapp_client or WhatsAppClient()

    async def dispatch_due_reminders(
        self, until_time: datetime | None = None
    ) -> int:
        """Executa a varredura e dispara todos os lembretes pendentes com horário vencido.
        Retorna o número de lembretes processados com sucesso.
        """
        user_tz = ZoneInfo(settings.DEFAULT_TIMEZONE)
        reference_time = until_time or datetime.now(user_tz)

        logger.debug("Iniciando varredura de lembretes com corte em %s", reference_time.isoformat())

        dispatched_count = 0
        async with self.session_factory() as session:
            repo = ReminderRepository(session)
            due_reminders = await repo.list_pending_due_reminders(until_time=reference_time)

            if not due_reminders:
                logger.debug("Nenhum lembrete pendente encontrado para disparo.")
                return 0

            logger.info("Encontrados %d lembrete(s) pendente(s) para disparo.", len(due_reminders))

            for reminder in due_reminders:
                success = await self._dispatch_single_reminder(reminder)
                new_status = ReminderStatus.SENT if success else ReminderStatus.FAILED

                await repo.update_status(reminder.id, new_status)
                if success:
                    dispatched_count += 1

            # Persiste imediatamente as alterações de status para garantir idempotência
            await session.commit()

        logger.info(
            "Varredura concluída: %d/%d lembrete(s) disparado(s) com sucesso.",
            dispatched_count,
            len(due_reminders),
        )
        return dispatched_count

    async def _dispatch_single_reminder(self, reminder: ReminderModel) -> bool:
        """Envia mensagem de lembrete para o WhatsApp do destinatário."""
        masked_phone = DataSanitizer.mask_phone(reminder.recipient_phone)
        logger.info(
            "Disparando lembrete #%d para o destinatário %s (send_at: %s)",
            reminder.id,
            masked_phone,
            reminder.send_at,
        )

        try:
            success = await self.whatsapp_client.send_text_message(
                recipient_phone=reminder.recipient_phone,
                text=reminder.message_text,
            )
            if success:
                logger.info("Lembrete #%d enviado com sucesso para %s.", reminder.id, masked_phone)
                return True

            logger.warning(
                "Falha ao enviar lembrete #%d para %s via WhatsApp API.",
                reminder.id,
                masked_phone,
            )
            return False
        except Exception as exc:
            logger.error(
                "Exceção ao disparar lembrete #%d para %s: %s",
                reminder.id,
                masked_phone,
                exc,
            )
            return False
