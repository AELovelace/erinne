#!/usr/bin/env bash
# Adds a share-access alias to the existing NAS DC without renaming or restarting it.
set -Eeuo pipefail
trap 'printf "Alias setup stopped at line %s; paste the error before continuing.\n" "$LINENO" >&2' ERR
die() { printf 'STOP: %s\n' "$*" >&2; exit 1; } # Stops on a conflicting identity or DNS name.

[[ $EUID == 0 ]] || die 'Run with sudo bash.'
for command in samba-tool testparm dig; do
    command -v "$command" >/dev/null || die "Missing command: $command"
done
[[ $(testparm -s --parameter-name='server role' 2>/dev/null) == 'active directory domain controller' ]] || die 'This must run on the existing AD DC.'
[[ $(testparm -s --parameter-name='netbios name' 2>/dev/null) == NAS ]] || die 'Expected the existing NAS computer identity.'
[[ $(testparm -s --parameter-name=realm 2>/dev/null) == SADGIRLSCLUB.LOCAL ]] || die 'Unexpected domain.'

zone=sadgirlsclub.local
alias_name=mnemerynne
target=nas.sadgirlsclub.local
dns_server=192.168.1.5
account='NAS$'

# Check the live DNS response before creating anything; existing conflicting names are never replaced.
reply=$(dig +time=3 +tries=1 +noall +comments +answer @"$dns_server" "$alias_name.$zone" CNAME)
grep -Eq 'status: (NOERROR|NXDOMAIN),' <<< "$reply" || die 'The domain DNS server did not return a usable response.'
existing=$(awk '$4 == "CNAME" {print tolower($5)}' <<< "$reply")
[[ -z $existing || $existing == "$target." ]] || die "The alias already points to $existing"

# Register both short and qualified SMB names on the existing machine account.
# samba-tool refuses SPNs owned by another account; do not use its --force option.
spns=$(samba-tool spn list "$account")
for spn in "cifs/$alias_name" "cifs/$alias_name.$zone"; do
    if sed 's/^[[:space:]]*//;s/[[:space:]]*$//' <<< "$spns" | grep -Fxiq -- "$spn"; then
        printf 'Already registered: %s\n' "$spn"
    else
        samba-tool spn add "$spn" "$account"
    fi
done

if [[ -z $existing ]]; then
    printf '\nEnter the SADGIRLSCLUB domain Administrator password for the DNS change.\n'
    samba-tool dns add "$dns_server" "$zone" "$alias_name" CNAME "$target" -U 'SADGIRLSCLUB\Administrator'
fi

# Verify the DNS alias and its destination without opening or changing share files.
resolved_alias=$(dig +time=3 +tries=1 +short @"$dns_server" "$alias_name.$zone" CNAME)
[[ ${resolved_alias,,} == "$target." ]] || die 'The new DNS alias could not be verified.'
dig +time=3 +tries=1 +short @"$dns_server" "$alias_name.$zone" A | grep -Fx "$dns_server"
printf '\nAlias ready: \\\\mnemerynne.sadgirlsclub.local\\share\n'
printf 'Short form: \\\\mnemerynne\\share (requires the sadgirlsclub.local DNS search suffix).\n'
printf 'Clients must use the domain DNS server at 192.168.1.5. No service was restarted.\n'
