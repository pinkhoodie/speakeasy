"""Weather lookups: a slow Open-Meteo reply no longer drops the card, and repeat questions are instant."""
from __future__ import annotations

import time

import pytest

from speakeasy import weather as W

HOME = "Springfield, Illinois (39.7817, -89.6501)"
FORECAST = {
    "current": {"time": "2026-10-03T16:00", "temperature_2m": 68.0, "apparent_temperature": 67.0,
                "weather_code": 0, "wind_speed_10m": 5.0},
    "daily": {"time": ["2026-10-03"], "weather_code": [0], "temperature_2m_max": [72.0],
              "temperature_2m_min": [58.0], "precipitation_probability_max": [5],
              "sunrise": ["2026-10-03T07:00"], "sunset": ["2026-10-03T18:40"]},
    "hourly": {"time": ["2026-10-03T16:00"], "temperature_2m": [68.0], "precipitation_probability": [0],
               "weather_code": [0]},
}


@pytest.fixture(autouse=True)
def fresh_cache():
    W._cache.clear()
    yield
    W._cache.clear()


def test_one_slow_reply_is_retried_not_dropped(monkeypatch):
    calls = []

    def flaky(url):
        calls.append(url)
        if len(calls) == 1:
            raise TimeoutError("timed out")
        return FORECAST

    monkeypatch.setattr(W, "_fetch_once", flaky)
    facts = W.facts("what's the weather today", HOME)
    assert facts and facts[0]["view"]["kind"] == "weather"
    assert len(calls) == 2


def test_repeat_question_is_served_from_cache(monkeypatch):
    calls = []
    monkeypatch.setattr(W, "_fetch_once", lambda url: (calls.append(url), FORECAST)[1])
    assert W.facts("weather today", HOME)
    assert W.facts("is it going to rain today", HOME)
    assert len(calls) == 1


def test_stale_forecast_is_fetched_again(monkeypatch):
    calls = []
    monkeypatch.setattr(W, "_fetch_once", lambda url: (calls.append(url), FORECAST)[1])
    W.facts("weather today", HOME)
    url = next(iter(W._cache))
    W._cache[url] = (time.monotonic() - W.FORECAST_TTL_S - 1, FORECAST)
    W.facts("weather today", HOME)
    assert len(calls) == 2


def test_warm_fills_the_cache_and_never_raises(monkeypatch):
    monkeypatch.setattr(W, "_fetch_once", lambda url: FORECAST)
    W.warm(HOME)
    assert W._cache
    W._cache.clear()

    def down(url):
        raise OSError("network down")

    monkeypatch.setattr(W, "_fetch_once", down)
    W.warm(HOME)  # no exception
    assert W.facts("weather today", HOME) == []
