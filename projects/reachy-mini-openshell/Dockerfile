FROM ghcr.io/nvidia/openshell-community/sandboxes/base:latest

USER root
WORKDIR /sandbox/reachy-mini-openshell

RUN apt-get update \
    && apt-get install -y --no-install-recommends \
        build-essential \
        pkg-config \
        python3-dev \
        libcairo2-dev \
        libgirepository1.0-dev \
        libglib2.0-dev \
        libgstreamer-plugins-bad1.0-dev \
        libgstreamer-plugins-base1.0-dev \
        libgstreamer1.0-dev \
        libnice10 \
        libportaudio2 \
        libssl-dev \
        gstreamer1.0-alsa \
        gstreamer1.0-nice \
        gstreamer1.0-plugins-bad \
        gstreamer1.0-plugins-good \
        python3-gi \
        python3-gi-cairo \
    && rm -rf /var/lib/apt/lists/*

COPY pyproject.toml README.md ./
COPY src ./src

RUN python3 -m venv /opt/reachy-mini-openshell
ENV PATH="/opt/reachy-mini-openshell/bin:${PATH}"
ENV PYTHONDONTWRITEBYTECODE=1

RUN pip install --no-cache-dir ".[backend,sdk]"
RUN chown -R sandbox:sandbox /sandbox/reachy-mini-openshell /opt/reachy-mini-openshell

USER sandbox

CMD ["python3", "-m", "reachy_openshell.backend", "--host", "127.0.0.1", "--port", "8080"]
