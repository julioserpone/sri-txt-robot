# Imagen del robot para AWS Lambda (contenedor) y para pruebas locales.
#
# La base de Playwright ya trae Chromium y sus dependencias; se añade el
# Runtime Interface Client de Lambda y el paquete del robot.
#
# La configuración y los selectores se montan o copian en /opt/config. Las
# credenciales NO van en la imagen: en el modo estático se pasan como variables
# de entorno (SRI_PASSWORD, SRI_AMBIENTE, SRI_BUCKET); en el dinámico solo se
# guarda SRI_CREDENTIALS_PRIVATE_KEY y cada evento trae sealed_credentials.
FROM mcr.microsoft.com/playwright/python:v1.64.0-noble

ENV PYTHONUNBUFFERED=1 \
    PYTHONDONTWRITEBYTECODE=1 \
    SRI_ROBOT_CONFIG=/opt/config/robots.yaml \
    SRI_ROBOT_SELECTORS=/opt/config/selectors.yaml

RUN pip install --no-cache-dir "awslambdaric==4.2.2"

WORKDIR /var/task

COPY pyproject.toml README.md ./
COPY src ./src
RUN pip install --no-cache-dir .

COPY config/selectors.yaml /opt/config/selectors.yaml

ENTRYPOINT ["/usr/bin/python", "-m", "awslambdaric"]
CMD ["sri_txt_robot.handler.handler"]
