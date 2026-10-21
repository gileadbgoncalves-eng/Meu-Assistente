"""Adaptador para cálculo de rotas e tempo de deslocamento via Google Maps Platform."""

import logging
from datetime import datetime
from typing import Optional
import httpx

from app.config import settings
from app.domain.interfaces import TravelTimeEstimatorPort, TravelEstimate

logger = logging.getLogger(__name__)


class GoogleMapsAdapter(TravelTimeEstimatorPort):
    """Adaptador que implementa TravelTimeEstimatorPort usando a Distance Matrix API do Google Maps."""

    def __init__(self, api_key: Optional[str] = None):
        self.api_key = api_key or settings.GOOGLE_MAPS_API_KEY

    async def estimate_travel_time(
        self,
        origin: str,
        destination: str,
        departure_time: datetime,
        mode: str = "driving",
    ) -> TravelEstimate:
        """Calcula a estimativa de viagem e trânsito com suporte a fallback gracioso."""
        if not self.api_key:
            # Fallback aproximado se a chave não estiver configurada
            logger.info("Chave do Google Maps não configurada. Usando estimativa aproximada de trânsito.")
            return TravelEstimate(
                origin_address=origin,
                destination_address=destination,
                departure_time=departure_time,
                mode=mode,
                duration_minutes=35,
                distance_meters=12000,
                provider="estimativa_aproximada_local",
                is_approximate=True,
                warning="Chave da Google Maps Platform não configurada no ambiente. Valor baseado em estimativa média urbana.",
            )

        # Chamada à Google Maps Distance Matrix API
        url = "https://maps.googleapis.com/maps/api/distancematrix/json"
        departure_timestamp = int(departure_time.timestamp())
        params = {
            "origins": origin,
            "destinations": destination,
            "mode": mode,
            "departure_time": departure_timestamp,
            "key": self.api_key,
            "language": "pt-BR",
        }

        try:
            async with httpx.AsyncClient(timeout=8.0) as client:
                response = await client.get(url, params=params)
                if response.status_code == 200:
                    data = response.json()
                    rows = data.get("rows", [])
                    if rows and rows[0].get("elements"):
                        elem = rows[0]["elements"][0]
                        if elem.get("status") == "OK":
                            # Prioriza duração com trânsito em tempo real se disponível
                            dur_info = elem.get("duration_in_traffic") or elem.get("duration", {})
                            dur_seconds = dur_info.get("value", 1800)
                            dist_meters = elem.get("distance", {}).get("value")

                            return TravelEstimate(
                                origin_address=origin,
                                destination_address=destination,
                                departure_time=departure_time,
                                mode=mode,
                                duration_minutes=max(1, dur_seconds // 60),
                                distance_meters=dist_meters,
                                provider="google_maps_platform",
                                is_approximate=False,
                            )

            logger.warning("Google Maps Distance Matrix retornou erro ou status diferente de OK.")
        except Exception as exc:
            logger.error("Falha ao comunicar com a Google Maps API: %s", exc)

        # Retorno de contingência
        return TravelEstimate(
            origin_address=origin,
            destination_address=destination,
            departure_time=departure_time,
            mode=mode,
            duration_minutes=30,
            provider="fallback_contingencia",
            is_approximate=True,
            warning="Não foi possível consultar os dados de trânsito em tempo real no momento.",
        )
