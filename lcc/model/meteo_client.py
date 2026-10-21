"""Клиент внешней метеослужбы космодрома (ЛР4, Т4.1, Т4.4).

Сервис прогнозов зависит от абстракции :class:`MeteoProvider`, поэтому в тестах
вместо сети подставляется заглушка. Формат ответа в описании не задан, принятый
формат описан в docs/07_lab4_analysis.md (пробел П4)::

    {"valid_at": "2026-10-22T06:00",
     "levels": [{"altitude_m": 0, "wind_ms": 6.5, "temperature_c": 3.0}, ...],
     "thunderstorm": false, "cloud_base_m": 900}
"""
from __future__ import annotations

import json
import socket
import urllib.error
import urllib.request
from abc import ABC, abstractmethod
from dataclasses import dataclass
from datetime import datetime
from urllib.parse import urlencode


class MeteoUnavailable(Exception):
    """Служба не ответила, вернула ошибку или ответ неизвестного формата."""


@dataclass(frozen=True)
class Forecast:
    """Прогноз на момент стартового окна, сведенный к величинам из WeatherLimits."""
    valid_at: datetime
    ground_wind: float          # ветер на нижнем уровне
    altitude_wind: float        # максимальный ветер на высотах
    temperature: float          # температура на нижнем уровне
    thunderstorm: bool
    cloud_base: float | None = None


class MeteoProvider(ABC):
    @abstractmethod
    def fetch(self, site: str, at: datetime) -> Forecast:
        """Прогноз для площадки `site` на момент `at`. Ошибки — только :class:`MeteoUnavailable`."""


def _number(value, name: str, min_value: float | None = None) -> float:
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        raise MeteoUnavailable(f"неизвестный формат ответа: поле {name} не число")
    if min_value is not None and value < min_value:
        raise MeteoUnavailable(f"неизвестный формат ответа: {name} < {min_value:g}")
    return float(value)


def parse_forecast(data) -> Forecast:
    """Проверить ответ службы и свести уровни высот к одному прогнозу."""
    if not isinstance(data, dict):
        raise MeteoUnavailable("неизвестный формат ответа: ожидается JSON-объект")
    levels = data.get("levels")
    if not isinstance(levels, list) or not levels or not all(isinstance(lv, dict) for lv in levels):
        raise MeteoUnavailable("неизвестный формат ответа: нет уровней высот")
    parsed = sorted((_number(lv.get("altitude_m"), "altitude_m", 0), _number(lv.get("wind_ms"), "wind_ms", 0),
                     _number(lv.get("temperature_c"), "temperature_c")) for lv in levels)
    try:
        valid_at = datetime.fromisoformat(str(data.get("valid_at")))
    except ValueError:
        raise MeteoUnavailable("неизвестный формат ответа: valid_at") from None
    storm = data.get("thunderstorm")
    if not isinstance(storm, bool):
        raise MeteoUnavailable("неизвестный формат ответа: thunderstorm")
    cloud = data.get("cloud_base_m")
    ground, upper = parsed[0], parsed[1:]
    return Forecast(valid_at.replace(second=0, microsecond=0, tzinfo=None), ground[1],
                    max((lv[1] for lv in upper), default=ground[1]), ground[2], storm,
                    None if cloud is None else _number(cloud, "cloud_base_m", 0))


class HttpMeteoProvider(MeteoProvider):
    """REST-клиент: ``GET {base_url}/forecast?site=...&time=...``.

    `timeout` — ожидание соединения и каждого чтения из сокета (30 с, пробел П4).
    """

    TIMEOUT = 30.0

    def __init__(self, base_url: str, timeout: float = TIMEOUT):
        if not base_url.startswith(("http://", "https://")):
            raise ValueError("Адрес метеослужбы должен начинаться с http:// или https://")
        self._base = base_url.rstrip("/")
        self._timeout = timeout

    def fetch(self, site: str, at: datetime) -> Forecast:
        url = f"{self._base}/forecast?{urlencode({'site': site, 'time': at.isoformat(timespec='minutes')})}"
        try:
            with urllib.request.urlopen(url, timeout=self._timeout) as resp:
                body = resp.read(64 * 1024)
        except (socket.timeout, TimeoutError):
            raise MeteoUnavailable(f"служба не ответила за {self._timeout:g} с") from None
        except urllib.error.HTTPError as e:
            raise MeteoUnavailable(f"служба вернула ошибку HTTP {e.code}") from None
        except (urllib.error.URLError, OSError) as e:
            reason = getattr(e, "reason", e)
            if isinstance(reason, (socket.timeout, TimeoutError)):
                raise MeteoUnavailable(f"служба не ответила за {self._timeout:g} с") from None
            raise MeteoUnavailable(f"нет связи со службой ({reason})") from None
        try:
            return parse_forecast(json.loads(body.decode("utf-8")))
        except (UnicodeDecodeError, json.JSONDecodeError):
            raise MeteoUnavailable("неизвестный формат ответа: не JSON") from None
