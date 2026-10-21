# Política de seguridad

## Estado

Este proyecto automatiza el acceso de un contribuyente a **sus propios**
comprobantes en el portal del SRI. No está afiliado ni respaldado por el SRI.

Las credenciales del portal y del servicio de resolución de captcha son
secretos del operador. El repositorio no debe contener nunca RUC, claves,
certificados, XML reales ni datos personales de contribuyentes.

## Reportar una vulnerabilidad

**No abras un issue público.** Usa el reporte privado de vulnerabilidades de GitHub:

1. Ve a la pestaña **Security** del repositorio.
2. Selecciona **Advisories** → **Report a vulnerability**.
3. Incluye:
   - Versión o commit afectado.
   - Descripción del impacto.
   - Pasos de reproducción mínimos y seguros.

No envíes claves privadas, contraseñas de contribuyentes, XML reales ni datos
de contribuyentes. Si el reporte requiere material sensible, coordina primero
un canal seguro.

## Alcance

Este proyecto maneja secretos de terceros y datos fiscales. En particular:

| Área | Medida |
| --- | --- |
| Clave de anti-captcha | Solo por variable de entorno o Secrets Manager. Nunca en el YAML versionado. |
| Clave del portal del SRI | Solo por variable de entorno o Secrets Manager. |
| TXT y XML descargados | Cifrados en reposo (SSE-S3) y permisos 0600 en local. |
| Logs | No incluyen credenciales, tokens de captcha ni el contenido de los TXT. |
| Selectores y configuración | Sin datos de contribuyentes; el RUC va en `config/robots.yaml`, que está en `.gitignore`. |

## Recomendaciones para el operador

- Restrinja el acceso al bucket de S3 y al secreto de Secrets Manager.
- Cifre el bucket con una CMK de KMS si la política lo exige.
- Conserve los TXT y XML solo el tiempo necesario para el proceso contable.
  La retención legal de los comprobantes es de 7 años, pero el reporte TXT es
  un archivo intermedio: consúltelo y elimínelo.
- Anonimice los registros de ejecución.
