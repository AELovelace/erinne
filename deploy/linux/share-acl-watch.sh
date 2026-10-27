#!/usr/bin/env bash
# Run by mnemerynne-share-acl.service, as root: whenever a folder appears on the
# SMB share (made there, or copied or moved in), give the service user read and
# write on it and everything already in it, and make that the folder's default
# for what's added later. New folders normally inherit this from their parent,
# but not when Windows replaces a folder's permissions or a folder is moved in
# from elsewhere, and then Mnemerynne can't see it. Files moved in on their own
# get the entry too. Hidden paths (Mnemerynne's own data among them) are left
# alone, as the share scan ignores them anyway.
#
# Root, because only a file's owner may change its ACL and people own what they
# put on the share. It only ever adds the mnemerynne entry.
set -euo pipefail

ROOT=${MNEMERYNNE_SHARE_ROOT:-}
ROOT=${ROOT%/}
ENTRY=u:mnemerynne:rwX

if [[ -z $ROOT ]]; then
    echo "MNEMERYNNE_SHARE_ROOT isn't set in /etc/mnemerynne/mnemerynne.env; no share to watch."
    exit 0
fi
for command in inotifywait setfacl; do
    command -v "$command" >/dev/null || { echo "Missing $command: sudo dnf install inotify-tools acl" >&2; exit 1; }
done
# Like the worker: no marker, no share (the array isn't mounted). The unit tries again later.
[[ -e $ROOT/.mnemerynne-share ]] || { echo "$ROOT/.mnemerynne-share is missing (share not mounted?)" >&2; exit 1; }

echo "Watching $ROOT for new folders"
# -r watches every folder below the root, and each new one as it appears.
inotifywait -m -r -q -e create -e moved_to --exclude '/\.' --format '%e %w%f' -- "$ROOT" |
while IFS= read -r line; do
    events=${line%% *}
    path=${line#* }
    if [[ ,$events, == *,ISDIR,* ]]; then
        # Recursive: whatever landed inside before the folder was seen.
        setfacl -R -m "$ENTRY" -m "d:$ENTRY" -- "$path" && echo "rw for mnemerynne: $path" || true
    elif [[ $events == MOVED_TO ]]; then
        setfacl -m "$ENTRY" -- "$path" || true
    fi
done

echo "Stopped watching $ROOT (unmounted, or too many folders for fs.inotify.max_user_watches?)" >&2
exit 1
