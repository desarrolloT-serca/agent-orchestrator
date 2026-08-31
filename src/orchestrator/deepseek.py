"""Cliente minimo de la API de DeepSeek (formato OpenAI chat completions)."""

from __future__ import annotations

import time
from datetime import datetime, timezone

import httpx

BASE_URL = "https://api.deepseek.com"
DEFAULT_MODEL = "deepseek-v4-flash"

# ponytail: sin streaming hasta que la UI lo necesite
REASONING = ("low", "high", "max")

# 429/5xx y caidas de red son infraestructura, no un fallo del worker: no deben
# consumir un intento de escalado Flash/Pro. Backoff corto porque el resto del
# budget (max_runtime_minutes) sigue corriendo mientras tanto.
RETRY_STATUS = {429, 500, 502, 503, 504}
RETRY_BACKOFF = (1, 3, 8)


class DeepSeekError(RuntimeError):
    pass


def chat(
    messages: list[dict],
    tools: list[dict] | None,
    api_key: str,
    model: str = DEFAULT_MODEL,
    reasoning: str = "high",
    timeout: int = 600,
) -> dict:
    """Una llamada a /chat/completions, con reintento corto ante fallos de infraestructura."""
    # reasoning_effort es un campo top-level, no va dentro de "thinking"
    # (confirmado en el ejemplo de payload de api-docs.deepseek.com/guides/thinking_mode)
    payload: dict = {
        "model": model,
        "messages": messages,
        "thinking": {"type": "enabled"},
        "reasoning_effort": reasoning,
    }
    if tools:
        payload["tools"] = tools

    ultimo: Exception | None = None
    for intento, espera in enumerate((*RETRY_BACKOFF, None)):
        try:
            r = httpx.post(
                f"{BASE_URL}/chat/completions",
                headers={"Authorization": f"Bearer {api_key}"},
                json=payload,
                timeout=timeout,
            )
        except httpx.HTTPError as exc:
            ultimo = DeepSeekError(f"error de red: {exc}")
        else:
            if r.status_code == 200:
                data = r.json()
                return {"message": data["choices"][0]["message"], "usage": data.get("usage", {})}
            if r.status_code not in RETRY_STATUS:
                raise DeepSeekError(f"HTTP {r.status_code}: {r.text[:500]}")
            ultimo = DeepSeekError(f"HTTP {r.status_code}: {r.text[:500]}")
        if espera is not None:
            time.sleep(espera)
    raise ultimo


# USD por 1M tokens: (cache hit, cache miss, output), off-peak y peak
PRICING = {
    "deepseek-v4-flash": ((0.007, 0.22, 0.66), (0.014, 0.44, 1.32)),
    "deepseek-v4-pro": ((0.022, 0.66, 1.98), (0.044, 1.32, 3.96)),
}


def is_peak(now: datetime | None = None) -> bool:
    """Horario peak: lunes a viernes 01:00-04:00 y 06:00-10:00 UTC."""
    now = now or datetime.now(timezone.utc)
    return now.weekday() < 5 and (1 <= now.hour < 4 or 6 <= now.hour < 10)


def cost(model: str, usage: dict) -> float:
    """Coste en USD de una llamada a partir del bloque usage de la respuesta."""
    hit_price, miss_price, out_price = PRICING.get(model, PRICING["deepseek-v4-flash"])[is_peak()]
    prompt = usage.get("prompt_tokens", 0)
    hit = usage.get("prompt_cache_hit_tokens", 0)
    miss = usage.get("prompt_cache_miss_tokens", prompt - hit)
    return (hit * hit_price + miss * miss_price + usage.get("completion_tokens", 0) * out_price) / 1_000_000
