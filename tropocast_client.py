import logging
import os
from typing import Optional

import requests

logger = logging.getLogger(__name__)

# urllib3 loguea detalles de cada request a nivel DEBUG; lo subimos a WARNING
# para no exponer headers de auth en el log (mismo criterio que firms_client.py).
logging.getLogger("urllib3").setLevel(logging.WARNING)

TROPOCAST_BASE_URL = "https://tropocast.eu/api/v1"
TROPOCAST_TIMEOUT_SECONDS = 30  # la consulta real tarda ~6s, dejamos margen holgado


class TropocastError(Exception):
    """Error genérico de la API: status code + cuerpo de la respuesta, si lo hay."""

    def __init__(self, status_code: int, body: str = ""):
        self.status_code = status_code
        self.body = body
        super().__init__(f"Tropocast HTTP {status_code}: {body[:200]}")


class TropocastRateLimited(TropocastError):
    def __init__(self, retry_after: Optional[int], body: str = ""):
        self.retry_after = retry_after
        super().__init__(429, body)


class TropocastUnauthorized(TropocastError):
    def __init__(self, body: str = ""):
        super().__init__(401, body)


class TropocastClient:
    def __init__(
        self,
        api_key: Optional[str] = None,
        base_url: str = TROPOCAST_BASE_URL,
        timeout_seconds: int = TROPOCAST_TIMEOUT_SECONDS,
        session=None,
    ):
        self.api_key = api_key if api_key is not None else os.getenv("TROPOCAST_API_KEY", "").strip()
        self.base_url = base_url
        self.timeout_seconds = timeout_seconds
        self.session = session or requests

    @property
    def enabled(self) -> bool:
        return bool(self.api_key)

    def _get(self, path: str, params: dict, auth: bool) -> dict:
        headers = {"X-Tropocast-Key": self.api_key} if auth else {}
        response = self.session.get(
            f"{self.base_url}{path}", params=params, headers=headers, timeout=self.timeout_seconds
        )
        if response.status_code == 429:
            retry_after_raw = response.headers.get("Retry-After")
            retry_after = int(retry_after_raw) if retry_after_raw and retry_after_raw.isdigit() else None
            raise TropocastRateLimited(retry_after, response.text)
        if response.status_code == 401:
            raise TropocastUnauthorized(response.text)
        if response.status_code >= 400:
            raise TropocastError(response.status_code, response.text)
        return response.json()

    def fetch_forecast(
        self,
        lat: float,
        lon: float,
        radius_km: float,
        source: str = "gdps",
        period: str = "next_72h",
        step_hours: int = 3,
        language: str = "es",
        timezone: str = "America/Argentina/Buenos_Aires",
        threshold: int = 65,
    ) -> dict:
        """GET /device/text-forecast. Devuelve el JSON completo tal cual lo
        manda Tropocast (ver tropocast_service.py para el parseo/clasificación).
        No reintenta en caliente: ante 429/503, el llamador debe esperar al
        próximo ciclo del scheduler, no insistir en el momento."""
        if not self.enabled:
            raise TropocastUnauthorized("TROPOCAST_API_KEY no configurada")
        params = {
            "lat": lat, "lon": lon, "radius_km": radius_km,
            "source": source, "period": period, "step_hours": step_hours,
            "language": language, "timezone": timezone, "threshold": threshold,
        }
        return self._get("/device/text-forecast", params, auth=True)

    def fetch_metadata(self, source: Optional[str] = None, area: Optional[str] = None) -> dict:
        """GET /tropo/metadata: liviano, sin autenticación. Se usa para
        chequear barato si hay una corrida de modelo nueva (campo
        publication.published_run) antes de gastar una consulta completa de
        varios segundos. Si el llamador no puede confiar en este chequeo
        para una fuente/área en particular, debe seguir funcionando igual
        consultando fetch_forecast directamente cada ciclo."""
        params = {}
        if source:
            params["source"] = source
        if area:
            params["area"] = area
        return self._get("/tropo/metadata", params, auth=False)
