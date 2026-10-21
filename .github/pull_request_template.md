## Descripción

<!-- Qué cambia y por qué. -->

## Tipo de cambio

- [ ] Corrección de error
- [ ] Nueva funcionalidad
- [ ] Documentación
- [ ] Refactor / mantenimiento
- [ ] Seguridad

## Fuente o justificación

<!-- Para cambios del portal del SRI: URL, manual oficial o ficha, y fecha de verificación. -->

## Pruebas

- [ ] `ruff check .` en verde
- [ ] `mypy` en verde
- [ ] `pytest` en verde
- [ ] Sin llamadas reales al SRI ni a anti-captcha en la suite

## Checklist

- [ ] Sin secretos, RUC, claves ni datos personales.
- [ ] Documentación actualizada (si aplica).
- [ ] `CHANGELOG.md` actualizado (si aplica).
- [ ] Los selectores del portal siguen en `config/selectors.yaml`.
- [ ] Se distingue confirmado / inferencia / validar.

## Notas para el revisor

<!-- Riesgos, decisiones, seguimiento. -->
