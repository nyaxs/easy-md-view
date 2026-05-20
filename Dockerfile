FROM python:3.11-slim-bullseye

ENV DEBIAN_FRONTEND=noninteractive \
    QT_QPA_PLATFORM=offscreen
RUN apt-get update && apt-get install -y --no-install-recommends \
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
    xfonts-base \
    && rm -rf /var/lib/apt/lists/*
RUN wkhtmltopdf --version && pandoc --version && rsvg-convert --version

WORKDIR /app

COPY requirements.txt .
RUN pip install -i https://pypi.tuna.tsinghua.edu.cn/simple --trusted-host pypi.tuna.tsinghua.edu.cn --no-cache-dir -r requirements.txt

COPY main.py .
EXPOSE 8080

CMD ["uvicorn", "main:app", "--host", "0.0.0.0", "--port", "8080"]
