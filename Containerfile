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
        python3 python3-libtorrent ca-certificates ffmpeg mesa-va-drivers intel-media-va-driver-non-free \
    && rm -rf /var/lib/apt/lists/*

# yt-dlp, and Deno, which it needs for YouTube, at their latest when the image
# is built (decisions#ytdlp-in-image); fetched with the image's own Python, so
# nothing else is installed for it
ARG TARGETARCH
COPY image/fetch_tools.py /tmp/fetch_tools.py
RUN python3 /tmp/fetch_tools.py && rm /tmp/fetch_tools.py

COPY mirutube /app/mirutube
WORKDIR /app

# service mounts the computer's file system at /host, and the user's data,
# cache and config folders at /data, /cache and /config
ENV MIRUTUBE_HOST_ROOT=/host \
    PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1

ENTRYPOINT ["python3", "-m", "mirutube", "--data", "/data", "--cache", "/cache", "--config", "/config/mirutube.conf"]

# the commit the image is built from, which check_image and image_promote
# compare with main (@MiruTubeWorkflow#components#image_build)
ARG REVISION=unknown
LABEL org.opencontainers.image.revision="$REVISION"
LABEL org.opencontainers.image.title="MiruTube" \
      org.opencontainers.image.description="A video server for the home network" \
      org.opencontainers.image.source="https://github.com/komorebinator/mirutube" \
      org.opencontainers.image.licenses="MIT"
