# MiruTube

A video server for the home network. MiruTube runs on the computer that holds your videos and serves them to every other device in the home — the TV, a laptop, a phone — as a site in the style of a video-hosting service. Nothing is installed on those devices and nothing is copied to them first: open the address, pick a video, watch.

## What it does

- **Folders become playlists.** Every folder is a playlist; a folder of episodes is recognised as a series and played in order, however the files are named — `S01E02`, `Show - 02 [1080p]`, `Серия 2`, seasons in subfolders, extras such as `Bonus` or `OVA` as playlists of their own.
- **Subtitles and dubs are found by themselves.** Embedded tracks, and external `.srt`, `.ass`, `.vtt` files and sound tracks beside the videos or in folders such as `Subs/<translator>` or `[Audio]/<dub>`, are matched to their episodes and labelled by language and translator. The choice is kept for the whole series.
- **Every video plays.** What a browser can play is sent as it is; anything else — HEVC 10-bit, FLAC, AVI — is converted as it plays, on the GPU where there is one (NVIDIA NVENC, Intel and AMD VAAPI) and on the processor otherwise. Seeking works the same either way.
- **Devices see each other.** Every open page is a device. The bar at the bottom controls whichever device plays — pause the TV from the phone, switch its subtitles — and moves playback from one device to another, from the same moment.
- **Continue where you stopped,** on any device: positions are kept by the server, one card per series.
- **Connect by QR code,** and install MiruTube as an app on a phone or a computer.

## Install

MiruTube runs on Linux as a container managed by systemd, through Podman's Quadlet. Podman 4.4 or newer is needed — it comes with Fedora (Workstation, Silverblue and the other Atomic desktops) and is a package away elsewhere.

```sh
mkdir -p ~/.config/containers/systemd
curl -fLo ~/.config/containers/systemd/mirutube.container \
  https://github.com/komorebinator/mirutube/releases/latest/download/mirutube.container
systemctl --user daemon-reload
systemctl --user start mirutube

loginctl enable-linger                                    # start at boot, before anyone logs in
systemctl --user enable --now podman-auto-update.timer    # keep MiruTube up to date
```

Then open `http://localhost:8800` on that computer, go to **Settings** and add the folders with your videos — any folder you can read, on any disk, by its usual path. Other devices open the address shown at the top right, or scan its QR code.

The first start downloads the image, about 240 MB.

### The GPU

Intel and AMD GPUs are used through VAAPI with nothing more to do — except that, for MiruTube started at boot, before anyone logs in, your user has to be in the `render` group:

```sh
sudo usermod -aG render $USER
```

On Fedora Atomic desktops the group lives in `/usr/lib/group`, so copy it to `/etc/group` first:

```sh
grep '^render:' /usr/lib/group | sudo tee -a /etc/group
sudo usermod -aG render $USER
```

For an NVIDIA GPU, install `nvidia-container-toolkit`, then add the drop-in that passes the GPU in:

```sh
mkdir -p ~/.config/containers/systemd/mirutube.container.d
curl -fLo ~/.config/containers/systemd/mirutube.container.d/nvidia.conf \
  https://github.com/komorebinator/mirutube/releases/latest/download/nvidia.conf
systemctl --user daemon-reload && systemctl --user restart mirutube
```

The video details under a playing video say which encoder converts it.

### The firewall

MiruTube listens on port 8800. Fedora Workstation and the Atomic desktops leave it open on the home network already; elsewhere, open it:

```sh
sudo firewall-cmd --permanent --add-port=8800/tcp && sudo firewall-cmd --reload   # firewalld
sudo ufw allow 8800/tcp                                                            # ufw
```

### HTTPS and installing as an app

MiruTube answers plain HTTP and HTTPS on the same port. Everything works over HTTP; HTTPS is what a browser asks for before it installs a site as an app, and what makes the `Not secure` label go away. MiruTube signs its own certificate, with an authority limited to home-network addresses: press **Install** in the top bar on a device, and it shows how to install the certificate on that device once, and then the app.

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

MiruTube itself needs only Python 3.10 or newer and ffmpeg:

```sh
python3 -m mirutube            # serves on port 8800
python3 -m mirutube --help     # every option
python3 -m unittest discover   # the tests
```

## Contributing

MiruTube is written with [duckspec](https://github.com/komorebinator/duckspec): what it does and why lives in the spec under `spec/`. [AGENTS.md](AGENTS.md) says how to load it.

## License

[MIT](LICENSE)
