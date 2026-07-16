FROM python:3.11-slim

WORKDIR /app

# Manim's system deps (cairo/pango/latex/ffmpeg), plus Noto fonts so Bengali/Devanagari/other Indic
# scripts actually render as glyphs instead of tofu boxes in generated Text()/Paragraph() objects.
RUN apt-get update && apt-get install -y \
    build-essential \
    libcairo2-dev \
    ffmpeg \
    texlive \
    texlive-latex-extra \
    texlive-fonts-extra \
    texlive-latex-recommended \
    texlive-science \
    tipa \
    libpango1.0-dev \
    pkg-config \
    curl \
    fonts-noto-core \
    fonts-noto-extra \
    fonts-noto-ui-core \
    && rm -rf /var/lib/apt/lists/*

COPY --from=ghcr.io/astral-sh/uv:latest /uv /bin/uv

COPY requirements.txt .
RUN uv pip install --system -r requirements.txt

COPY . .

RUN mkdir -p media content

ENV PYTHONUNBUFFERED=1
ENV PYTHONPATH=/app/backend

EXPOSE 8000 8501
