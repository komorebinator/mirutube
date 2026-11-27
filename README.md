# MiruTube

A video server for the home network. MiruTube runs on the computer that holds your videos and serves them to every other device in the home — the TV, a laptop, a phone — as a site in the style of a video-hosting service. Nothing is installed on those devices and nothing is copied to them first: open the address, pick a video, watch.

## What it does

- **Folders become playlists.** Every folder is a playlist; a folder of episodes is recognised as a series and played in order, however the files are named — `S01E02`, `Show - 02 [1080p]`, `Серия 2`, seasons in subfolders, extras such as `Bonus` or `OVA` as playlists of their own.
- **Subtitles and dubs are found by themselves.** Embedded tracks, and external `.srt`, `.ass`, `.vtt` files and sound tracks beside the videos or in folders such as `Subs/<translator>` or `[Audio]/<dub>`, are matched to their episodes and labelled by language and translator. The choice is kept for the whole series.
- **Every video plays.** What a browser can play is sent as it is; anything else — HEVC 10-bit, FLAC, AVI — is converted as it plays, on the GPU where there is one (NVIDIA NVENC, Intel and AMD VAAPI) and on the processor otherwise. Seeking works the same either way.
- **Devices see each other.** Every open page is a device. The bar at the bottom controls whichever device plays — pause the TV from the phone, switch its subtitles — and moves playback from one device to another, from the same moment.
- **Continue where you stopped,** on any device: positions are kept by the server, one card per series.
- **Bring videos in from any device.** Drop files or a whole folder onto the page, or paste a link to a video or a playlist. What is watched first comes first: the first episode plays while the rest arrive, a new episode joins its series by itself, and every device sees what is on its way.
- **Subtitles sized for the screen.** They keep the same proportion to the picture on a phone and on a TV across the room, and one setting makes them larger or smaller on every device at once.
- **English and Russian.** One language for the whole household, chosen in settings; until then each browser shows its own.
- **Connect by QR code,** and install MiruTube as an app on a phone or a computer.

## Install

MiruTube runs on Linux as a container in your user's systemd, through Podman's Quadlet. It needs systemd and Podman 5.1 or newer — Fedora, Debian 13, Ubuntu 25.04 and later, Arch and openSUSE Tumbleweed have it; Ubuntu 24.04 and Debian 12 are too old, and there `systemctl --user start mirutube` answers that the unit is not found.

```sh
mkdir -p ~/.config/containers/systemd
curl -fLo ~/.config/containers/systemd/mirutube.container \
  https://github.com/komorebinator/mirutube/releases/latest/download/mirutube.container
systemctl --user daemon-reload
systemctl --user start mirutube
systemctl --user enable --now podman-auto-update.timer    # keep MiruTube up to date
```

Then open `http://localhost:8800` on that computer, go to **Settings** and add the folders with your videos — any folder you can read, on any disk, by its usual path. Other devices open the address shown at the top left, beside the logo, or scan its QR code. The first start downloads the image, about 290 MB.

MiruTube starts whenever you log in. Intel and AMD GPUs convert video with nothing more to do; the video details under a playing video say which encoder is used.

### Running while nobody is logged in

For MiruTube to start at boot and keep running after you log out — a computer that serves the house — let your user's services run without a login, and add yourself to the `render` group, which is what gives the GPU to a service started before anyone logs in:

```sh
loginctl enable-linger
grep -q '^render:' /etc/group || grep '^render:' /usr/lib/group 2>/dev/null | sudo tee -a /etc/group >/dev/null
sudo usermod -aG render $USER
```

The second line matters only on Fedora Atomic desktops (Silverblue, Kinoite, Bluefin, Bazzite and the like), which keep the group in `/usr/lib/group`; elsewhere it does nothing. Restart the computer afterwards: the new group reaches MiruTube only then.

### NVIDIA

Install `nvidia-container-toolkit`, then add the drop-in that passes the GPU in:

```sh
nvidia-ctk cdi list | grep -q nvidia.com/gpu || sudo nvidia-ctk cdi generate --output=/etc/cdi/nvidia.yaml
mkdir -p ~/.config/containers/systemd/mirutube.container.d
curl -fLo ~/.config/containers/systemd/mirutube.container.d/nvidia.conf \
  https://github.com/komorebinator/mirutube/releases/latest/download/nvidia.conf
systemctl --user daemon-reload && systemctl --user restart mirutube
```

The first line describes the GPU to Podman where the toolkit has not done it by itself.

### The firewall

MiruTube listens on port 8800. Fedora leaves it open on the home network; elsewhere, if a firewall is on, open it:

```sh
sudo firewall-cmd --permanent --add-port=8800/tcp && sudo firewall-cmd --reload   # firewalld
sudo ufw allow 8800/tcp                                                            # ufw
```

### Installing as an app

Everything works over plain HTTP; a browser installs a site as an app only over HTTPS, which MiruTube serves on the same port with a certificate of its own. Press **Install** in the top bar on a device, and it shows what that device needs.

### Uninstall

```sh
systemctl --user stop mirutube
rm ~/.config/containers/systemd/mirutube.container
rm -r ~/.config/containers/systemd/mirutube.container.d   # if you added drop-ins
systemctl --user daemon-reload
```

Your positions, settings and certificates stay in `~/.local/share/mirutube` until you remove them.

## Where things are kept

| What | Where |
|---|---|
| Positions, settings, certificates | `~/.local/share/mirutube` |
| Thumbnails and other things that can be made again | `~/.cache/mirutube` |
| Optional configuration | `~/.config/mirutube/mirutube.conf` |

The configuration file is INI, every key optional:

```ini
[server]
port = 8800
```

## Running from source

MiruTube itself needs only Python 3.10 or newer and ffmpeg; links are fetched with [yt-dlp](https://github.com/yt-dlp/yt-dlp), which the image carries:

```sh
python3 -m mirutube            # serves on port 8800
python3 -m mirutube --help     # every option
python3 -m unittest discover   # the tests
```

## Contributing

MiruTube is written with [duckspec](https://github.com/komorebinator/duckspec): what it does and why lives in the spec under `spec/`. [AGENTS.md](AGENTS.md) says how to load it.

## License

[MIT](LICENSE)
