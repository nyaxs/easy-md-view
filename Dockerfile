ARG BASE_IMAGE=python:3.11-slim-bullseye
FROM ${BASE_IMAGE}

ARG APT_MIRROR=
ARG APT_SECURITY_MIRROR=
ARG PIP_INDEX_URL=https://pypi.tuna.tsinghua.edu.cn/simple
ARG PIP_TRUSTED_HOST=pypi.tuna.tsinghua.edu.cn

ENV DEBIAN_FRONTEND=noninteractive \
    QT_QPA_PLATFORM=offscreen
# Some older container hosts reject QtCore's Linux ABI note, which makes wkhtmltopdf unable to load libQt5Core.so.5.
RUN set -eux; \
    if [ -n "$APT_MIRROR" ]; then \
      sed -i \
        -e "s|http://deb.debian.org/debian|$APT_MIRROR|g" \
        -e "s|http://security.debian.org/debian-security|${APT_SECURITY_MIRROR:-$APT_MIRROR-security}|g" \
        -e "s|http://deb.debian.org/debian-security|${APT_SECURITY_MIRROR:-$APT_MIRROR-security}|g" \
        /etc/apt/sources.list; \
    fi; \
    apt-get update && apt-get install -y --no-install-recommends \
    binutils \
    pandoc \
    wkhtmltopdf \
    librsvg2-bin \
    libfontconfig1 \
    libfreetype6 \
    libqt5core5a \
    libqt5gui5 \
    libqt5printsupport5 \
    libqt5svg5 \
    libqt5webkit5 \
    libqt5widgets5 \
    libx11-6 \
    libxext6 \
    libxrender1 \
    fonts-wqy-microhei \
    fonts-wqy-zenhei \
    xfonts-75dpi \
    xfonts-base; \
    qtcore_path="$(readlink -f /usr/lib/x86_64-linux-gnu/libQt5Core.so.5)"; \
    strip --remove-section=.note.ABI-tag "$qtcore_path"; \
    apt-get purge -y --auto-remove binutils; \
    rm -rf /var/lib/apt/lists/*
RUN wkhtmltopdf --version && pandoc --version && rsvg-convert --version

WORKDIR /app

COPY requirements.txt .
RUN pip install -i "$PIP_INDEX_URL" --trusted-host "$PIP_TRUSTED_HOST" --no-cache-dir -r requirements.txt

COPY main.py .
EXPOSE 8080

CMD ["uvicorn", "main:app", "--host", "0.0.0.0", "--port", "8080"]
