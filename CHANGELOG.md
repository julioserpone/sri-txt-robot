# Changelog

Todos los cambios relevantes se documentan aquí. El formato sigue
[Keep a Changelog](https://keepachangelog.com/es-ES/1.1.0/) y el proyecto usa
[SemVer](https://semver.org/lang/es/).

## [Unreleased]

### Añadido

- **Callback firmado al terminar cada invocación** (`SRI_CALLBACK_URL` +
  `SRI_CALLBACK_SECRET`): el robot envía un `POST` con `event_id`, timestamp,
  firma `HMAC-SHA256` en `X-Sri-Signature` y el resumen. Reintenta ante red,
  `408`, `425`, `429` y `5xx`, y respeta el tiempo restante de la invocación.
  Un evento puede proponer `callback_url` solo si su host está autorizado en
  `SRI_CALLBACK_ALLOWED_HOSTS`. Desbloquea el callback firmado de
  `autotributo` (F2-04).
- **Arranque perezoso**: el perfil de Chrome y el bundle del navegador se
  preparan después de comprobar la idempotencia en S3, solo si hay descargas
  pendientes. Una invocación sin trabajo responde sin materializar el runtime.
- **Artefactos del despliegue ZIP** (`scripts/construir_zip.sh`): bundle del
  navegador (Chromium + Xvfb + librerías + fuentes) y paquete de la función,
  construidos sobre la misma base AL2023 del runtime de Lambda.
- `sri_txt_robot.runtime`: descarga desde S3, verifica SHA-256 y extrae el
  bundle y el perfil en `/tmp` de forma atómica.
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
- **Datos organizados por ambiente** (`--ambiente`, `SRI_AMBIENTE`): la clave
  es `{ambiente}/{ruc}/{periodo}/{tipo}-recibidas-{periodo}.txt`, con nombres
  legibles (`facturas-recibidas-2026-08.txt`,
  `notas-credito-recibidas-2026-08-claves.json`). El árbol es idéntico en
  disco y en S3.
- **`storage.prefix`** queda vacío por defecto y `SRI_BUCKET` permite fijar el
  bucket en Lambda sin recompilar la imagen.
- **Despliegue en Lambda sin Secrets Manager** (`scripts/publicar_zip.sh`):
  publica un ZIP por referencia a S3, reutiliza el rol y la clave privada de la
  función existente y configura el runtime. Es idempotente: si la función ya
  existe no toca nada salvo `--actualizar`. El flujo anterior con imagen de
  contenedor y ECR (`scripts/publicar.sh`) queda obsoleto y ya no se usa.
- **Credenciales selladas por invocación** (`sealed_credentials`): el evento
  puede traer el RUC y la clave del portal cifrados con
  `sodium_crypto_box_seal` usando la clave pública del robot. La Lambda las
  abre con `SRI_CREDENTIALS_PRIVATE_KEY`, valida que el RUC coincida con el
  exterior y ejecuta con un contribuyente dinámico. Permite varios RUC sin
  volver a desplegar, sin `SRI_PASSWORD` y sin AWS Secrets Manager. El modo
  estático sigue existiendo en el flujo heredado con imagen.
- Comandos `sri-txt-robot generar-claves` y `sri-txt-robot
  sellar-credenciales` para generar el par de claves y probar el sobre.
- `scripts/publicar_zip.sh --private-key-file` (con `--env-file` para secretos
  adicionales): publica con `robots: []`, omite `SRI_PASSWORD`, no crea la
  regla mensual y configura `SRI_CREDENTIALS_PRIVATE_KEY`.
- `SRI_CREDENTIALS_PRIVATE_KEY_ANTERIOR` y
  `SRI_CREDENTIALS_MAX_AGE_SECONDS` para rotar claves y limitar la antigüedad
  de los sobres.
- Dependencia `PyNaCl` para el sellado compatible con el
  `sodium_crypto_box_seal` de PHP.
- `deploy/Dockerfile` (Playwright + `awslambdaric`) y
  `deploy/Dockerfile.lambda` (configuración, perfil calentado y banderas de
  Chrome para Lambda): flujo heredado con imagen, ya no se usa.
- `deploy/Dockerfile.calentar`: añade `x11vnc` a la imagen para calentar el
  perfil dentro de Linux una sola vez, por VNC.
- La opción `--selectors` respeta `SRI_ROBOT_SELECTORS`, para que la función
  ZIP encuentre los selectores en `/var/task/config`.
- Descarga de **varios períodos en una sola ejecución**: `-p` es repetible
  (`-p 2026-07 -p 2026-08 -p 2026-09`) y también acepta listas separadas por
  comas. El evento de Lambda acepta `periodos` (y mantiene `periodo`).
- **Cierre de sesión** al terminar todas las descargas, desde el menú superior
  derecho del portal, incluso si algún período falló.
- El robot intenta primero el captcha con el propio navegador y solo recurre a
  anti-captcha si el portal lo rechaza.

### Validado en AWS (oct-2026)

- **Lambda de extremo a extremo**: el captcha lo resuelve el navegador, sin
  anti-captcha (44 claves en 2026-09 y 24 en 2026-08, ejecuciones de 45-60 s
  con 3 GB). Funciona con la sesión de Keycloak caducada: el login se hace solo
  y la confianza del reCAPTCHA sigue vigente.
- El perfil debe calentarse **dentro del contenedor Linux**
  (`deploy/Dockerfile.calentar` + VNC): el calentado en macOS no transfiere la
  confianza.
- Chrome se lanza con `--no-zygote` (`SRI_CHROME_BANDERAS` en la función): el
  sandbox de Lambda no permite el `fork` del zygote y el navegador abortaba con
  `Zygote could not fork` / `GPU process isn't usable`.
- El runtime extrae el perfil y el navegador a `/tmp` de forma perezosa: en
  Lambda `/var/task` es de solo lectura y la fase de inicialización tiene un
  límite de 10 s, así que la copia no puede hacerse en el arranque.
- **Función ZIP de producción `sri-txt-robot`** (arm64, 3008 MB, 900 s):
  reemplazada en sitio (se eliminó la imagen y luego el repositorio ECR) y
  validada con una descarga real de 2026-09: 44 facturas, 8.164 bytes, captcha
  resuelto por el navegador y credenciales selladas por invocación en ~32 s.
- **Función ZIP de prueba**: bootstrap desde S3 en ~6 s, Xvfb con keymap propio
  y login de Chrome correctos; el callback firmado llegó a `autotributo` con
  `202` al primer intento.

### Obsoleto

- `scripts/publicar.sh` (imagen de contenedor + ECR). El despliegue soportado
  es `scripts/publicar_zip.sh`; el script heredado muestra un aviso y se niega
  a ejecutarse salvo que se exporte `SRI_PERMITIR_PUBLICAR_IMAGEN=1`.

### Eliminado

- Repositorio ECR `sri-txt-robot` con la imagen `:produccion`, la función de
  imagen original y la función de prueba `sri-txt-robot-zip`. El rollback ahora
  consiste en volver a desplegar el ZIP.

### Corregido

- **Xvfb no podía compilar el keymap en el despliegue ZIP**: el runtime de
  Lambda no trae `xkbcomp` ni `xkeyboard-config`. El bundle ahora incluye
  ambos y el binario de Xvfb se parchea para buscar `xkbcomp` en `/tmp/xkb`
  (`/usr/bin` es de solo lectura allí); el bootstrap crea el enlace.
- **Chrome abortaba en el runtime limpio de Lambda con
  `FATAL:crypto/nss_util.cc nss_error=-5925`**: faltaban los módulos PKCS#11 de
  NSS que Chrome carga con `dlopen` (`libsoftokn3`, `libfreebl3`,
  `libnssckbi`…). Ahora viajan en el bundle y se resuelven por
  `LD_LIBRARY_PATH`.
- **La consulta tardaba 30 s de más**: `_esperar_a` usaba `time.sleep`, y en
  Playwright síncrono eso no bombea eventos, así que el escucha de respuestas
  nunca veía el envío con token y se agotaba el tiempo en cada tipo. Ahora se
  usa `expect_response` y el resultado se sondea cada 100 ms: la consulta pasó
  de ~30 s a ~0,6 s (una ejecución de 2 tipos, de 80 s a 22 s).
- Elegir opción en los desplegables esperaba 5 s por cada alternativa que no
  existía; ahora se leen las opciones una vez y se selecciona la que coincide.
- El perfil de macOS lleva enlaces `SingletonLock/Socket/Cookie` que en Linux
  hacen que Chrome crea que el perfil está en uso y termine con código 21; la
  imagen los elimina al copiarlo y el robot los retira antes de cada arranque,
  también cuando reutiliza el perfil en `/tmp` dentro de Lambda.
- En un perfil ya calentado el portal podía completar la navegación antes de que
  `_esperar_sesion` empezara a observarla; la espera agotaba los 90 s aunque la
  sesión ya estuviera establecida. Ahora se comprueba primero la URL actual.
- Xvfb se arranca de forma perezosa antes de abrir Chrome y se comprueba que
  su socket acepta conexiones, no solo que el archivo exista: un socket
  huérfano tras el timeout de inicialización de Lambda hacía que Chrome
  terminara con `Missing X server or $DISPLAY`.
- Xvfb puede desaparecer cuando Lambda reutiliza el microVM; el robot lo
  detecta, retira sockets/locks huérfanos y lo relanza (`SRI_ASEGURAR_XVFB`)
  antes de abrir Chrome.
- Si el arranque del navegador falla, `Navegador` detiene Playwright en lugar
  de dejarlo vivo; en el sandbox reutilizado eso provocaba en la siguiente
  invocación `Sync API inside the asyncio loop`.
- El filtro de consulta acepta la cédula de 10 dígitos aunque el portal muestre
  el RUC completo de 13 (`...001`) en el campo deshabilitado; antes fallaba con
  "El filtro de RUC está deshabilitado".
- En Linux el perfil dedicado se atascaba en HTTP/2 al entrar al portal: se
  lanza con `--disable-http2`, igual que el modo clásico.
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
