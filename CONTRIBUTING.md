# Cómo contribuir

Gracias por querer ayudar. Este proyecto es una herramienta operativa: los
cambios que tocan el portal del SRI tienen más riesgo de romperse que el
código común, así que hay un par de reglas específicas.

## Puesta en marcha

```bash
python3.12 -m venv .venv
source .venv/bin/activate
pip install -e ".[dev]"
playwright install chromium
```

## Comandos

```bash
make check      # ruff + mypy + pytest
make test       # solo pruebas
make lint       # solo ruff y mypy
make format     # corrige el estilo
```

Las pruebas **no** llaman al SRI ni a anti-captcha: el cliente de captcha se
simula con un doble de `httpx.post`. Para validar contra el portal real use
`make robot ARGS="calibrar --ruc <ruc>"` con un RUC de pruebas.

## Reglas del proyecto

1. **Documentación y comentarios en español técnico.** Los nombres de clases,
   funciones y variables, en inglés o español, pero consistentes dentro de un
   archivo.

2. **Los selectores del portal van en `config/selectors.yaml`,** no en el
   código. Si el portal cambia, se ajusta el YAML. Cada entrada es una lista
   de alternativas y se usa la primera que resuelva a un elemento visible.

3. **Cite la fuente y la fecha** de cualquier afirmación sobre el SRI: manual
   de comprobantes electrónicos, ficha técnica, o la URL del portal con la
   fecha de verificación. Distinga lo **confirmado**, lo **inferido** y lo
   que queda por **validar**.

4. **Nunca un secreto en el repositorio.** RUC, claves del portal y clave de
   anti-captcha viajan por variable de entorno o Secrets Manager. El archivo
   `config/robots.yaml` está en `.gitignore`.

5. **El robot es idempotente.** No duplique descargas ya hechas ni amplíe el
   número de consultas al portal por su cuenta. El portal no impone una ventana
   de días, pero cada consulta cuesta un captcha y golpea al SRI.

6. **No amplíe el alcance sin acuerdo.** El proyecto no emite comprobantes,
   no los autoriza y no genera XML. Eso es territorio de
   [`sri-comprobantes-php`](https://github.com/julioserpone/sri-comprobantes-php).

## Pull requests

- Rama desde `main`, historial lineal.
- `make check` en verde.
- Plantilla de PR completada.
- `CHANGELOG.md` actualizado si el cambio es visible.
- Sin llamadas reales al SRI en la suite.

## Código de conducta

Ver [CODE_OF_CONDUCT.md](CODE_OF_CONDUCT.md).
