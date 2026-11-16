"""Fetches yt-dlp, and Deno, which it needs for YouTube, at their latest into
/usr/local/bin as the image is built (decisions#ytdlp-in-image) — with the
image's own Python, so nothing else is installed for it."""

import io
import os
import urllib.request
import zipfile

ARCH = {"amd64": "x86_64", "arm64": "aarch64"}[os.environ.get("TARGETARCH") or "amd64"]
BIN = "/usr/local/bin"


def fetch(url):
    with urllib.request.urlopen(url, timeout=300) as r:
        return r.read()


with open(os.path.join(BIN, "yt-dlp"), "wb") as f:
    f.write(fetch("https://github.com/yt-dlp/yt-dlp/releases/latest/download/yt-dlp"))
zipfile.ZipFile(io.BytesIO(fetch(
    "https://github.com/denoland/deno/releases/latest/download/deno-%s-unknown-linux-gnu.zip" % ARCH
))).extract("deno", BIN)
for tool in ("yt-dlp", "deno"):
    os.chmod(os.path.join(BIN, tool), 0o755)
