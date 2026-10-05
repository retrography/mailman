# The Home Assistant app image (also runs as a plain container).
# Two stages on Docker Hardened Images: the build stage has a shell and installs the project; the final
# image is the hardened Python runtime (no shell, no package manager) plus the installed project.
FROM dhi.io/python:3.13-dev AS build

COPY --from=ghcr.io/astral-sh/uv:latest /uv /usr/local/bin/uv
ENV UV_LINK_MODE=copy UV_NO_CACHE=1 UV_PYTHON=/usr/bin/python3 UV_PYTHON_DOWNLOADS=never
WORKDIR /app

COPY pyproject.toml uv.lock ./
RUN uv sync --frozen --no-dev --no-install-project
COPY src ./src
RUN uv sync --frozen --no-dev --no-editable

# Data the app never reads: every Google API description but Gmail's, phone-number geography and carriers,
# country-name translations.
RUN cd .venv/lib/python3.13/site-packages \
 && find googleapiclient/discovery_cache/documents -type f ! -name 'gmail.v1.json' -delete \
 && rm -rf phonenumbers/geodata phonenumbers/carrierdata pycountry/locales \
 && find . -name '__pycache__' -type d -prune -exec rm -rf {} +

COPY starter ./defaults/config


FROM dhi.io/python:3.13

ENV PYTHONUNBUFFERED=1 PYTHONDONTWRITEBYTECODE=1 TZ=Europe/Amsterdam
WORKDIR /app
COPY --from=build /app/.venv ./.venv
COPY --from=build /app/defaults ./defaults

ARG BUILD_VERSION=dev
LABEL io.hass.version="${BUILD_VERSION}" io.hass.type="app" io.hass.arch="aarch64|amd64" \
      org.opencontainers.image.source="https://github.com/retrography/mailman" \
      org.opencontainers.image.description="Mailman: sorts a Gmail mailbox by rules you can read and change"

# Home Assistant mounts the app's storage and options as root-owned.
USER 0
EXPOSE 8099
ENTRYPOINT ["/app/.venv/bin/python", "-m", "mailman.ha"]
