from __future__ import annotations

from collections import deque
from dataclasses import dataclass, field
from datetime import date
import os
import threading
import time
from typing import Any, Iterable


@dataclass(slots=True)
class SlidingWindowRateLimiter:
    """Headroom below Groww's shared live-data 10/sec and 300/minute limits."""
    max_per_second: int = 8
    max_per_minute: int = 240
    _calls: deque[float] = field(default_factory=deque)
    _lock: threading.Lock = field(default_factory=threading.Lock)

    def __post_init__(self) -> None:
        if self.max_per_second <= 0 or self.max_per_minute <= 0:
            raise ValueError("rate limits must be positive")
        if self.max_per_second > self.max_per_minute:
            raise ValueError("per-second limit cannot exceed per-minute limit")

    def wait(self) -> None:
        while True:
            with self._lock:
                now = time.monotonic()
                while self._calls and now - self._calls[0] >= 60.0:
                    self._calls.popleft()
                last_second = sum(1 for called_at in self._calls if now - called_at < 1.0)
                minute_full = len(self._calls) >= self.max_per_minute
                second_full = last_second >= self.max_per_second
                if not minute_full and not second_full:
                    self._calls.append(now)
                    return
                waits: list[float] = []
                if minute_full:
                    waits.append(max(60.0 - (now - self._calls[0]), 0.001))
                if second_full:
                    recent = [called_at for called_at in self._calls if now - called_at < 1.0]
                    waits.append(max(1.0 - (now - recent[0]), 0.001))
                sleep_for = min(waits) if waits else 0.01
            time.sleep(sleep_for)


class GrowwMarketData:
    """REST snapshot adapter; use GrowwFeed for high-frequency LTP updates."""
    def __init__(self, access_token: str, limiter: SlidingWindowRateLimiter | None = None) -> None:
        from growwapi import GrowwAPI
        self.groww = GrowwAPI(access_token)
        self.limiter = limiter or SlidingWindowRateLimiter()

    @classmethod
    def from_env(cls) -> "GrowwMarketData":
        from growwapi import GrowwAPI
        token = os.getenv("GROWW_ACCESS_TOKEN")
        if token:
            return cls(token)
        api_key = os.getenv("GROWW_API_KEY")
        api_secret = os.getenv("GROWW_API_SECRET")
        totp_token = os.getenv("GROWW_TOTP_TOKEN")
        totp_secret = os.getenv("GROWW_TOTP_SECRET")
        if totp_token and totp_secret:
            import pyotp
            token = GrowwAPI.get_access_token(api_key=totp_token, totp=pyotp.TOTP(totp_secret).now())
            return cls(token)
        if api_key and api_secret:
            return cls(GrowwAPI.get_access_token(api_key=api_key, secret=api_secret))
        raise RuntimeError("configure a Groww access token, API key/secret, or TOTP flow")

    def quote(self, symbol: str, segment: str = "CASH", *, exchange: str = "NSE") -> dict[str, Any]:
        self.limiter.wait()
        return dict(self.groww.get_quote(exchange=exchange, segment=segment, trading_symbol=symbol))

    def quote_many(self, symbols: Iterable[str], segment: str = "CASH") -> dict[str, dict[str, Any]]:
        return {symbol: self.quote(symbol, segment=segment) for symbol in symbols}

    def ltp_many(self, symbols: Iterable[str], segment: str = "CASH") -> dict[str, Any]:
        names = tuple(symbols)
        if not names:
            return {}
        if len(names) > 50:
            raise ValueError("Groww get_ltp supports at most 50 instruments per call")
        self.limiter.wait()
        return dict(self.groww.get_ltp(
            segment=segment,
            exchange_trading_symbols=tuple(f"NSE_{symbol}" for symbol in names),
        ))

    def option_chain(self, expiry: date, *, exchange: str = "NSE", underlying: str = "NIFTY") -> dict[str, Any]:
        self.limiter.wait()
        return dict(self.groww.get_option_chain(
            exchange=exchange, underlying=underlying, expiry_date=expiry.isoformat()
        ))

    def nearest_nifty_future(self, today: date | None = None) -> dict[str, Any]:
        today = today or date.today()
        frame = self.groww.get_all_instruments()
        matches = frame[
            (frame["exchange"] == "NSE")
            & (frame["segment"] == "FNO")
            & (frame["instrument_type"] == "FUT")
            & (frame["underlying_symbol"] == "NIFTY")
        ].copy()
        matches["expiry_date"] = matches["expiry_date"].astype(str)
        matches = matches[matches["expiry_date"] >= today.isoformat()].sort_values("expiry_date")
        if matches.empty:
            raise RuntimeError("no live NIFTY future found in Groww instrument master")
        return dict(matches.iloc[0].to_dict())
