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

El trabajo suele ser mensual (EventBridge en el modo estático; en el modo
dinámico lo agenda la aplicación que invoca), pero ya no es obligatorio
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

### Runtime: AWS Lambda (ZIP + runtime en S3)

- El trabajo es **mensual y a ráfagas**: Lambda encaja exactamente en ese
  perfil. 12 invocaciones al año cuestan céntimos, frente a ~USD 15-20/mes de
  una instancia EC2 siempre encendida.
- El paquete es un **ZIP** de ~51 MB (149 MB descomprimido), no una imagen de
  contenedor. Chromium, Xvfb, sus dependencias de sistema y el perfil calentado
  viven en un prefijo privado de S3 y se extraen a `/tmp` **solo cuando la
  invocación tiene descargas pendientes**. Así no hace falta ECR y el
  almacenamiento baja a ~0,25 GB.
- Los artefactos se construyen con Docker sobre la misma base AL2023 del
  runtime gestionado (`scripts/construir_zip.sh`), para que el binario de
  Chromium y sus librerías sean compatibles con Lambda. El bundle incluye
  `xkbcomp`, `xkeyboard-config` y los módulos PKCS#11 de NSS que Chromium
  necesita.
- **Xvfb** se arranca de forma perezosa, justo antes de abrir Chrome, y este
  corre "con ventana": en headless se anuncia como `HeadlessChrome` y el portal
  no responde.
- **Credenciales sin Secrets Manager**: en el modo estático la clave del portal
  va en una variable de entorno de la función (cifrada en reposo por Lambda).
  En el modo dinámico cada invocación trae un sobre `sealed_credentials`
  cifrado con la clave pública del robot; la Lambda solo guarda la clave
  privada. Los resultados van a S3.
- El despliegue lo hace `scripts/publicar_zip.sh` (sube bundle, perfil y ZIP a
  S3 y crea o actualiza la función por referencia), no una plantilla SAM.
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
- Docker, solo para construir los artefactos del despliegue ZIP
  (`scripts/construir_zip.sh`).
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

El RUC puede ser la **cédula de 10 dígitos**: el robot la completa con `001`
para el login y acepta tanto la cédula como el RUC de 13 dígitos en el filtro
de consulta del portal.

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

# Generar el par de claves para credenciales selladas (una sola vez)
sri-txt-robot generar-claves \
  --publica ~/.sri-txt-robot/publica.b64 \
  --privada ~/.sri-txt-robot/privada.b64

# Generar el sobre que va en sealed_credentials (pregunta la clave oculta)
sri-txt-robot sellar-credenciales \
  --ruc 1758871873001 --public-key ~/.sri-txt-robot/publica.b64
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
            ├── facturas-recibidas-2026-08-xml/
            │   └── <claveAcceso>.xml
            └── manifest.json
```

La clave completa es:

```text
<ambiente>/<ruc>/<periodo>/<tipo>-recibidas-<periodo>.txt
<ambiente>/<ruc>/<periodo>/<tipo>-recibidas-<periodo>-claves.json
<ambiente>/<ruc>/<periodo>/<tipo>-recibidas-<periodo>-xml/<claveAcceso>.xml
<ambiente>/<ruc>/<periodo>/manifest.json
```

### XML por comprobante

Con `--descargar-xml` (por defecto) el robot, una vez enviada la consulta,
recorre la tabla de comprobantes fila por fila y descarga el XML de cada uno,
pasando el paginador si hay más de una página. Cada XML se guarda como
`<claveAcceso>.xml` dentro de la carpeta `*-xml/` del tipo.

- Es **idempotente**: si el XML ya existe no se vuelve a pedir, así que se
  puede reintentar sin gastar anti-captcha de más.
- Un fallo en una fila no detiene el proceso ni impide entregar el TXT; queda
  registrado en `fallos` del resumen con `ruc`, `periodo`, `tipo`, `pagina`.
- Si el TXT del período ya está descargado, el robot **no vuelve a bajar el
  TXT**: envía la consulta para dejar la tabla en pantalla y solo descarga los
  XML que falten. Use `--no-descargar-xml` para recuperar el comportamiento
  anterior (omitir por completo lo que ya existe).
- `--forzar` re-descarga también el TXT.

```bash
sri-txt-robot descargar -p 2026-07 --descargar-xml        # TXT + XML (por defecto)
sri-txt-robot descargar -p 2026-07 --no-descargar-xml     # solo TXT
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

> **Estado (oct-2026): validado de extremo a extremo en Lambda (ZIP).**
> El captcha lo resuelve el propio navegador, sin anti-captcha: 44 claves en
> 2026-09 y 24 en 2026-08, en ejecuciones de 29-60 s. La clave está en calentar
> el perfil **dentro de Linux** (el perfil calentado en macOS no transfiere la
> confianza) y en lanzar Chrome con `--no-zygote`, porque el sandbox de Lambda
> no permite el `fork` del zygote. La función de producción `sri-txt-robot`
> corre como ZIP arm64, 3008 MB y 900 s.

El robot corre en **Lambda con paquete ZIP** (el navegador se sirve desde S3),
guarda los TXT en S3 y tiene dos modos de credenciales:

- **Estático:** un RUC fijo en la configuración y `SRI_PASSWORD` en la función.
  La regla mensual de EventBridge puede ejecutarlo.
- **Dinámico:** `robots: []`, sin `SRI_PASSWORD`; cada invocación trae el RUC y
  la clave cifrados en `sealed_credentials`. Es el modo de producción: sirve
  para varios RUC sin recompilar y sin Secrets Manager. No se usa la regla
  mensual (EventBridge no puede generar sobres), así que la aplicación que
  invoca decide cuándo. La regla `sri-txt-robot-mensual` sigue creada pero
  **deshabilitada**.

### Modo estático (heredado, solo imagen)

> **Obsoleto.** `scripts/publicar.sh` crea una imagen de contenedor y un
> repositorio ECR; el flujo de producción es ZIP (vea
> [Despliegue ZIP](#despliegue-zip-recomendado)). El script se niega a
> ejecutarse salvo que exporte `SRI_PERMITIR_PUBLICAR_IMAGEN=1`.

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
   rol IAM, función Lambda (`arm64`, 3008 MB, 900 s) y regla de EventBridge.

Lo que publica:

- `deploy/build/perfil`: copia del perfil de Chrome calentado (se quitan los
  `Singleton*`, que en Linux hacen que Chrome crea que el perfil está en uso).
- `deploy/build/config/robots.yaml`: RUC y `password_env: SRI_PASSWORD`.
- Variables de la función: `SRI_PASSWORD`, `SRI_AMBIENTE`, `SRI_BUCKET`.

### Credenciales selladas por invocación

Este modo permite atender **varios RUC con una sola Lambda** sin volver a
desplegar y sin guardar la contraseña del portal en la función. Cada invocación
lleva un RUC (puede incluir varios `periodos`); para varios contribuyentes, la
aplicación sella e invoca una vez por RUC. La aplicación que invoca cifra un
pequeño JSON con la clave **pública** del robot usando
`sodium_crypto_box_seal`; la Lambda lo abre con su clave **privada**.

Genere el par una sola vez y guarde la clave privada fuera del repositorio:

```bash
sri-txt-robot generar-claves \
  --publica ~/.sri-txt-robot/publica.b64 \
  --privada ~/.sri-txt-robot/privada.b64
```

- `SRI_ROBOT_PUBLIC_KEY`: clave pública en la aplicación que invoca (por
  ejemplo `autotributo`). Puede compartirse.
- `SRI_CREDENTIALS_PRIVATE_KEY`: clave privada en la Lambda. No la versiones,
  no la pases por argumentos si puedes usar un archivo y no la registres.

Despliegue dinámico (ZIP):

```bash
AWS_PROFILE=sri-robot ./scripts/publicar_zip.sh \
  --funcion sri-txt-robot \
  --private-key-file ~/.sri-txt-robot/privada.b64 \
  --env-file ~/.sri-txt-robot/lambda.env \
  --actualizar
```

El script genera `robots: []`, configura solo `SRI_CREDENTIALS_PRIVATE_KEY`,
`SRI_AMBIENTE` y `SRI_BUCKET` (más lo que traiga `--env-file`) y **no** crea la
regla mensual. Toma el perfil de `dist/runtime/perfil.tar.gz` y el bundle de
`dist/runtime/`; ambos se generan con `scripts/construir_zip.sh`. La región por
defecto es `us-east-2`. Si sus credenciales están en un perfil distinto de
`default`, exporte `AWS_PROFILE=...` antes de ejecutar.

El evento tiene este contrato:

```json
{
  "periodo": "2026-09",
  "ruc": "1758871873001",
  "tipos": ["01", "07"],
  "sealed_credentials": "<base64 del sobre>"
}
```

El contenido en claro del sobre es:

```json
{
  "ruc": "1758871873001",
  "password": "clave del portal",
  "claveAdicional": "",
  "iat": 1760000000
}
```

`ruc` se repite dentro del sobre para detectar eventos mezclados: si el evento
trae un `ruc` exterior, debe coincidir con el interior; si se omite, se usa el
del sobre. El `ruc` exterior, cuando existe, es el que filtra la ejecución.
`claveAdicional` e `iat` son opcionales; `iat` es la marca UNIX de cuando se
selló.

Desde PHP el sobre se genera así:

```php
$contenido = json_encode([
    'ruc' => $ruc,
    'password' => $password,
    'claveAdicional' => $claveAdicional,
    'iat' => time(),
], JSON_UNESCAPED_UNICODE);

$sealed = base64_encode(
    sodium_crypto_box_seal($contenido, base64_decode($publicKey, true))
);
```

Si el sobre incluye `iat`, la Lambda acepta una antigüedad de hasta 24 horas
por defecto, para cubrir los reintentos asíncronos. Se cambia con
`SRI_CREDENTIALS_MAX_AGE_SECONDS` (segundos); `0` desactiva la comprobación y
un sobre sin `iat` tampoco caduca. Durante una rotación de claves puede
mantener la anterior en `SRI_CREDENTIALS_PRIVATE_KEY_ANTERIOR`: el robot prueba
primero la actual y luego la anterior.

Para probar el sobre sin invocar el portal:

```bash
sri-txt-robot sellar-credenciales \
  --ruc 1758871873001 \
  --public-key ~/.sri-txt-robot/publica.b64
```

El comando pide la contraseña de forma oculta y escribe el base64 que va en
`sealed_credentials`. No registre el evento completo: contiene la contraseña
cifrada y el sobre podría reutilizarse mientras no caduque.

### Despliegue ZIP (recomendado)

Este es el despliegue de producción: un **ZIP** sin repositorio ECR. El
navegador no cabe en el paquete (Lambda limita el ZIP a 250 MB sin comprimir),
así que Chromium, Xvfb, las librerías y el perfil se guardan en un prefijo
privado del bucket y se extraen en `/tmp` **solo cuando la invocación tiene
descargas pendientes**.

Construir los artefactos (Docker, misma base AL2023 que Lambda):

```bash
./scripts/construir_zip.sh --ambiente produccion
```

Genera:

| Artefacto | Tamaño |
|---|---:|
| `dist/runtime/browser-<arch>-<playwright>-<chromium>.tar.gz` | ~242 MB |
| `dist/runtime/perfil.tar.gz` | ~21 MB |
| `dist/funcion.zip` (149 MB descomprimido) | ~51 MB |

El bundle incluye lo que el runtime de Lambda no trae y Chromium/Xvfb
necesitan sin red: `xkeyboard-config`, el binario `xkbcomp` y los módulos
PKCS#11 de NSS (`libsoftokn3`, `libfreebl3`, `libnssckbi`…). El `Xvfb` del
bundle se compila con `/usr/bin` fijo para invocar `xkbcomp`; como en Lambda
ese directorio es de solo lectura, el build parchea ese literal a `/tmp/xkb` y
`sri_txt_robot.runtime` enlaza ahí el `xkbcomp` incluido. Sin los módulos NSS,
Chrome aborta con `FATAL:crypto/nss_util.cc nss_error=-5925`.

Publicar una función de prueba (no toca producción):

```bash
AWS_PROFILE=sri-robot ./scripts/publicar_zip.sh --funcion sri-txt-robot-zip
```

Actualizar la función de producción (mismo nombre, mismo rol y misma clave
privada que la función existente):

```bash
AWS_PROFILE=sri-robot ./scripts/publicar_zip.sh \
  --funcion sri-txt-robot \
  --env-file ~/.sri-txt-robot/lambda.env \
  --actualizar
```

Con callback firmado, el secreto se pasa por archivo para no dejarlo en el
historial:

```bash
AWS_PROFILE=sri-robot ./scripts/publicar_zip.sh \
  --funcion sri-txt-robot \
  --callback-url https://app.ejemplo.com/api/v1/webhooks/sri-robot \
  --callback-secret-file ~/.sri-txt-robot/callback.secret \
  --actualizar
```

Producción todavía no tiene `SRI_CALLBACK_URL`: hasta que exista una URL
pública de staging, omita estas dos opciones y use la conciliación por S3.

El script sube el bundle, el perfil y el propio ZIP a `_runtime/` y crea la
función por referencia a S3: el paquete supera los 50 MB y `CreateFunction`
rechaza el envío directo (el cuerpo en base64 supera el límite de 70 MB).

Para secretos adicionales, como la clave de anti-captcha, use `--env-file` con
un archivo `KEY=VALUE` fuera del repositorio:

```bash
AWS_PROFILE=sri-robot ./scripts/publicar_zip.sh \
  --funcion sri-txt-robot-zip \
  --env-file ~/.sri-txt-robot/lambda.env
```

Variables que configura el script:

| Variable | Uso |
|---|---|
| `SRI_RUNTIME_BUCKET`, `SRI_RUNTIME_BROWSER_KEY/SHA256` | Bundle del navegador. |
| `SRI_RUNTIME_PERFIL_KEY/SHA256` | Perfil calentado. |
| `SRI_ROBOT_CONFIG`, `SRI_ROBOT_SELECTORS` | Configuración bajo `/var/task/config`. |
| `DISPLAY`, `SRI_ASEGURAR_XVFB`, `SRI_CHROME_BANDERAS` | Xvfb y banderas de Chrome. |

El bootstrap verifica el SHA-256 antes de extraer y es atómico: si falla, no
deja un runtime a medias. El código está en `sri_txt_robot.runtime`.

Ventaja: ECR desaparece y el almacenamiento pasa a ~0,25 GB en S3
(~0,006 USD/mes). Coste: la primera invocación con trabajo paga la descarga y
extracción (~30-60 s); las siguientes reutilizan `/tmp` mientras el sandbox
siga caliente. Una invocación sin trabajo responde en segundos porque no
materializa nada.

Validado en AWS (oct-2026): la función de producción `sri-txt-robot` (arm64,
3008 MB, ZIP) descargó 44 facturas de 2026-09 con el captcha resuelto por el
navegador en ~32 s, usando credenciales selladas por invocación. En la función
de prueba el bootstrap descargó y verificó el bundle (254 MB, 1,5 s) y el
perfil, extrajo ambos en ~6 s y Xvfb arrancó con el keymap del bundle. El
callback `failed` llegó firmado al endpoint de `autotributo` con `202` en el
primer intento durante esa validación. La URL pública de callback todavía no
está configurada en producción; actualícela con `--actualizar` cuando exista.

### Calentar el perfil dentro de Linux

El perfil calentado en macOS **no** sirve en Linux: el portal rechaza el
reCAPTCHA porque la confianza está atada al navegador donde se resolvió. Hay que
calentarlo una vez dentro del mismo contenedor que se usa en Lambda:

```bash
# Imagen con VNC sobre la imagen de Playwright
docker build -f deploy/Dockerfile.calentar -t sri-txt-robot:calentar .

# Página de consulta lista y esperando el clic manual
docker run --rm -p 127.0.0.1:5900:5900 \
  -v "$PWD/deploy/build/perfil:/perfil" \
  --entrypoint bash sri-txt-robot:calentar -lc '
    Xvfb :99 -screen 0 1440x900x24 >/tmp/xvfb.log 2>&1 &
    x11vnc -display :99 -forever -shared -passwd srirobot-2026 >/tmp/x11vnc.log 2>&1 &
    export DISPLAY=:99; sleep 2
    exec python -m sri_txt_robot calentar \
      --perfil /perfil --ruc "$RUC" --password "$SRI_PASSWORD" --espera 5400'
```

Desde el Finder: `Cmd+K` → `vnc://127.0.0.1:5900`, contraseña `srirobot-2026`.
Elija un mes cerrado, día **Todos**, pulse **Consultar** y espere la tabla. El
comando termina solo y deja el perfil en `deploy/build/perfil`; después:

```bash
./scripts/construir_zip.sh --ambiente produccion
AWS_PROFILE=sri-robot ./scripts/publicar_zip.sh \
  --funcion sri-txt-robot \
  --env-file ~/.sri-txt-robot/lambda.env \
  --actualizar
```

Solo hay que repetirlo si el portal vuelve a rechazar el captcha (por ejemplo,
tras muchos meses o un cambio de infraestructura). Guarde el perfil fuera del
repositorio (por ejemplo `~/.sri-txt-robot/perfil-linux`) y pase esa ruta a
`--perfil`; contiene cookies de sesión y no debe versionarse.

Tres detalles del entorno Lambda que el despliegue ya resuelve:

- `/var/task` es de solo lectura: el bootstrap extrae el perfil y el navegador
  a `/tmp` (no en el arranque, que tiene un límite de 10 s), y solo cuando hay
  descargas pendientes.
- Xvfb se arranca desde el handler y se comprueba que su socket acepta
  conexiones. Así no se reutiliza un socket huérfano si Lambda congela o
  reinicia el sandbox entre invocaciones.
- Chrome se lanza con `--no-zygote` (`SRI_CHROME_BANDERAS` en la función): sin
  esa bandera el sandbox de Lambda aborta con `Zygote could not fork`.

En el **modo estático** (solo imagen), la frecuencia por defecto es el día 5 de
cada mes a las 13:00 UTC (08:00 de Ecuador), descargando el mes anterior. Se
cambia con `--cron`. En el **modo dinámico**, que es el de producción, no se
usa la regla mensual: la aplicación que invoca decide la frecuencia. La regla
`sri-txt-robot-mensual` sigue creada pero deshabilitada.

### Invocación manual

Modo estático:

```bash
aws lambda invoke --function-name sri-txt-robot \
  --payload '{"periodos":["2026-07","2026-08"]}' \
  --cli-binary-format raw-in-base64-out out.json && cat out.json
```

Modo dinámico: agregue `ruc` y `sealed_credentials` al payload. El sobre se
puede generar en el momento (el comando pregunta la contraseña de forma oculta):

```bash
SOBRE="$(sri-txt-robot sellar-credenciales \
  --ruc 1758871873001 --public-key "$SRI_ROBOT_PUBLIC_KEY")"

aws lambda invoke --function-name sri-txt-robot \
  --payload "{\"periodo\":\"2026-09\",\"ruc\":\"1758871873001\",\"sealed_credentials\":\"$SOBRE\"}" \
  --cli-binary-format raw-in-base64-out out.json && cat out.json
```

El evento acepta `periodos` (lista) o `periodo` (un solo mes). Sin períodos se
usa el mes cerrado anterior.

### Callback firmado

La invocación asíncrona (`InvocationType=Event`) solo devuelve un `RequestId`.
Para saber si la descarga terminó, el robot puede enviar un `POST` firmado a un
endpoint propio (por ejemplo `POST /webhooks/sri-robot` de `autotributo`).

El evento admite dos campos opcionales:

```json
{
  "periodo": "2026-09",
  "event_id": "01J9Z8Q7K3",
  "callback_url": "https://app.ejemplo.com/webhooks/sri-robot",
  "sealed_credentials": "..."
}
```

- `event_id` correlaciona la invocación con el callback. Si no llega, el robot
  genera `sri-<uuid>`.
- `callback_url` solo se acepta si su host está en
  `SRI_CALLBACK_ALLOWED_HOSTS`. Si no, se usa `SRI_CALLBACK_URL`.

Variables de entorno de la función:

| Variable | Uso |
|---|---|
| `SRI_CALLBACK_URL` | URL por defecto. Debe ser `https` (solo se admite `http` en localhost). |
| `SRI_CALLBACK_SECRET` | Secreto compartido, mínimo 16 caracteres. |
| `SRI_CALLBACK_ALLOWED_HOSTS` | Hosts separados por coma que pueden venir en `callback_url`. |
| `SRI_CALLBACK_TIMEOUT_SECONDS` | Timeout por intento (10 s por defecto). |
| `SRI_CALLBACK_ATTEMPTS` | Intentos totales (3 por defecto). |
| `SRI_CALLBACK_BACKOFF_SECONDS` | Espera base; se duplica en cada reintento (1 s por defecto). |

Cabeceras de la petición:

```text
Content-Type: application/json
X-Sri-Event-Id: 01J9Z8Q7K3
X-Sri-Timestamp: 1792791111
X-Sri-Signature: sha256=<hex>
X-Sri-Delivery-Attempt: 1
```

La firma es `HMAC-SHA256(secreto, "{timestamp}.{cuerpo}")`, con el cuerpo
**exacto** que se envía (bytes, sin reformatear). Cuerpo:

```json
{
  "schema_version": 1,
  "event_id": "01J9Z8Q7K3",
  "event_type": "sri.robot.finished",
  "timestamp": 1792791111,
  "status": "completed",
  "ambiente": "produccion",
  "ruc": "1790012345001",
  "periodos": ["2026-09"],
  "request_id": "5f3c...",
  "started_at": "2026-10-26T18:00:00+00:00",
  "finished_at": "2026-10-26T18:01:30+00:00",
  "duration_ms": 90000,
  "result": {
    "descargados": [],
    "omitidos": [],
    "sinDatos": [],
    "fallos": [],
    "totalClaves": 0
  },
  "error": null
}
```

`status` es `completed` (sin fallos), `partial` (hubo fallos pero también
progreso) o `failed` (no se descargó nada o la invocación lanzó una excepción).
El callback se intenta también cuando no hay trabajo pendiente, para que quien
invoca pueda cerrar su ejecución.

El receptor debe:

1. Verificar la firma en tiempo constante y rechazar si no coincide.
2. Rechazar un `timestamp` fuera de una ventana (por ejemplo ±5 minutos).
3. Deducir por `event_id` y responder `202` sin reprocesar.
4. Responder rápido: el robot reintenta ante error de red, `408`, `425`, `429`
   y `5xx`. Un `4xx` distinto de esos no se reintenta.

Ejemplo de verificación en PHP:

```php
$cuerpo = $request->getContent();
$timestamp = (int) $request->header('X-Sri-Timestamp');
$firma = hash_hmac('sha256', $timestamp.'.'.$cuerpo, config('sri.robot.callback_secret'));

if (! hash_equals('sha256='.$firma, (string) $request->header('X-Sri-Signature'))) {
    abort(401, 'Firma inválida.');
}

if (abs(time() - $timestamp) > 300) {
    abort(401, 'Callback caducado.');
}
```

Si `SRI_CALLBACK_URL` y `SRI_CALLBACK_SECRET` no están definidos, el robot no
envía nada y la respuesta incluye solo el resumen (comportamiento anterior).

### Coste

Una ejecución mensual de ~1 minuto con 3 GB de memoria cuesta menos de un
céntimo. Sin ECR, el almacenamiento se reduce al bundle, el perfil y los TXT en
S3: ~0,25 GB, unos 0,006 USD al mes. En total, muy por debajo de 0,05 USD al
mes para un contribuyente.

### Alternativa: tarea programada en el Mac

Si prefiere no depender de AWS, un **LaunchAgent** (corre en la sesión gráfica
del usuario, que es lo que Chrome necesita) puede ejecutar el robot y subir los
TXT a S3:

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

### Identidad que invoca la función

El despliegue se hizo con el usuario
`arn:aws:iam::076767082111:user/sri-txt-robot-deployer`, que tiene
`AWSLambda_FullAccess`. Esa identidad es solo para desplegar: **no** debe
usarse como credencial de la aplicación.

La identidad de ejecución de `autotributo` necesita invocar la función y leer
la salida del robot. Política mínima (ajuste el prefijo si define
`SRI_ROBOT_OUTPUT_PREFIX`):

```json
{
  "Version": "2012-10-17",
  "Statement": [
    {
      "Sid": "InvocarRobotSri",
      "Effect": "Allow",
      "Action": "lambda:InvokeFunction",
      "Resource": "arn:aws:lambda:us-east-2:076767082111:function:sri-txt-robot"
    },
    {
      "Sid": "LeerSalidaRobotSri",
      "Effect": "Allow",
      "Action": "s3:GetObject",
      "Resource": "arn:aws:s3:::sri-txt-robot-076767082111-produccion/produccion/*"
    },
    {
      "Sid": "ListarSalidaRobotSri",
      "Effect": "Allow",
      "Action": "s3:ListBucket",
      "Resource": "arn:aws:s3:::sri-txt-robot-076767082111-produccion",
      "Condition": {
        "StringLike": {"s3:prefix": ["produccion/*"]}
      }
    }
  ]
}
```

Aplíquela como política inline del usuario o rol que usa la aplicación (por
ejemplo con `aws iam put-user-policy`). No hace falta `s3:PutObject`: el único
que escribe en el bucket es el rol de la Lambda.

Ajustes de `autotributo` para apuntar a producción:

```dotenv
SRI_ROBOT_DRIVER=lambda
SRI_ROBOT_FUNCTION=sri-txt-robot
SRI_ROBOT_REGION=us-east-2
SRI_ROBOT_ENVIRONMENT=produccion
SRI_ROBOT_OUTPUT_BUCKET=sri-txt-robot-076767082111-produccion
SRI_ROBOT_PUBLIC_KEY=<clave pública del robot>
```

`SRI_ROBOT_CALLBACK_URL` y `SRI_ROBOT_CALLBACK_SECRET` quedan pendientes hasta
que exista una URL pública de staging; mientras tanto, la conciliación por S3
(F2-05) cubre el resultado.

---

## Estructura

```text
src/sri_txt_robot/
├── captcha/       anti-captcha (cliente API) + inyección del token
├── portal/        navegación: login Keycloak, captcha, recibidos, cierre de sesión, navegador
├── callback.py    callback firmado (HMAC) y reintentos
├── config.py      configuración validada con pydantic
├── credentials.py credenciales selladas (sodium_crypto_box_seal)
├── manifest.py    extracción de claves de acceso (módulo 11 del SRI)
├── models.py      Período, tipo de comprobante, credenciales
├── period.py      años disponibles y período sugerido
├── pipeline.py    orquestación RUC × período × tipo
├── runtime.py     bootstrap desde S3 a /tmp (bundle y perfil)
├── storage.py     persistencia local o S3
└── handler.py     entrada de AWS Lambda
config/
├── robots.example.yaml   configuración con los contribuyentes
└── selectors.yaml        selectores del portal
deploy/
├── Dockerfile.artefactos construye el bundle del navegador (AL2023)
├── Dockerfile.calentar   contenedor con VNC para calentar el perfil
├── Dockerfile            imagen base (flujo heredado con ECR)
└── Dockerfile.lambda     imagen final (flujo heredado con ECR)
scripts/
├── instalar.sh           crea el entorno local con un Python >= 3.12
├── construir_zip.sh      genera bundle, perfil y dist/funcion.zip
├── publicar_zip.sh       despliega el ZIP (producción)
└── publicar.sh           flujo heredado con imagen/ECR (obsoleto)
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
