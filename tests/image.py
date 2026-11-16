"""The image tests#container and tests#browser run.

`MIRUTUBE_TEST_IMAGE` names a built one — `ghcr.io/komorebinator/mirutube:experimental`
when @MiruTubeWorkflow#recipes#check_image runs — which is pulled and refused,
before anything is tested, unless it is `main`'s: the commit it is labelled
with is in the history checked out, and nothing the image is built from
changed since. Without it, an image is built from the working tree.
"""

import http.client
import os
import shutil
import subprocess
import time

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
LOCAL = "localhost/mirutube:test"
# what the image is built from (@MiruTubeWorkflow#components#image_build)
BUILD_PATHS = ("mirutube", "Containerfile", "image", ".containerignore")
REVISION_LABEL = "org.opencontainers.image.revision"

_image = None


class ImageRefused(Exception):
    """The image named is not the one `main` makes."""


def podman(*args, check=True):
    return subprocess.run(["podman", *args], capture_output=True, text=True, check=check)


def git(*args):
    return subprocess.run(["git", "-C", ROOT, *args], capture_output=True, text=True)


def have_podman():
    return shutil.which("podman") is not None


def check_revision(revision):
    """Refuses an image whose commit is not in the history checked out, or
    when what the image is built from changed since that commit."""
    if not revision or revision == "unknown":
        raise ImageRefused("the image has no %s label: it was not built by image_build" % REVISION_LABEL)
    if git("merge-base", "--is-ancestor", revision, "HEAD").returncode != 0:
        raise ImageRefused("the image was built from %s, which is not in the history checked out: "
                           "check out main and pull" % revision)
    changed = git("diff", "--name-only", revision, "HEAD", "--", *BUILD_PATHS).stdout.split()
    if changed:
        raise ImageRefused("what the image is built from changed since %s, which it was built from (%s): "
                           "wait for image_build, or check the working tree's own build"
                           % (revision[:12], ", ".join(changed[:5])))


def image_under_test():
    """The image to test, pulled or built once per run."""
    global _image
    if _image is not None:
        return _image
    named = os.environ.get("MIRUTUBE_TEST_IMAGE")
    if named:
        pulled = podman("pull", named, check=False)
        if pulled.returncode:
            raise RuntimeError("the image could not be pulled:\n" + pulled.stderr[-2000:])
        revision = podman("image", "inspect", "--format", "{{index .Labels \"%s\"}}" % REVISION_LABEL,
                          named).stdout.strip()
        check_revision(revision)
        _image = named
    else:
        revision = git("rev-parse", "HEAD").stdout.strip() or "unknown"
        if git("status", "--porcelain", "--", *BUILD_PATHS).stdout.strip():
            revision += "-dirty"
        build = podman("build", "--build-arg", "REVISION=" + revision, "-t", LOCAL, ROOT, check=False)
        if build.returncode:
            raise RuntimeError("the image did not build:\n" + build.stdout[-3000:] + build.stderr[-3000:])
        _image = LOCAL
    return _image


def gpu_options():
    """What service passes in of this computer's GPU: `/dev/dri`, and an NVIDIA
    card by CDI where nvidia-container-toolkit has described it (nvidia.conf)."""
    options = []
    if os.path.exists("/dev/dri"):
        options += ["--device", "/dev/dri"]
    if any(os.path.exists(p) for p in ("/etc/cdi/nvidia.yaml", "/var/run/cdi/nvidia.yaml")):
        options += ["--device", "nvidia.com/gpu=all"]
    return options


def run_mirutube(name, folders, port):
    """Starts the image under test as service runs it (mirutube.container) —
    the computer's own network, as the user, the whole file system at /host,
    the GPU — with `folders` {data, cache, config}, listening on `port`, and
    waits until it answers."""
    options = ["--network", "host", "--userns", "keep-id", "--group-add", "keep-groups",
               "--security-opt", "label=disable", "-v", "/:/host:rslave",
               "-v", folders["data"] + ":/data", "-v", folders["cache"] + ":/cache",
               "-v", folders["config"] + ":/config"] + gpu_options()
    podman("run", "-d", "--rm", "--name", name, *options, image_under_test(),
           "--port", str(port), "--host", "127.0.0.1")
    deadline = time.monotonic() + 60
    while True:
        try:
            conn = http.client.HTTPConnection("127.0.0.1", port, timeout=5)
            conn.request("GET", "/api/ping")
            if conn.getresponse().status == 200:
                return
        except OSError:
            pass
        if time.monotonic() > deadline:
            logs = podman("logs", name, check=False)
            podman("rm", "-f", "-t", "2", name, check=False)
            raise RuntimeError("the container did not start:\n" + logs.stdout + logs.stderr)
        time.sleep(0.5)
