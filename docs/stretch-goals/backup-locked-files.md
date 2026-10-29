# Stretch goal: backing up locked files

*Written 2026-10-29 by Claude for Doll. A stretch goal: nothing here is scheduled or started. It comes out of the
desktop sync client plan (`docs/plans/desktop-sync-client.md`), where locked files are skipped for now.*

**The problem:** some files are open with an exclusive lock for as long as their program runs: Outlook's `.pst` and
`.ost`, QuickBooks and other desktop databases, virtual machine disks. The backup app can't read them, so it lists
them as "in use, not backed up" and tries again at the next pass. For someone who never closes Outlook, that means
their mail never gets backed up.

## The idea (Windows)
Read locked files from a **Volume Shadow Copy** snapshot, the way Windows Backup and robocopy `/B` do:

1. At the start of a pass that found locked files, ask a small **helper service** for a snapshot of that volume.
2. Read the locked files from the snapshot (`\\?\GLOBALROOT\Device\HarddiskVolumeShadowCopyN\…`), so the copy is
   consistent as of that moment.
3. Release the snapshot when the pass ends.

**Why a helper:** creating a snapshot needs administrator rights, and the app itself installs per user without them.
The helper is a Windows service, installed once by an admin. It only answers "snapshot this volume" and "release it",
from the backup app on the same machine, and never reads files itself.

## Linux
Usually not needed: Linux doesn't lock files the same way, so the app can read them. For files that change while
being read (databases), an LVM or Btrfs snapshot would do the same job; only if someone asks.

## Before starting
- Is it worth it? Check how many people have big `.pst` files. Moving mail to the server (M365) may be the better
  fix.
- A code-signing certificate for the helper; an unsigned service is a harder sell to install.
- Large `.pst` files change a little every day but are re-uploaded whole. This may want block-level deltas first.

## Size
2–3 evenings for the helper and the app's use of it, plus testing on real Outlook profiles.
