# image: the container image MiruTube is installed from (decisions#quadlet).
# Debian rather than Fedora: its ffmpeg has every codec and NVENC, and its
# Mesa VAAPI driver encodes H.264 and HEVC, which Fedora's leaves out. Nothing
# of NVIDIA's is here: the driver's own libraries are mounted in by CDI when
# the container starts, so they always match the driver of the computer.
FROM docker.io/library/debian:trixie-slim

RUN sed -i 's/^Components: main$/Components: main contrib non-free non-free-firmware/' \
        /etc/apt/sources.list.d/debian.sources \
    && apt-get update \
    && apt-get install -y --no-install-recommends \
        python3 ffmpeg mesa-va-drivers intel-media-va-driver-non-free \
    && rm -rf /var/lib/apt/lists/*

COPY mirutube /app/mirutube
WORKDIR /app

# service mounts the computer's file system at /host, and the user's data,
# cache and config folders at /data, /cache and /config
ENV MIRUTUBE_HOST_ROOT=/host \
    PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1

ENTRYPOINT ["python3", "-m", "mirutube", "--data", "/data", "--cache", "/cache", "--config", "/config/mirutube.conf"]

LABEL org.opencontainers.image.title="MiruTube" \
      org.opencontainers.image.description="A video server for the home network" \
      org.opencontainers.image.source="https://github.com/komorebinator/mirutube" \
      org.opencontainers.image.licenses="MIT"
