# Security feeds

Security feeds answer one question every morning: **is there a new vulnerability that is actively exploited, or critical, in something we actually run, and on which machines?** Claude's `security_feeds` tool and Erynne's *Security digest* automation both read from here.

They're set up when Mnemerynne starts. You don't build any feed by hand. Find them under **Access → Automator → Security feeds**.

## What ships

| Feed | Source | Polled | Speaks for |
|---|---|---|---|
| **CISA KEV** | `cisa.gov/.../known_exploited_vulnerabilities.json` | every 6 h | every product below, by vendor and product |
| **NVD CVEs** | NVD CVE API 2.0, a `lastModStartDate`/`lastModEndDate` window | every 2 h | CVEs matching our CPEs (or keywords, before NVD has analysed them) with CVSS 7 or more |
| **Microsoft (MSRC)** | the security update guide's CVRF documents: the latest two (monthly and out-of-band) | daily | Windows 10/11, the Windows Server versions we run, .NET, Microsoft 365 Apps / Office, Edge |
| **Mozilla advisories** | mozilla.org's advisories page, and each new Firefox advisory's page for its CVEs (Mozilla has no feed) | daily | Firefox, Firefox ESR |
| **GitHub advisories: git/git** and **git-for-windows/git** | GitHub's repository security advisories API | daily | Git, Git for Windows |
| **nginx.org advisories** | `nginx.org/en/security_advisories.html` | daily | nginx |
| **Oracle CPU and Security Alerts** | Oracle's security RSS | daily | Oracle Hospitality (OPERA 5, IFC8) |
| **Ubiquiti security bulletins** | *off* | – | Ubiquiti's community site only renders in a browser and has no RSS. UniFi CVEs still arrive through NVD (the `ui` CPEs) and KEV. |

Each default is put in **once**. If you switch one off, change it or delete it, it stays that way after updates. Feeds you add yourself sit beside them.

## What you enter

1. **The NVD API key.** On the **NVD CVEs** row, choose **Enter API key**. Get one free at nvd.nist.gov/developers/request-an-api-key. It's stored encrypted with `MNEMERYNNE_SECRET_KEY` and never shown again. Without a key NVD still works, but it allows far fewer requests, so polls are slower and fewer CVEs are looked up.
2. **The Windows Server versions we run.** Under **Products we watch**, choose **Edit** on **Windows** and remove the CPEs and keywords of the versions we don't run. The defaults are 2012 R2, 2016, 2019 and 2022. We run at least 2012 R2 (WAPSCO-SVR-001) and 2019 (Win2019SRVR).
3. **The Wazuh preset.** For *affects us*, add the **Wazuh** preset under **Access → APIs** if it isn't there yet (see [Wazuh](wazuh.md)). Security feeds read its *Installed packages* index with that API's own address, credential and trusted CA.

## Products we watch

Each product has three kinds of match key:

| Key | Matches | Notes |
|---|---|---|
| **CPEs** | NVD records | CPE 2.3, compared part by part. A `*` inside a part matches anything, so `cpe:2.3:o:microsoft:windows_server_2012*` covers R2. |
| **Keywords** | KEV's vendor and product, vendor titles, MSRC product names | Whole words, any case. ALL-CAPS keywords match exactly, so `OPERA` doesn't catch the Opera browser. In NVD descriptions only longer keywords are used, so plain "Git" doesn't catch every CVE that mentions a git repository. |
| **Wazuh packages** | package names in Wazuh's inventory | `*` wildcards, any case, e.g. `Mozilla Firefox*`. Windows itself and Oracle IFC8 have none, so their items say `no-inventory`. |

The plan's "worth adding later" products ship **off**: Wazuh, Suricata, OPNsense, Proxmox VE, Synology DSM, Veeam, Vaultwarden (switch it on once T-23 is live), the GravityZone agent, the Atera agent and OpenSSH. Choose **Turn on** to watch one.

## One item per CVE

The same CVE arrives from NVD, KEV and the vendor. Mnemerynne keeps what each source said and merges them into **one item** that lists all its `sources`:

- **Title, fixed version and link:** the vendor's first.
- **CVSS:** NVD's, falling back to the vendor's.
- **KEV fields:** from KEV.
- **Published:** the earliest date any source gives.

An advisory without a CVE (an Oracle CPU announcement) is an item of its own, with `cve` empty.

Every item has the same fields with the same types:

```json
{"cve": "CVE-2024-32002", "title": "…", "product": "Git for Windows", "vendor": "Git",
 "sources": ["github", "nvd"], "published": "2024-05-14T19:15:10Z", "first_seen": "2026-11-19T07:00:00Z",
 "cvss": 9.0, "cvss_vector": "CVSS:3.1/…", "severity": "critical", "kev": false, "kev_due_date": "",
 "kev_ransomware": "", "fixed_in": "2.39.4, 2.40.2, …, 2.45.1", "link": "https://…", "summary": "…",
 "affects_us": {"machines": 13, "agents": ["EH-AC-1", "…"], "versions": ["2.44.0.windows.1"],
                "version_check": "compared", "unclear_agents": []}}
```

- `cvss` is a number, or `null` when no source scored it.
- The other unknown values are empty strings, and lists are always lists.
- `fixed_in` may list one version per release branch. An install counts as fixed at or above the newest one, or at or above the one for its own major.minor branch (2.44.1 fixes 2.44.x).

## Affects us

Each item is joined with Wazuh's **Installed packages** at read time. The inventory is cached for 15 minutes. Only installs below `fixed_in` count. `version_check` says how sure the count is:

| `version_check` | Meaning |
|---|---|
| `compared` | Installed versions were compared with `fixed_in`. |
| `no-fixed-version` | No source gave a fixed version, so every install of the product is counted. |
| `distro-backport-unknown` | Some installs are distribution builds older than the fix, e.g. Ubuntu's `1.24.0-2ubuntu7.19` against nginx `1.30.1`. Distributions backport fixes, so those machines aren't counted. They're listed in `unclear_agents` for you to check. |
| `no-inventory` | No Wazuh preset is set up, or the product has no Wazuh package names. |
| `inventory-error` | Wazuh's indexer didn't answer. Its row in `feed_status` says why. |

## Feed health: never silent

Every answer carries `feed_status`, one entry per feed: `{feed, last_success, last_attempt, error, items_last_fetch, next_poll}`. There's also an entry for **Wazuh inventory (affects_us)**. An empty list of items means "nothing new" only when no enabled feed has an `error`; the answer's `note` names any broken ones. The admin page shows the same health, with **Poll now** on each feed.

## Polling and limits

- The feeds are polled by Automator's scheduler (the automations service, or the web app), each on its own interval, and also when someone reads them. You can change a feed's interval with **Edit**. Feeds are polled whether Automator is on or off.
- **403 or 429:** the feed backs off. It honours `Retry-After`, then waits 15 minutes, doubling up to a day.
- **Other failures** retry sooner, never later than the feed's own interval.
- NVD pauses between pages within its rate limit: about 0.7 s with a key, 6.5 s without.
- **History:** items are kept for at least 90 days, so `since_days=90` works. Items quiet for 120 days that no feed lists any more are deleted.
- **nginx.org's page has no dates.** What's on it at the first poll counts as history. An advisory that appears later counts as new on the day it appears.

## Certificates

Outbound fetches trust the **certificate authorities**: the system's bundle (httpx `verify=True`). No leaf certificate is pinned, so a vendor's routine renewal doesn't break a feed. Leaf pinning broke Gasco on 2026-11-16 and Atera on 2026-11-19. A certificate failure shows in `feed_status` as "couldn't verify the server's certificate".

## Wazuh alerts

A **new** item that **affects us** (`affects_us.machines > 0`) and is either **in KEV** or **CVSS 9 or more** gets one JSON line in a log file. It's written once, never again for that item, no matter how often the feeds are polled. Only items active in the last 30 days are pushed, so the first poll's backfill isn't an alarm. When Wazuh's inventory can't be read, nothing is pushed until it can.

```json
{"integration": "security-feeds", "event": "kev_affects_us", "cve": "CVE-2026-50001", "title": "…",
 "product": "Firefox", "vendor": "Mozilla", "cvss": 8.8, "severity": "high", "kev": true,
 "kev_due_date": "2026-12-10", "kev_ransomware": "Known", "fixed_in": "147.0", "machines": 12,
 "agents": ["…"], "versions": ["146.0.1"], "version_check": "compared", "sources": ["kev", "mozilla", "nvd"],
 "link": "https://…", "first_seen": "…", "at": "…"}
```

`event` is `kev_affects_us` or `critical_affects_us` (CVSS 9 or more, not in KEV).

The log is `<data_dir>/logs/security-feeds.json`. Set `MNEMERYNNE_SECURITY_FEEDS_LOG` to put it elsewhere. The Wazuh agent on this server reads it with:

```xml
<localfile>
  <log_format>json</log_format>
  <location>/var/lib/mnemerynne/logs/security-feeds.json</location>
</localfile>
```

Use the path the admin page shows. The rules are a separate job, in the reserved block **100350–100359**:

| Level | When |
|---|---|
| 12 | KEV and affects us |
| 10 | CVSS 9 or more and affects us |
| 5 | anything else that's pushed |

## The tool

```
security_feeds(since_days=7, product=None, min_cvss=None, kev_only=False, affects_us_only=False)
```

- `product` matches a product's key, name or vendor, e.g. `"nginx"`, `"git"` or `"Microsoft"`.
- Items come most urgent first: KEV and affecting us, then CVSS 9 or more and affecting us, then by score.
- At most 200 items are returned. `truncated` says when there were more.

The morning brief calls `security_feeds(kev_only=True)` and `security_feeds(affects_us_only=True, min_cvss=7)`.
