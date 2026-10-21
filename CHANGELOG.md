# Changelog

Todos los cambios relevantes se documentan aquí. El formato sigue
[Keep a Changelog](https://keepachangelog.com/es-ES/1.1.0/) y el proyecto usa
[SemVer](https://semver.org/lang/es/).

## [Unreleased]

### Añadido

- **Configuración por línea de comandos**: `--ruc`, `--password`, `--api-key`,
  `--perfil`, `--salida` y `--visible` sustituyen al YAML. Sin `--config` el
  robot funciona con valores por defecto (`./datos`, todos los tipos) y la
  clave se pregunta de forma oculta si no se pasa `--password`.
- **Perfil de Chrome dedicado y calentado** (`portal.perfil_dir`): el portal
  solo acepta el reCAPTCHA de un perfil que ya recibió una consulta hecha por
  una persona. El robot lanza Chrome sin las banderas de automatización de
  Playwright y se conecta por CDP, así que después de un clic manual todas las
  descargas son automáticas.
- Comando `sri-txt-robot calentar`: abre el perfil dedicado, espera la consulta
  manual y lo deja habilitado.
- **`scripts/instalar.sh`**: elige un Python ≥ 3.12, recrea `.venv` con
  `--clear` e instala con `python -m pip`, de modo que no se mezclen
  intérpretes.
- Descarga de **varios períodos en una sola ejecución**: `-p` es repetible
  (`-p 2026-07 -p 2026-08 -p 2026-09`) y también acepta listas separadas por
  comas. El evento de Lambda acepta `periodos` (y mantiene `periodo`).
- **Cierre de sesión** al terminar todas las descargas, desde el menú superior
  derecho del portal, incluso si algún período falló.
- El robot intenta primero el captcha con el propio navegador y solo recurre a
  anti-captcha si el portal lo rechaza.

### Corregido

- El login tolera que Playwright no reciba el evento de navegación tras pulsar
  "Ingresar" (habitual al conectar por CDP): el resultado se decide por la URL,
  no por la espera del clic.
- La guía de instalación usaba `python3.12`, que no existe en todas las macOS;
  ahora indica cualquier intérprete ≥ 3.12 y el uso real sin editar el YAML.
- `pip install -e ".[dev]"` podía tardar minutos en `Using cached …`: un
  `.venv` creado primero con 3.13 y luego con `python3` (3.11) conserva el
  `lib/python3.11` y un `pip` con shebang al intérprete viejo, que resuelve
  para 3.11 un proyecto `>=3.12` y entra en backtracking sobre `ruff`. Ahora
  la instalación recrea el entorno y el README explica el síntoma.
- El portal no responde a Chromium cuando se identifica como headless ni cuando
  negocia HTTP/2: se fija un User-Agent de Chrome de escritorio y
  `--disable-http2`. Sin esto todas las ejecuciones acababan en
  `chrome-error://chromewebdata/`.
- Una sesión guardada caducada (`about:blank`, página de error) ya no se toma
  por una sesión válida.
- El manifiesto de cada período ya solo incluye las descargas de ese período.
- La pantalla de comprobantes se abre con `accederAplicacion.jspa` (token de un
  solo uso); la URL limpia del JSF devolvía al formulario de login.

### Cambiado

- Se elimina la supuesta ventana de descarga de 5 días: la consulta funciona
  para meses cerrados y años anteriores. `period.py` solo valida los años que
  ofrece el portal.
- La clave pública del reCAPTCHA es fija (`6LdukTQsAAAAAIcciM4GZq4ibeyplUhmWvlScuQE`)
  y el token debe ser de **Enterprise invisible v2**; los de v3 se rechazan.

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
