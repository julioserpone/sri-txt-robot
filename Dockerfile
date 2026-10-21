# Imagen del robot.
#
# El runtime de Lambda para Python es Amazon Linux 2023, que usa `dnf` (no
# `apt-get` como Ubuntu) y tiene glibc 2.35. Por eso NO se puede usar
# `playwright install --with-deps`: las dependencias de sistema se instalan
# con `dnf` de forma explícita, y el navegador se descarga sin `--with-deps`.
#
# La imagen oficial de Playwright está basada en Ubuntu Noble (glibc 2.39) y
# no es compatible con el runtime de Lambda.
FROM public.ecr.aws/lambda/python:3.12

ENV PLAYWRIGHT_BROWSERS_PATH=/opt/playwright \
    PIP_NO_CACHE_DIR=1 \
    PIP_DISABLE_PIP_VERSION_CHECK=1 \
    PYTHONUNBUFFERED=1 \
    PYTHONDONTWRITEBYTECODE=1

WORKDIR /opt/robot

# Dependencias de sistema que Chromium necesita en distribuciones de la
# familia RHEL. Equivalen a las que instala `playwright install --with-deps`
# en Ubuntu.
RUN dnf install -y \
        alsa-lib \
        atk \
        at-spi2-atk \
        at-spi2-core \
        cups-libs \
        libdrm \
        libXcomposite \
        libXdamage \
        libXext \
        libXfixes \
        libXrandr \
        libxkbcommon \
        mesa-libgbm \
        nss \
        pango \
        cairo \
        libX11 \
    && dnf clean all \
    && rm -rf /var/cache/dnf

COPY pyproject.toml README.md ./
COPY src ./src

RUN pip install --upgrade pip \
    && pip install . \
    && playwright install chromium \
    && rm -rf /var/cache/dnf

COPY config ./config

ENV SRI_ROBOT_CONFIG=/opt/robot/config/robots.yaml \
    SRI_ROBOT_SELECTORS=/opt/robot/config/selectors.yaml \
    SRI_ROBOT_SECRETS_ARN=""

CMD ["sri_txt_robot.handler.handler"]
