"""Cliente heredado de anti-captcha y utilidades de inyección del token."""

from __future__ import annotations

from .anticaptcha import AntiCaptchaClient, AntiCaptchaError, CaptchaNoResueltoError, SolucionCaptcha

__all__ = [
    "AntiCaptchaClient",
    "AntiCaptchaError",
    "CaptchaNoResueltoError",
    "SolucionCaptcha",
]
