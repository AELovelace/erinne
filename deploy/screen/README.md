# Mnemerynne screen: a Fedora box as the TV

`install.sh` turns a small PC (built for an Intel NUC with a 3rd-generation Core i5) into a screen for
Media. It starts straight into the screen page, full-screen, with sound over HDMI. You cast to it from
Media on your phone. It needs no keyboard, mouse or sign-in.

How it works:

- **Kiosk:** Firefox runs in kiosk mode under `cage` (a Wayland compositor that shows one app) on tty1,
  as its own user, `mnemerynne-screen`.
- **Pairing:** the first time, the screen shows a code and a QR code. Pair it from a phone with
  **Media → Screens → Pair a TV**.
- **Self-healing:** if Firefox crashes, it starts again within seconds. After a power cut it waits for
  the network and the server, then carries on.

## 1. The box

- **BIOS** (F2 at boot): set **After Power Failure → Power On**, so the screen comes back by itself.
  Update the BIOS while you're there.
- **Fedora:** Fedora Server, or the Everything netinstall with a *Minimal Install*, is all it needs; it
  doesn't need a desktop. Create an admin account (for SSH) during installation. Leave root locked.
- **Network:** wired is best. Wi-Fi works, set up with `nmtui`.
- **Ports:** nothing to open. The screen only connects out to the server, on 443.

## 2. Install

Copy the installer to the box, then run it with the address people open Mnemerynne at:

```sh
scp deploy/screen/install.sh admin@nuc:          # from the PC with this repository
ssh admin@nuc
sudo bash install.sh https://erynne.example.com
```

It takes a few minutes. It:

- enables RPM Fusion (free);
- swaps Fedora's FFmpeg for RPM Fusion's, because Fedora's own can't decode H.264, which Mnemerynne's
  transcoder sends;
- installs the VA-API drivers, cage, Firefox and PipeWire;
- writes its files and starts the screen;
- checks the box and prints what it found (step 4).

The TV then shows **Pair this screen** with a code.

Options:

| Option | What it does |
| --- | --- |
| `--audio auto` | Let PipeWire choose the output, instead of HDMI. |
| `--youtube-any-codec` | Let YouTube send VP9 or AV1. By default it's asked for H.264, which this GPU decodes. VP9 or AV1 are decoded on the CPU, which manages 1080p at 30 fps but struggles at 60 fps. |
| `--no-auto-updates` | Don't install updates nightly. By default `dnf5-automatic` installs them and reboots when an update needs it. |
| `--dry-run` | Show the plan; change nothing. |

Run it again at any time, for example with a new address. It rewrites its files and restarts the screen.

## 3. Pair it

1. **Turn on screens:** an admin sets **Settings → Media → Who may use screens**, if that isn't already done.
2. **Open Pair a TV:** on your phone, open Media → **Screens → Pair a TV**, or scan the QR code on the TV.
3. **Enter the code:** type the code if you didn't scan, name the screen, for example *Living room*, and press **Pair**.

The TV switches to screen mode within a few seconds and stays paired until you remove it under
**Media → Screens**. Removing it sends the TV back to showing a new code.

## 4. Check it

```sh
sudo bash install.sh --check
```

| Line | Meaning |
| --- | --- |
| **RPM Fusion FFmpeg** / **H.264 (High) decodes** | Films play at all. |
| **the GPU decodes H.264** | VA-API works with the i965 driver; video is cheap. If this says FAIL, video still plays, on the CPU. |
| **… answers** / **the screen page answers** | The box reaches the server, its certificate is trusted, and Media is turned on. |
| **running on tty1** / **sound goes to HDMI** | The kiosk is up and sound goes to the TV. If the TV was off, give it 30 seconds after turning it on. |

## When something's wrong

| What you see | What to do |
| --- | --- |
| A black screen | `journalctl -u mnemerynne-screen -b` shows why cage or Firefox stopped. `sudo systemctl restart mnemerynne-screen` starts it again. |
| **Connecting to … / Not reachable yet** | The box can't reach the server: check the cable and DNS, and `curl -I https://erynne.example.com/healthz`. |
| A 404 page | Media is off, or the screens group isn't set (Settings → Media). |
| No sound | Is the TV's input on this box, and is its volume up? `sudo bash install.sh --check` says whether sound goes to HDMI. `--audio auto` leaves the choice to PipeWire. |
| Stutters on films | The GPU isn't decoding (see `--check`), or the server is transcoding something heavy. Admin → Settings → Media shows running transcodes. |

To use the box for something else: `sudo bash install.sh --uninstall`, and remove the screen under Media → Screens.

## What it writes

| File | What it's for |
| --- | --- |
| `/etc/mnemerynne-screen.conf` | The address and the sound setting. |
| `/etc/systemd/system/mnemerynne-screen.service`, `/etc/pam.d/mnemerynne-screen` | cage on tty1 with a login session. That session gives it the GPU, input and PipeWire. |
| `/usr/local/bin/mnemerynne-screen-session` | Starts Firefox. |
| `/usr/local/bin/mnemerynne-screen-audio` | Keeps sound on HDMI. |
| `/usr/local/share/mnemerynne-screen/` | `start.html`, which waits for the server; `user.js`, the Firefox preferences, copied in at every start. |
| `/etc/firefox/policies/policies.json` | Autoplay allowed for the server only; no extensions, updates, telemetry or first-run pages. It applies to every Firefox on the box, which on a dedicated box is only this one. |
| `/etc/dnf/dnf5-plugins/automatic.conf` | Nightly updates. |

The screen's key is a cookie in `/home/mnemerynne-screen/firefox`. Anyone with that folder can play
Media as the screen's owner, so treat the box like a signed-in PC. Removing the screen under Media →
Screens revokes the key.
