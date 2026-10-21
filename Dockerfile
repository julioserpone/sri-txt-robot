# Imagen del robot. El runtime de Lambda (Amazon Linux 2023) comparte glibc
# con Ubuntu 22.04, por lo que se instala Chromium con Playwright sobre la
# imagen base de AWS Lambda en lugar de usar la imagen oficial de Playwright
# (que está basada en Noble, glibc 2.39).
FROM public.ecr.aws/lambda/python:3.12

ENV PLAYWRIGHT_BROWSERS_PATH=/opt/playwright \
    PIP_NO_CACHE_DIR=1 \
    PIP_DISABLE_PIP_VERSION_CHECK=1 \
    PYTHONUNBUFFERED=1 \
    PYTHONDONTWRITEBYTECODE=1

WORKDIR /opt/robot

COPY pyproject.toml README.md ./
COPY src ./src

RUN pip install --upgrade pip \
    && pip install . \
    && playwright install --with-deps chromium \
    && playwright install chromium

COPY config ./config

ENV SRI_ROBOT_CONFIG=/opt/robot/config/robots.yaml \
    SRI_ROBOT_SELECTORS=/opt/robot/config/selectors.yaml \
    SRI_ROBOT_SECRETS_ARN=""

CMD ["sri_txt_robot.handler.handler"]
