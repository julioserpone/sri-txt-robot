# SRI TXT Robot

[![CI](https://github.com/julioserpone/sri-txt-robot/actions/workflows/ci.yml/badge.svg)](https://github.com/julioserpone/sri-txt-robot/actions/workflows/ci.yml)
[![License](https://img.shields.io/badge/license-MIT-blue.svg)](LICENSE)

Robot que entra al portal del SRI, resuelve el reCAPTCHA con **anti-captcha**,
consulta los comprobantes electrónicos **recibidos** y descarga el reporte
**TXT** de cada tipo de comprobante.

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
| **Login** | Keycloak en `/tuportal-internet/`; acción `/auth/realms/Internet/login-actions/authenticate`. Campos `usuario` (RUC), `ciAdicional` (solo RUC de 13 dígitos) y `password`. **El login no tiene reCAPTCHA.** |
| **Sesión** | La app JSF `/comprobantes-electronicos-internet/pages/*.jsf` se abre vía `/tuportal-internet/GeneraToken.jsp?urlAplicacion=…`. |
| **Captcha** | reCAPTCHA **Enterprise**. La clave pública **no** está fija en el HTML: en el portal nuevo se obtiene de `/sri-captcha-servicio-internet/rest/ValidacionCaptcha/obtenerClavePublicaGoogleRecaptcha`. El robot la detecta en tiempo de ejecución. |
| **Pantalla** | *Facturación Electrónica → Comprobantes electrónicos recibidos*. Filtros: *Periodo de emisión* (año, mes, día — con **Todos** para el mes completo) y *Tipo de comprobante*. Acciones: **Consultar** (dispara el captcha) y **Descargar reporte** (entrega el TXT). |
| **Salida** | El TXT lista los comprobantes con su *Clave de acceso (CA)* de 49 dígitos. |
| **Ventana** | El TXT solo se descarga **5 días** tras el cierre del período. |

### La ventana de 5 días (la restriction que manda sobre el diseño)

El SRI solo habilita la descarga del reporte TXT durante los **cinco días**
siguientes al cierre del período consultado. Pasado ese plazo la consulta
responde *"fuera de periodo"* y el TXT ya no está disponible; la alternativa
es descargar los XML individualmente.

Por eso el robot **no es una herramienta bajo demanda**, sino un **trabajo
programado**: el schedule por defecto (`cron(0 9 1-5 * ? *)`) lo ejecuta los
días 1 a 5 de cada mes para el mes cerrado anterior. Ejecutarlo "cuando uno se
acuerde" es tarde.

---

## Decisiones de diseño

### Lenguaje: Python 3.12 + Playwright

| Alternativa | Por qué no |
| --- | --- |
| **PHP** (extensión del paquete) | No hay un controlador de navegador headless en PHP con la madurez de Playwright: `chrome-php/chrome` habla CDP a pelo y Symfony Panther arrastra Symfony. Además, atar el paquete a la UI del portal contradice el ADR 0003. |
| Node / TypeScript | Válido (Playwright es TS-first), pero Python tiene SDK oficial de anti-captcha, mejor tooling de datos para el parseo del TXT y `httpx`/`pydantic` más sobrios para un servicio de esta forma. |
| **Python 3.12 + Playwright** ✅ | Playwright resuelve los problemas reales de una app JSF: estado de vista, sesión y descarga de archivos. Auto-espera, sin *sleeps*. El SDK oficial de anti-captcha. Y `boto3`/`httpx`/`pydantic` cubren el resto sin tirar de frameworks. |

El paquete PHP **no se toca** para esto: sigue siendo la capa de integración
con los WSDL oficiales. La frontera entre ambos es un archivo (el TXT o su
manifiesto JSON de claves).

### Runtime: AWS Lambda (imagen de contenedor) + EventBridge

- El trabajo es **mensual y a ráfagas**: Lambda encaja exactamente en ese
  perfil. 12 invocaciones al año cuestan céntimos, frente a ~USD 15-20/mes de
  una instancia EC2 siempre encendida.
- Playwright necesita Chromium (~150 MB) y sus dependencias de sistema → **imagen
  de contenedor**, no un ZIP. La imagen se construye sobre
  `public.ecr.aws/lambda/python:3.12` (Amazon Linux 2023, glibc 2.35) con las
  dependencias de sistema instaladas explícitamente con `dnf` y el navegador
  descargado con `playwright install chromium`. Dos trampas: la base de Lambda
  usa `dnf` y **no** `apt-get`, así que `playwright install --with-deps` falla;
  y la imagen oficial de Playwright está basada en Noble (glibc 2.39), que **no**
  es compatible con el runtime de Lambda.
- Se reutiliza el stack que ya usas: SAM (`infra/template.yaml`), Secrets
  Manager para las claves y S3 para los resultados.
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
  no iniciar sesión en cada ejecución.

---

## Puesta en marcha

### 1. Requisitos

- Python 3.12 o superior.
- [Playwright](https://playwright.dev/python/) con Chromium.
- Una cuenta de [anti-captcha](https://anti-captcha.com/) con saldo.

```bash
cd sri-txt-robot
python3.12 -m venv .venv
source .venv/bin/activate
pip install -e ".[dev]"
playwright install chromium
```

### 2. Configuración

```bash
cp config/robots.example.yaml config/robots.yaml
```

Edite `config/robots.yaml`:

- `captcha.api_key_env`: variable de entorno con la clave de anti-captcha
  (por defecto `ANTICAPTCHA_API_KEY`).
- `storage.local_dir`: dónde guardar los TXT en local (`.` bucket si prefiere
  S3).
- `robots[]`: un RUC por contribuyente, con `password_env` apuntando a la
  variable que contiene su clave del portal.

```bash
export ANTICAPTCHA_API_KEY="..."
export SRI_PASSWORD_1790012345001="..."
```

Los secretos **nunca** se escriben en el YAML versionado: use `password_env` o
la forma `${VARIABLE}`.

### 3. Calibrar contra el portal real

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
pantalla, rellene `consulta.url_directa` con la URL directa de la pantalla de
comprobantes recibidos.

### 4. Ejecutar

```bash
# Período sugerido (mes anterior), todos los RUC y todos los tipos
sri-txt-robot descargar --config config/robots.yaml

# Un RUC, un tipo y un período concretos
sri-txt-robot descargar -r 1790012345001 -t 01 -p 2026-09

# Ver la ventana de descarga y el período recomendado
sri-txt-robot periodo

# Ver el saldo de anti-captcha
sri-txt-robot saldo

# Extraer las claves de un TXT ya descargado
sri-txt-robot claves datos/1790012345001/2026-09/comprobantes-recibidos-01.txt
```

### 5. Resultado

```text
datos/
└── 1790012345001/
    └── 2026-09/
        ├── comprobantes-recibidos-01.txt          # TXT del portal
        ├── comprobantes-recibidos-01.claves.json  # claves extraídas
        └── manifest.json                           # resumen del período
```

El `.claves.json` es el contrato de entrada para el paquete PHP:

```json
{
  "ruc": "1790012345001",
  "periodo": "2026-09",
  "tipo": "01",
  "total": 128,
  "clavesAcceso": ["1509202601179001234500120010010000001231234567812"]
}
```

---

## Despliegue en AWS

```bash
# 1. Construir y publicar la imagen
aws ecr create-repository --repository-name sri-txt-robot
docker build -t sri-txt-robot .
docker tag sri-txt-robot:latest <cuenta>.dkr.ecr.<region>.amazonaws.com/sri-txt-robot:latest
docker push <cuenta>.dkr.ecr.<region>.amazonaws.com/sri-txt-robot:latest

# 2. Crear el secreto con las claves del portal
aws secretsmanager create-secret --name sri/txt-robot/credenciales \
  --secret-string '{"1790012345001":"clave-del-portal"}'

# 3. Desplegar
sam build --use-container && sam deploy --guided
```

La plantilla `infra/template.yaml` crea el bucket, la función, la regla de
EventBridge, el log group y los permisos de IAM (solo `s3:PutObject` sobre el
bucket y `secretsmanager:GetSecretValue` sobre el secreto).

### Invocación manual

```bash
aws lambda invoke --function-name sri-txt-robot-prod \
  --payload '{"periodo":"2026-09"}' --cli-binary-format raw-in-base64-out out.json
```

---

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

$claves = TxtReportParser::extraerClavesAcceso(file_get_contents('/ruta/comprobantes-recibidos-01.txt'));

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
├── portal/        navegación: login Keycloak, pantalla de recibidos, sesión
├── config.py      configuración validada con pydantic
├── manifest.py    extracción de claves de acceso (módulo 11 del SRI)
├── models.py      Período, tipo de comprobante, credenciales
├── period.py      ventana de descarga de 5 días
├── pipeline.py    orquestación RUC × período × tipo
├── storage.py     persistencia local o S3
└── handler.py     entrada de AWS Lambda
config/
├── robots.example.yaml   configuración con los contribuyentes
└── selectors.yaml        selectores del portal
infra/template.yaml       plantilla SAM
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
- Fuera de la ventana de 5 días el TXT no está disponible; el robot lo avisa
  pero no puede resolverlo.
- La extracción de claves del TXT se basa en el patrón de 49 dígitos con
  verificador módulo 11 (el mismo algoritmo de la ficha técnica v2.34 que usa
  `AccessKeyGenerator`), **no** en posiciones fijas de columna, porque el SRI no
  publica el formato del TXT.
- El robot no descarga los XML: eso lo hace el paquete PHP contra la API
  oficial, una clave a la vez.

## Consideraciones legales

El robot accede a información **del propio contribuyente** con sus
credenciales. Aun así:

- Los términos de uso del portal del SRI pueden restringir la automatización.
  Valídelo con el SRI y con su área legal antes de operarlo en producción.
- Los XML de terceros (proveedores) contienen datos personales de terceros: el
  paquete PHP recomienda cifrarlos en reposo, controlar su retención (7 años) y
  anonimizar los registros.
- Límite las consultas: el robot ya es idempotente y respeta la ventana del
  portal; no aumente la frecuencia de accesos por su cuenta.
