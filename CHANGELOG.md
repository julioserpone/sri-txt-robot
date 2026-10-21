# Changelog

Todos los cambios relevantes se documentan aquí. El formato sigue
[Keep a Changelog](https://keepachangelog.com/es-ES/1.1.0/) y el proyecto usa
[SemVer](https://semver.org/lang/es/).

## [Unreleased]

## [0.1.0] - 2026-10-20

### Añadido

- Robot con Playwright que inicia sesión en el portal del SRI (Keycloak),
  resuelve el reCAPTCHA Enterprise con anti-captcha, consulta la pantalla de
  *Comprobantes electrónicos recibidos* y descarga el reporte TXT por tipo de
  comprobante.
- Cliente de la API de anti-captcha con reintento entre tipos de tarea,
  espera de resultado y reporte de tokens aceptados o rechazados.
- Inyección del token en el campo `g-recaptcha-response` y sustitución de
  `grecaptcha.getResponse()` para cubrir la lectura en servidor y en cliente.
- Detección en tiempo de ejecución de la clave pública del reCAPTCHA
  (el portal la obtiene de un endpoint propio, no está fija en el HTML).
- Selectores del portal externalizados a `config/selectors.yaml`, con lista de
  alternativas por elemento.
- Cálculo de la ventana de descarga de 5 días del SRI y del período sugerido.
- Ejecución idempotente: un TXT ya descargado no se vuelve a pedir.
- Configuración multi-RUC, con secretos por variable de entorno o Secrets
  Manager, y persistencia de la sesión del portal entre ejecuciones.
- Publicación del TXT, el manifiesto de claves de acceso y el resumen del
  período en Amazon S3 o en disco.
- Comandos `descargar`, `calibrar`, `claves`, `saldo` y `periodo`.
- Imagen de contenedor para AWS Lambda, plantilla SAM y Makefile.
- Pruebas unitarias, `ruff`, `mypy` en modo estricto y CI.

### Documentación

- README con los hallazgos técnicos del portal, las decisiones de diseño
  (lenguaje y runtime) y el procedimiento de calibración.
- `SECURITY.md` con el tratamiento de secretos y datos personales.

[Unreleased]: https://github.com/julioserpone/sri-txt-robot/compare/v0.1.0...HEAD
[0.1.0]: https://github.com/julioserpone/sri-txt-robot/releases/tag/v0.1.0
