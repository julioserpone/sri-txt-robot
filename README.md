# SRI TXT Robot

[![CI](https://github.com/julioserpone/sri-txt-robot/actions/workflows/ci.yml/badge.svg)](https://github.com/julioserpone/sri-txt-robot/actions/workflows/ci.yml)
[![License](https://img.shields.io/badge/license-MIT-blue.svg)](LICENSE)

Robot que entra al portal del SRI, resuelve el reCAPTCHA (primero con el propio
navegador, con **anti-captcha** como respaldo), consulta los comprobantes
electrónicos **recibidos** de uno o varios meses y descarga el reporte **TXT**
de cada tipo de comprobante. Al terminar, cierra la sesión.

Es la pieza que falta entre el portal del SRI y el paquete
[`sri-comprobantes-php`](https://github.com/julioserpone/sri-comprobantes-php):
el TXT trae la **clave de acceso** de cada comprobante recibido y el paquete
consulta el XML autorizado de cada una por la API oficial.

```text
┌──────────────┐   TXT    ┌──────────────────┐   claves   ┌──────────────────────┐
│  Portal SRI  │ ───────► │  sri-txt-robot   │ ─────────► │ sri-comprobantes-php │
│  (Keycloak + │          │  (Python+Playwright)│           │ (SOAP oficial, 1 a 1)│
│  reCAPTCHA)  │          └──────────────────┘           └──────────┬───────────┘
└──────────────┘                                                   │ XML autorizado
                                                                   ▼
                                                            tu aplicación
```
> **Este proyecto no está afiliado ni respaldado por el SRI.** Automatiza el
> acceso de un contribuyente a **sus propios** comprobantes. Revise las
> condiciones de uso del portal y las obligaciones de protección de datos
> antes de operarlo. Ver [Consideraciones legales](#consideraciones-legales).

---

## Hallazgos técnicos sobre el portal

Verificados por inspección del portal y del manual oficial del SRI
(*Comprobantes electrónicos / Manual de usuario - internet*, 21 páginas) en
octubre de 2026:

| Aspecto | Detalle |
| --- | --- |
| **Login** | Keycloak en `/tuportal-internet/`; acción `/auth/realms/Internet/login-actions/authenticate`. Campos `usuario` (RUC de 13 dígitos), `ciAdicional` (opcional) y `password`. **El login no tiene reCAPTCHA.** |
| **Sesión** | La app JSF (`/comprobantes-electronicos-internet/pages/consultas/recibidos/comprobantesRecibidos.jsf`) **no** se puede abrir por su URL limpia con la sesión iniciada: exige el token de un solo uso que entrega `/tuportal-internet/accederAplicacion.jspa?redireccion=57&idGrupo=55` y, si falta, devuelve al formulario de Keycloak. El robot usa esa URL de entrada. |
| **Captcha** | reCAPTCHA **Enterprise invisible v2**. La clave pública sí está fija en el HTML: `6LdukTQsAAAAAIcciM4GZq4ibeyplUhmWvlScuQE`, con acción `consulta_cel_recibidos`. El servidor valida el token con semántica **v2**: un token v3 (`grecaptcha.enterprise.execute({action})`) se rechaza con *"No se pudo validar el captcha en google"*. |
| **Pantalla** | *Facturación Electrónica → Comprobantes electrónicos recibidos*. Filtros: *Periodo de emisión* (año, mes, día — con **Todos** para el mes completo) y *Tipo de comprobante*. Acciones: **Consultar** (dispara el captcha) y **Descargar reporte** (entrega el TXT). |
| **Salida** | El TXT lista los comprobantes con su *Clave de acceso (CA)* de 49 dígitos. |
| **Histórico** | **No hay ventana de 5 días**: en octubre de 2026 se descargaron TXT de septiembre de 2026 y de enero de 2022 sin problema. |

### Sobre la supuesta ventana de 5 días

El manual del portal menciona un plazo para el reporte, pero la consulta real
**no lo aplica**: el robot descarga meses cerrados y meses de años anteriores.
Por eso `period.py` no bloquea períodos antiguos; solo valida que el año exista
en el desplegable (`2022`–`2026` en la última revisión) y sugiere el mes cerrado
anterior cuando no se indica ninguno.

El trabajo sigue siendo mensual (EventBridge), pero ya no es obligatorio
ejecutarlo en los primeros días del mes: sirve también para recuperar históricos.

---

## Decisiones de diseño

### Lenguaje: Python 3.12+ + Playwright

| Alternativa | Por qué no |
| --- | --- |
| **PHP** (extensión del paquete) | No hay un controlador de navegador headless en PHP con la madurez de Playwright: `chrome-php/chrome` habla CDP a pelo y Symfony Panther arrastra Symfony. Además, atar el paquete a la UI del portal contradice el ADR 0003. |
| Node / TypeScript | Válido (Playwright es TS-first), pero Python tiene SDK oficial de anti-captcha, mejor tooling de datos para el parseo del TXT y `httpx`/`pydantic` más sobrios para un servicio de esta forma. |
| **Python 3.12+ + Playwright** ✅ | Playwright resuelve los problemas reales de una app JSF: estado de vista, sesión y descarga de archivos. Auto-espera, sin *sleeps*. El SDK oficial de anti-captcha. Y `boto3`/`httpx`/`pydantic` cubren el resto sin tirar de frameworks. |

El paquete PHP **no se toca** para esto: sigue siendo la capa de integración
con los WSDL oficiales. La frontera entre ambos es un archivo (el TXT o su
manifiesto JSON de claves).

### Runtime: AWS Lambda (imagen de contenedor) + EventBridge

- El trabajo es **mensual y a ráfagas**: Lambda encaja exactamente en ese
  perfil. 12 invocaciones al año cuestan céntimos, frente a ~USD 15-20/mes de
  una instancia EC2 siempre encendida.
- Playwright necesita Chromium (~150 MB) y sus dependencias de sistema → **imagen
  de contenedor**, no un ZIP. La imagen parte de
  `mcr.microsoft.com/playwright/python:v1.64.0-noble`, que ya trae Chromium y
  sus dependencias, y añade `awslambdaric` como Runtime Interface Client. Al
  traer su propia userland, la imagen de Playwright es válida en Lambda: la
  restricción de glibc aplica a los runtimes gestionados y a los paquetes ZIP,
  no a las imágenes de contenedor.
- Dentro del contenedor se arranca **Xvfb** y Chrome corre "con ventana": en
  headless se anuncia como `HeadlessChrome` y el portal no responde.
- **Sin Secrets Manager**: la clave del portal va en una variable de entorno
  de la función (cifrada en reposo por Lambda) y los resultados en S3.
- El despliegue lo hace `scripts/publicar.sh` (bucket, ECR, IAM, Lambda y la
  regla mensual de EventBridge), no una plantilla SAM.
- Alternativa si prefieres un servidor: el robot es un CLI (`sri-txt-robot
  descargar`), así que un `cron` en EC2 o un `systemd timer` funcionan igual.

### Decisiones de robustez

- **Idempotencia:** si el TXT de una combinación RUC × período × tipo ya existe,
  no se vuelve a descargar. Reintentar la invocación tras un fallo parcial no
  duplica gasto de anti-captcha ni golpea otra vez al portal.
- **Selectores externalizados:** todo selector vive en `config/selectors.yaml`
  como lista de alternativas. Un cambio del portal se corrige en el YAML, sin
  tocar código.
- **Reintento del captcha:** cada token vive ~2 minutos. Si el portal lo
  rechaza, el robot resuelve uno nuevo y reintenta (3 intentos por defecto).
- **Inyección en dos niveles:** el token se escribe en el campo
  `g-recaptcha-response` **y** se sustituye `grecaptcha.getResponse()`, para
  cubrir tanto la lectura en servidor como la lectura por JavaScript.
- **Sesión persistente:** se guarda el `storage_state` (cookies) por RUC para
  no iniciar sesión en cada ejecución. Solo se reutiliza si la pestaña quedó en
  una página real del portal; un `about:blank` o una página de error de Chromium
  fuerzan el login de nuevo.
- **Cierre de sesión garantizado:** al terminar de descargar todos los períodos
  pedidos se pulsa **Cerrar sesión** en el menú superior derecho
  (`#topbar-menu-button` → `a.boton-cerrar-legado`), incluso si alguna descarga
  falló. Se ejecuta en un `finally`, así que también corre cuando hay excepciones.
- **Varios períodos por ejecución:** `-p` es repetible
  (`-p 2026-07 -p 2026-08 -p 2026-09`, o `-p 2026-07,2026-08`). Se abre **una**
  sesión y se recorre período × tipo dentro de ella.
- **Navegador de escritorio, no "headless" identificable:** contra
  `srienlinea.sri.gob.ec`, Chromium con su User-Agent de headless se queda
  esperando para siempre (sin respuesta ni error de red) y termina en
  `chrome-error://chromewebdata/`. Con un User-Agent de Chrome de escritorio y
  `--disable-http2` la portada responde en décimas de segundo. Ambas cosas están
  fijadas en `portal/session.py` y se pueden sobrescribir con
  `portal.user_agent`.

---

## Puesta en marcha

### 1. Requisitos

- Python 3.12 o superior. En macOS `python3` puede ser 3.11: compruebe con
  `python3 --version` y use el binario nuevo (`python3.13`, `python3.12`, …)
  si hace falta.
- Google Chrome instalado (solo para el perfil dedicado; el modo clásico usa
  el Chromium de Playwright).
- anti-captcha es **opcional**: hoy el portal rechaza sus tokens, así que el
  robot funciona sin cuenta.

La forma corta elige sola un Python válido y recrea el entorno limpio:

```bash
cd sri-txt-robot
bash scripts/instalar.sh
source .venv/bin/activate
```

Equivalente a mano (note el `python -m pip`, no `pip` a secas):

```bash
python3.13 -m venv --clear .venv
source .venv/bin/activate
python -m pip install --upgrade pip
python -m pip install -e ".[dev]"
```

Si va a usar el modo clásico (sin perfil dedicado), instale además el
navegador de Playwright:

```bash
playwright install chromium
```

> **Si `pip install` se queda minutos en `Using cached …`**: está corriendo un
> `pip` de otro Python. Pasa cuando se ejecuta `python3 -m venv .venv` sobre un
> entorno ya creado con 3.13 (`venv` no borra): quedan `lib/python3.11` y un
> `pip` con shebang al 3.11, que resuelve un proyecto `>=3.12` y hace
> *backtracking* sin fin. Compruébelo con `python -m pip --version` y recree el
> entorno con `bash scripts/instalar.sh`.

### 2. Configuración (opcional)

No hace falta editar ningún archivo: RUC, clave, perfil y carpeta de salida se
pasan en el mismo comando. Copie la plantilla solo si prefiere un YAML (varios
RUC, S3, etc.):

```bash
cp config/robots.example.yaml config/robots.yaml
```

En ese caso, `captcha.api_key` es la **clave** de anti-captcha y
`captcha.api_key_env` es el **nombre de la variable de entorno** que la
contiene; no mezcle ambos. `robots[].password_env` también espera un nombre de
variable, no la clave.

### 3. Calentar el perfil de Chrome

El reCAPTCHA Enterprise del portal solo acepta tokens de un navegador que ya
haya recibido **una consulta hecha por una persona**. Un perfil recién creado,
aunque el robot haga clic con eventos reales (`isTrusted`, `userActivation`),
recibe siempre `Captcha incorrecta`.

El flujo tiene dos pasos y solo el primero necesita intervención:

```bash
# 1) Una sola vez: abre Chrome con el perfil dedicado y espera tu consulta
sri-txt-robot calentar --ruc 1758871873001 --perfil ~/.sri-txt-robot/perfil
```

Si omite `--password`, el comando pide la clave de forma oculta. También puede
pasarla en el mismo comando (`--password '...'`), aunque quedará en el
historial del shell.

En la ventana que se abre: elija un mes cerrado, deje el día en **Todos**,
pulse **Consultar** a mano y espere los resultados. El comando termina solo y
deja el perfil habilitado.

```bash
# 2) A partir de aquí, todo automático
sri-txt-robot descargar \
  --ruc 1758871873001 \
  --perfil ~/.sri-txt-robot/perfil \
  --visible \
  -p 2026-07 -p 2026-08 -p 2026-09 \
  -t 01
```

`--salida ./datos` cambia la carpeta de los TXT y `--api-key` define la clave
de anti-captcha si algún día hace falta.

Detalles que importan:

- `portal.perfil_dir` debe apuntar a una carpeta **dedicada** (por ejemplo
  `~/.sri-txt-robot/perfil`), nunca al perfil personal de Chrome.
- El robot lanza Chrome **sin** las banderas de automatización de Playwright
  (`--enable-automation`, `--remote-debugging-pipe`) y se conecta por CDP. Con
  esas banderas el portal rechaza incluso un perfil ya calentado.
- Este modo necesita una ventana visible: en macOS `headless` se ignora.
- Si el portal vuelve a responder `Captcha incorrecta` (por ejemplo tras muchos
  días o un cambio de red), repita `calentar`.
- anti-captcha queda solo como respaldo opcional: el portal rechazó todos sus
  tokens en las pruebas de octubre de 2026.

### 4. Calibrar contra el portal real

Este paso es **imprescindible**: la primera vez hay que confirmar que los
selectores siguen vigentes. El comando inicia sesión, abre la pantalla de
consulta y vuelca el DOM:

```bash
sri-txt-robot calibrar --ruc 1790012345001 --salida diagnostico
```

Genera:

- `diagnostico/pantalla-consulta.html`: el HTML de la pantalla.
- `diagnostico/diagnostico.json`: los `<select>` con sus opciones (año, mes,
  día, tipo de comprobante), los botones y enlaces detectados, y el tipo de
  reCAPTCHA con su clave pública.

Con ese archivo se ajusta `config/selectors.yaml`. Si el robot no encuentra la
pantalla, revise `consulta.url_directa`: debe ser la URL del menú
(`/tuportal-internet/accederAplicacion.jspa?redireccion=57&idGrupo=55`), no la
URL limpia del JSF.

### 5. Ejecutar

```bash
# Sin archivo de configuración: todo por línea de comandos
sri-txt-robot descargar \
  --ruc 1758871873001 \
  --perfil ~/.sri-txt-robot/perfil \
  --visible \
  -p 2026-07 -p 2026-08 -p 2026-09 -t 01

# Separar los datos por entorno (dev, test, produccion)
sri-txt-robot descargar --ruc 1758871873001 --ambiente test -p 2026-08

# Con YAML (varios RUC, S3, tipos por defecto)
sri-txt-robot descargar --config config/robots.yaml

# Un RUC, un tipo y un período concretos
sri-txt-robot descargar -r 1790012345001 -t 01 -p 2026-09

# Varios períodos en una sola sesión (se cierra al terminar)
sri-txt-robot descargar -p 2026-07 -p 2026-08 -p 2026-09

# Equivalente, con lista separada por comas
sri-txt-robot descargar -p 2026-07,2026-08,2026-09

# Ver los años que ofrece el portal y el período recomendado
sri-txt-robot periodo

# Ver el saldo de anti-captcha
sri-txt-robot saldo

# Extraer las claves de un TXT ya descargado
sri-txt-robot claves datos/test/1758871873001/2026-09/facturas-recibidas-2026-09.txt
```

### 6. Resultado

Los archivos se guardan con la misma jerarquía en disco y en S3:

```text
datos/                                   s3://<bucket>/
└── test/                                └── test/
    └── 1758871873001/                       └── 1758871873001/
        └── 2026-08/                             └── 2026-08/
            ├── facturas-recibidas-2026-08.txt
            ├── facturas-recibidas-2026-08-claves.json
            └── manifest.json
```

La clave completa es:

```text
<ambiente>/<ruc>/<periodo>/<tipo>-recibidas-<periodo>.txt
<ambiente>/<ruc>/<periodo>/<tipo>-recibidas-<periodo>-claves.json
<ambiente>/<ruc>/<periodo>/manifest.json
```

| Tipo | Archivo |
| --- | --- |
| `01` Factura | `facturas-recibidas-2026-08.txt` |
| `03` Liquidación de compra | `liquidaciones-compra-recibidas-2026-08.txt` |
| `04` Nota de crédito | `notas-credito-recibidas-2026-08.txt` |
| `05` Nota de débito | `notas-debito-recibidas-2026-08.txt` |
| `07` Retención | `retenciones-recibidas-2026-08.txt` |

El ambiente se elige con `--ambiente` (o `SRI_AMBIENTE`): `dev`, `test` o
`produccion`. Así los mismos RUC y períodos conviven en un solo bucket sin
mezclarse.

El `.claves.json` es el contrato de entrada para el paquete PHP:

```json
{
  "ambiente": "test",
  "ruc": "1758871873001",
  "periodo": "2026-08",
  "tipo": "01",
  "tipoEtiqueta": "Factura",
  "archivo": "facturas-recibidas-2026-08.txt",
  "total": 128,
  "clavesAcceso": ["1509202601179001234500120010010000001231234567812"]
}
```

Desde PHP, la clave del JSON para un período se arma siempre igual:

```php
$clave = "produccion/{$ruc}/{$periodo}/facturas-recibidas-{$periodo}-claves.json";
```

---

## Despliegue en AWS

> **Estado (oct-2026): el empaquetado está validado, el captcha no.**
> La imagen se construye y el robot se ejecuta dentro del contenedor Linux
> (Xvfb + Chrome "con ventana"), pero el portal **rechaza el reCAPTCHA** con el
> perfil calentado en macOS: la confianza parece atada al navegador/sistema
> donde se calentó. También se probó anti-captcha y el portal rechazó sus
> tokens. Por eso, hoy, **no se recomienda publicarlo en Lambda**: use el cron
> local (ver más abajo) o vuelva a calentar el perfil en el sistema de destino
> y verifique antes.

El robot corre en **Lambda con imagen de contenedor**, guarda los TXT en S3 y se
dispara con una regla mensual de EventBridge. No usa Secrets Manager: la clave
del portal se pasa como variable de entorno de la función (cifrada en reposo
por Lambda) y el resto de la configuración viaja en la imagen.

```bash
./scripts/publicar.sh \
  --ruc 1758871873001 \
  --ambiente produccion \
  --tipos 01
```

El script pide la clave de forma oculta (o `--password '...'`) y es
**idempotente**:

1. Comprueba las credenciales de AWS.
2. Si la función ya existe, muestra su estado y **no cambia nada**; para
   desplegar una versión nueva hay que añadir `--actualizar`.
3. Si no existe, crea lo mínimo: bucket S3 (privado, cifrado), repositorio ECR,
   rol IAM, función Lambda (`arm64`, 2 GB, 900 s) y regla de EventBridge.

Lo que se publica:

- `deploy/build/perfil`: copia del perfil de Chrome calentado (se quitan los
  `Singleton*` de macOS, que en Linux hacen que Chrome crea que el perfil está
  en uso).
- `deploy/build/config/robots.yaml`: RUC y `password_env: SRI_PASSWORD`.
- Variables de la función: `SRI_PASSWORD`, `SRI_AMBIENTE`, `SRI_BUCKET`.

Frecuencia por defecto: día 5 de cada mes a las 08:00 UTC (03:00 de Ecuador),
descargando el mes anterior. Se cambia con `--cron`.

### Invocación manual

```bash
aws lambda invoke --function-name sri-txt-robot \
  --payload '{"periodos":["2026-07","2026-08"]}' \
  --cli-binary-format raw-in-base64-out out.json && cat out.json
```

El evento acepta `periodos` (lista) o `periodo` (un solo mes). Sin períodos se
usa el mes cerrado anterior.

### Coste

Una ejecución mensual de 2-3 minutos con 2 GB de memoria cuesta menos de un
centavo; el repositorio ECR (~1,5 GB) ronda los 0,15 USD al mes y S3 unos
céntimos. En total, por debajo de 0,30 USD al mes para un contribuyente.

### Alternativa probada: tarea programada en el Mac

Mientras el captcha no se pueda resolver fuera del Mac, la forma de automatizar
sin coste es un **LaunchAgent** (corre en la sesión gráfica del usuario, que es
lo que Chrome necesita) que ejecute el robot y suba los TXT a S3:

```xml
<!-- ~/Library/LaunchAgents/com.ejemplo.sri-txt-robot.plist -->
<key>ProgramArguments</key>
<array>
  <string>/bin/zsh</string>
  <string>-lc</string>
  <string>cd "$HOME/Projects/sri-txt-robot" &amp;&amp; SRI_BUCKET=mi-bucket SRI_AMBIENTE=produccion .venv/bin/sri-txt-robot descargar --config config/robots.yaml</string>
</array>
<key>StartCalendarInterval</key>
<dict><key>Day</key><integer>5</integer><key>Hour</key><integer>8</integer></dict>
```

Con `config/robots.yaml` declarando `password_env: SRI_PASSWORD` y la clave en
el llavero o en un archivo con permisos `600` que se cargue antes de ejecutar.
Si el Mac está dormido, launchd lo despierta o lo ejecuta al volver.

## Integración con `sri-comprobantes-php`

El TXT (o su `.claves.json`) es la entrada. El paquete PHP añade la capacidad
de **consulta masiva**, que es orquestación de consultas unitarias sobre la API
oficial: nunca inventa un endpoint por rango.

```php
use JulioSerpone\SriComprobantes\Consulta\TxtReportParser;
use JulioSerpone\SriComprobantes\Client\AutorizacionClient;
use JulioSerpone\SriComprobantes\Service\ConsultaMasivaService;
use JulioSerpone\SriComprobantes\Storage\FilesystemComprobanteRepository;
use JulioSerpone\SriComprobantes\Transport\SoapTransport;

$claves = TxtReportParser::extraerClavesAcceso(file_get_contents('/ruta/facturas-recibidas-2026-09.txt'));

$servicio = new ConsultaMasivaService(
    autorizacion: new AutorizacionClient(new SoapTransport($wsdl)),
    repositorio: new FilesystemComprobanteRepository('/ruta/xmls'),
);

$informe = $servicio->procesar($claves);

echo $informe->autorizados(), " autorizados de ", $informe->total(), PHP_EOL;
```

Cada XML autorizado queda persistido por el repositorio, y el informe
distingue `AUTORIZADO`, `EN PROCESO` (`PPR`) y `RECHAZADO`/`NAT`, que **no**
se reintentan.

---

## Estructura

```text
src/sri_txt_robot/
├── captcha/       anti-captcha (cliente API) + inyección del token
├── portal/        navegación: login Keycloak, captcha, recibidos, cierre de sesión, navegador
├── config.py      configuración validada con pydantic
├── manifest.py    extracción de claves de acceso (módulo 11 del SRI)
├── models.py      Período, tipo de comprobante, credenciales
├── period.py      años disponibles y período sugerido
├── pipeline.py    orquestación RUC × período × tipo
├── storage.py     persistencia local o S3
└── handler.py     entrada de AWS Lambda
config/
├── robots.example.yaml   configuración con los contribuyentes
└── selectors.yaml        selectores del portal
deploy/
├── Dockerfile            imagen base (Playwright + código)
└── Dockerfile.lambda     imagen final (configuración + perfil calentado)
scripts/
├── instalar.sh           crea el entorno local con un Python >= 3.12
└── publicar.sh           publica en Lambda de forma idempotente
```

## Pruebas

```bash
pytest              # unitarias, sin red
ruff check .
mypy
```

Las pruebas no llaman al SRI ni a anti-captcha: el cliente de captcha se
simula con un doble de `httpx.post`. Para validar contra el portal real, use
el comando `calibrar` con un RUC de pruebas.

## Limitaciones conocidas

- El portal puede cambiar el HTML, los identificadores o la ruta de la
  pantalla. `config/selectors.yaml` existe para amortiguar eso, pero una
  reescritura mayor del portal sí requerirá cambios en el robot.
- Fuera del rango de años que ofrece el portal (`2022`–`2026` en la última
  revisión) la consulta no devuelve datos; el robot lo avisa antes de entrar
  pero no puede ampliar el desplegable.
- La extracción de claves del TXT se basa en el patrón de 49 dígitos con
  verificador módulo 11 (el mismo algoritmo de la ficha técnica v2.34 que usa
  `AccessKeyGenerator`), **no** en posiciones fijas de columna, porque el SRI no
  publica el formato del TXT.
- El robot no descarga los XML: eso lo hace el paquete PHP contra la API
  oficial, una clave a la vez.
- El reCAPTCHA Enterprise está **atado al navegador donde se calentó** el
  perfil. Medido en oct-2026: el mismo perfil copiado a un contenedor Linux
  (con Xvfb, Chrome "con ventana" y la misma IP de salida) es rechazado, y
  anti-captcha tampoco obtiene tokens válidos para este portal. El camino
  probado es ejecutar el robot en el mismo Mac donde se calentó el perfil.

## Consideraciones legales

El robot accede a información **del propio contribuyente** con sus
credenciales. Aun así:

- Los términos de uso del portal del SRI pueden restringir la automatización.
  Valídelo con el SRI y con su área legal antes de operarlo en producción.
- Los XML de terceros (proveedores) contienen datos personales de terceros: el
  paquete PHP recomienda cifrarlos en reposo, controlar su retención (7 años) y
  anonimizar los registros.
- Límite las consultas: el robot ya es idempotente; no aumente la frecuencia
  de accesos al portal por su cuenta.
