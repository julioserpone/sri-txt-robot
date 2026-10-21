"""Resolución de reCAPTCHA con anti-captcha."""

from __future__ import annotations

from .anticaptcha import AntiCaptchaClient, AntiCaptchaError, CaptchaNoResueltoError, SolucionCaptcha

__all__ = [
    "AntiCaptchaClient",
    "AntiCaptchaError",
    "CaptchaNoResueltoError",
    "SolucionCaptcha",
]
