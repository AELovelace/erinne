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
- **Wi-Fi from a phone:** with no network, the box offers its own Wi-Fi hotspot, and the TV shows how
  to join it and pick the box's network from your phone (step 5).
- **Kiosk settings:** admins change the box's address, volume, top bar and text size, and install updates, from the
  TV's menu with a phone approval or a PIN (step 6).

## 1. The box

- **BIOS** (F2 at boot): set **After Power Failure → Power On**, so the screen comes back by itself.
  Update the BIOS while you're there.
- **Fedora:** Fedora Server, or the Everything netinstall with a *Minimal Install*, is all it needs; it
  doesn't need a desktop. Create an admin account (for SSH) during installation. Leave root locked.
- **Network:** wired is best for the install itself. Afterwards the box can be moved onto Wi-Fi from
  a phone (step 5).
- **Ports:** nothing to open. The screen only connects out to the server, on 443.

## 2. Install

Get the repository onto the box and run the installer with the address people open Mnemerynne at:

```sh
sudo dnf install -y git && git clone https://github.com/AELovelace/erinne.git
sudo bash erinne/deploy/screen/install.sh https://erynne.example.com
```

The installer copies the clone's commit to `/opt/mnemerynne-screen`, the box's own copy, which **Kiosk
settings → Updates** moves between releases (step 6). Updating by hand still works:
`cd ~/erinne && git pull && sudo bash deploy/screen/install.sh --keep-options` (it keeps the address and
the options). The installer needs `wifi-setup.py` beside it; to copy files instead of cloning, copy both,
but then Kiosk settings can't update the box.

It takes a few minutes. It:

- enables RPM Fusion (free);
- swaps Fedora's FFmpeg for RPM Fusion's, because Fedora's own can't decode H.264, which Mnemerynne's
  transcoder sends;
- installs the VA-API drivers, cage, Firefox, PipeWire, wlr-randr, git, and what the Wi-Fi setup needs;
- writes its files and starts the screen;
- checks the box and prints what it found (step 4).

The TV then shows **Pair this screen** with a code.

Options:

| Option | What it does |
| --- | --- |
| `--audio auto` | Let PipeWire choose the output, instead of HDMI. Kiosk settings lists the box's other outputs (`--audio card:CARD:PROFILE`). |
| `--youtube-any-codec` | Let YouTube send VP9 or AV1. By default it's asked for H.264, which this GPU decodes. VP9 or AV1 are decoded on the CPU, which manages 1080p at 30 fps but struggles at 60 fps. |
| `--no-auto-updates` | Don't install updates nightly. By default `dnf5-automatic` installs them and reboots when an update needs it. |
| `--no-wifi-setup` | No setup hotspot. The start page and Kiosk settings are still there. |
| `--volume 0-100` | The system volume (default 100). Kiosk settings changes it too. |
| `--display auto` | The TV's picture. **auto** (the default) keeps the TV's own mode, except a mode bigger than 1080p at under 50 Hz (4K at 30 Hz): then the same size at 60 Hz if the TV offers it, else 1920x1080 at the best refresh rate it offers. **1080p** always asks for 1920x1080, **native** keeps the TV's own mode, and **scale:N** (1 to 3, like `scale:1.5`) keeps the TV's own mode and draws everything N times bigger. Kept as `DISPLAY_MODE`. See "A 4K TV" below. |
| `--keep-options` | Start from the options the box already has, then apply the ones given. Without it, options not given go back to their defaults. |
| `--reconfigure` | `--keep-options`, and install no packages: a new address, say. |
| `--update screen-vX.Y.Z`, `--rollback` | Move the box to a release, or back to the one before the last update. Kiosk settings runs these. |
| `--dry-run` | Show the plan; change nothing. |

Run it again at any time, for example with a new address. It rewrites its files and restarts the screen.

## 3. Pair it

1. **Turn on screens:** an admin sets **Settings → Entertainment → Media → Who may use screens**, if that isn't already done.
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
| **picture: …** | The mode the TV is in and its refresh rate (or, with the screen stopped, the TV's first mode), and the `--display` setting. |
| **start page and Kiosk settings running** / **can be a hotspot** | The helper answers, and the Wi-Fi card can offer the setup hotspot. |
| **release …** | Which screen release the box runs, from `/opt/mnemerynne-screen`. |

## 5. Wi-Fi

**When the box has no network at all** (no cable, no Wi-Fi it knows) for about 90 seconds, it starts
a hotspot called `Mnemerynne-Setup-XXXX`. The TV shows its password and two QR codes:

1. **Join the hotspot:** scan the first code with your phone's camera.
2. **Open the setup page:** most phones show "Sign in to network" and open it by themselves. If not, scan
   the second code or open `http://10.42.0.1`.
3. **Pick the network:** choose the box's network and type its password. The hotspot goes away while the
   box tries.
   - If it joined, the TV carries on to Mnemerynne.
   - If the hotspot comes back, the password was wrong. Join it again; the page says what happened.

After a power cut the router may take longer to start than the box. While nobody is on the hotspot, the
box steps aside every 3 minutes so it can rejoin the networks it already knows.

**To change networks while the box is online:** **Kiosk settings → Wi-Fi → Change network…** (step 6),
with a keyboard or air mouse. The same page lists the networks. While the box has no network at all, the
start page's **Set up Wi-Fi with a keyboard** opens it without unlocking.

Only the box itself and phones on its hotspot can reach the setup page; nothing is opened on your
network. The hotspot's password is new every time it starts.

## 6. Kiosk settings

**⚙ Kiosk settings** in the TV's menu (with a keyboard or air mouse) opens the box's settings. They are
for Mnemerynne admins:

- **With a phone:** the TV shows a code and a QR code. An admin scans it (or opens `/media/kiosk` and types
  the code), signs in if asked, and approves. The TV opens the settings.
- **With the kiosk PIN:** **Use the kiosk PIN** on the same screen. A new box has no PIN; set one in the
  settings. Without the server, the PIN is the only way in.

They lock again after ten minutes untouched, or with **Lock**.

| Setting | What it does |
| --- | --- |
| **Mnemerynne address** | The server the TV shows. The box checks the new one answers like Mnemerynne, then runs `install.sh --reconfigure` with it and restarts. The TV shows the new server's pairing code. |
| **Show the top bar** | The TV page gets a short top bar: Ask, Files and the Wiki, and **Sign in**. Whoever signs in on the TV is signed out when they go back to the TV (**📺 Back to TV**), or after ten minutes untouched. Kept on the server for this screen, so it needs a phone approval to change. |
| **Sound output** | **HDMI to the TV** (the default: it switches over as soon as the TV is on), **Automatic**, or any output the box's sound cards have, such as headphones or USB speakers. Applied at once and kept (`AUDIO_OUTPUT`). |
| **System volume** | The sound card's level, kept across restarts. Media's own volume (− and +) is on top of it. |
| **Screen size…** | For a TV that crops the picture's edges: arrows until the pink frame shows whole, Enter to save. Back to the settings after. The phone's **Screen size** (Media → Screens) does the same. |
| **Text size…** | Text and buttons on the TV's pages, bigger or smaller: ← → until it reads well from the couch, **0** for automatic, Enter to save. Back to the settings after. Kept on the server for this screen; the phone's **Text size** (Media → Screens) does the same. Automatic is normal size on a 1080p picture, 1.5× from 2400 pixels wide and 2× from 3000. |
| **Updates** | The installed `screen-v*` release, newer ones with their notes, **Install**, and **Roll back** to the one before the last update. Only newer releases install, and never over changes made by hand in `/opt/mnemerynne-screen`. **Install system updates now** runs `dnf5 upgrade`; **Reboot to finish** shows when a new kernel is waiting. |
| **Kiosk PIN** | 4 to 12 digits. Five wrong tries lock it for five minutes. |
| **Wi-Fi** | The network it's on; **Change network…** lists the networks to join. |
| **This box** | Its name, addresses, system and server; **Restart the screen**, **Reboot the box**. |

**When the TV can't connect** (a new box, or a server that moved), the start page offers **Change the
Mnemerynne address** after 20 seconds. With a kiosk PIN set it asks for the PIN first. With none, anyone at
the TV can change it, so set a PIN once the box is in place.

Updates, address changes and system updates run in their own systemd unit (`mnemerynne-screen-update`,
`-config`, `-osupdate`). The TV shows "Updating the screen…" until they finish, and the settings page shows
their last lines. In full: `journalctl -u mnemerynne-screen-update`.

**On other pages** (Ask, Files, the Wiki, a document): the TV keeps its screen size, has a small bar (**📺
Back to TV**, **‹ ›**), and still hears the remote. A phone's command gives whoever is using the TV 10 seconds
to keep it (**Keep using the TV**, or Escape); then an open document is saved as it is and the TV goes back to
its page, signed out. An admin can override the countdown. Firefox opens new tabs and windows in place (the
kiosk has no tabs), and **Backspace** goes back a page.

**A 4K TV:** 4K at 60 Hz needs HDMI 2.0 all the way: the graphics card (an NVIDIA GTX 900 series, such as a
Titan X, or newer), a cable rated 18 Gbit/s ("Premium High Speed"), and a TV input with its full-bandwidth
setting on (*Input Signal Plus* on Samsung, *HDMI Ultra HD Deep Colour* on LG, *Enhanced format* on Sony).
Without that setting, many TVs offer 4K at 30 Hz only. The NUC's HD 4000 has HDMI 1.4, which tops out below 4K.

- **4K at 60 Hz:** `--display auto` keeps it, and the TV's pages pick 2× text by themselves (**Text size**).
- **4K at 30 Hz only:** `--display auto` switches to 1080p at 60 Hz. Menus, the remote's highlight and 60 fps
  YouTube judder at 30 Hz, and the TV upscales 1080p well. To keep 4K at 30 anyway (films at 24 or 30 fps look
  the same): `sudo bash install.sh --keep-options --display native`.
- **1080p:** `--display 1080p` whatever the TV offers; the pages are then normal size.

`sudo bash install.sh --check` shows the mode the TV is in and its refresh rate (**picture: …**). The start page
and Kiosk settings grow on a 4K picture too.

**Ctrl+Shift+Alt+R** on any keyboard restarts the kiosk (Firefox and cage), even when Firefox has stopped
answering: the helper reads the keyboards itself.

**Releases:** the box follows git tags named `screen-vX.Y.Z`. Tag a release only on a commit that's ready
for every box, and push the tag (`git push origin screen-v0.3.0`).

## When something's wrong

| What you see | What to do |
| --- | --- |
| A black screen | `journalctl -u mnemerynne-screen -b` shows why cage or Firefox stopped. `sudo systemctl restart mnemerynne-screen` starts it again. |
| **Connecting to … / Waiting for the network** | The box can't reach the server: check the cable and DNS, and `curl -I https://erynne.example.com/healthz`. Without any network it offers the setup hotspot within about 90 seconds. |
| No setup hotspot | `journalctl -u mnemerynne-screen-wifi -b`. `sudo bash install.sh --check` says whether the Wi-Fi card can be a hotspot. |
| Kiosk settings: "no copy of the repository" | The box was installed from copied files. Clone the repository onto it and run the installer from the clone once. |
| An update didn't finish | **Kiosk settings → Updates** shows its last lines; `journalctl -u mnemerynne-screen-update` the rest. **Roll back** returns to the release before. |
| Forgot the kiosk PIN | Approve from an admin's phone instead, then set a new one. Or over SSH: `sudo rm /etc/mnemerynne-screen.pin`. |
| A 404 page | Media is off, or the screens group isn't set (Settings → Entertainment → Media). |
| No sound | Is the TV's input on this box, and is its volume up? `sudo bash install.sh --check` says whether sound goes to HDMI. `--audio auto` leaves the choice to PipeWire. |
| Stutters on films | The GPU isn't decoding (see `--check`), or the server is transcoding something heavy. Admin → Settings → Entertainment → Media shows running transcodes. |

To use the box for something else: `sudo bash install.sh --uninstall`, and remove the screen under Media → Screens.

## What it writes

| File | What it's for |
| --- | --- |
| `/etc/mnemerynne-screen.conf` | The address and the options, so `--keep-options` and Kiosk settings keep them. |
| `/etc/mnemerynne-screen.pin` | Kiosk settings' PIN, as a scrypt hash (root only). Not there until one is set. |
| `/opt/mnemerynne-screen` | The box's own copy of the repository, at the installed release. |
| `/var/lib/mnemerynne-screen/previous` | The release before the last update, for **Roll back**. |
| `/etc/systemd/system/mnemerynne-screen.service`, `/etc/pam.d/mnemerynne-screen` | cage on tty1 with a login session. That session gives it the GPU, input and PipeWire. |
| `/usr/local/bin/mnemerynne-screen-session` | Starts Firefox. |
| `/usr/local/bin/mnemerynne-screen-audio` | Keeps sound on HDMI. |
| `/usr/local/bin/mnemerynne-screen-display` | Sets the TV's picture (`DISPLAY_MODE`) with `wlr-randr` before Firefox starts. |
| `/usr/local/share/mnemerynne-screen/` | `start.html`, the start page without the Wi-Fi setup; `user.js`, the Firefox preferences, copied in at every start. |
| `/usr/local/libexec/mnemerynne-screen-wifi`, `/etc/systemd/system/mnemerynne-screen-wifi.service` | The helper, as root: the start page (`http://127.0.0.1:9187/`), Kiosk settings (`/settings`), and the Wi-Fi setup (it starts NetworkManager's hotspot and joins networks). |
| `/etc/NetworkManager/dnsmasq-shared.d/mnemerynne-screen.conf` | On the hotspot, every name resolves to the box, so phones open the setup page. |
| firewalld zone `nm-shared` | HTTP allowed, for the setup page on the hotspot only. |
| `/etc/firefox/policies/policies.json` | Autoplay allowed for the server only; no extensions, updates, telemetry or first-run pages. It applies to every Firefox on the box, which on a dedicated box is only this one. |
| `/etc/dnf/dnf5-plugins/automatic.conf` | Nightly updates. |

The screen's key is a cookie in `/home/mnemerynne-screen/firefox`. Anyone with that folder can play
Media as the screen's owner, so treat the box like a signed-in PC. Removing the screen under Media →
Screens revokes the key.
