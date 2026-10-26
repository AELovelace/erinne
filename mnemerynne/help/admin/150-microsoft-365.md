# Microsoft 365: tenants, SharePoint, mail and Entra groups

People keep signing in with their Mnemerynne account. On Preferences they link their Microsoft 365 account(s), one per tenant, through a normal Microsoft sign-in (MFA and Conditional Access apply). That gives Mnemerynne delegated access *as that person*, so Microsoft's permissions decide what it can open. Mnemerynne never sends, deletes or moves mail and asks for no permission to.

## One app registration (home tenant)

1. Entra admin center → App registrations → New registration: `Mnemerynne`, *Accounts in any organizational directory (multitenant)*, redirect URI **Web** `https://YOUR-HOST/auth/microsoft/callback` (Access → Settings → Microsoft 365 shows the exact one).
2. Certificates & secrets → New client secret. Put the client ID and the secret in the settings file as `MNEMERYNNE_M365_CLIENT_ID` and `MNEMERYNNE_M365_CLIENT_SECRET`, restart the web app and worker.
3. API permissions → Microsoft Graph:
   - **Delegated**: `openid`, `profile`, `email`, `offline_access`, `User.Read`, `Files.Read.All`, `Mail.Read`, `Mail.Read.Shared`, `Calendars.Read`, `Mail.ReadWrite` (drafts only).
   - **Application**: `Sites.Selected` (SharePoint indexing, granted per site) and `GroupMember.Read.All` (Entra groups).

## Each tenant (Access → Settings → Microsoft 365)

1. Add it by one of its domains or its tenant ID, with a name and who may link there:
   - **Synced from our AD**: people may only link the Microsoft account whose on-premises logon name matches their linked domain account.
   - **Separate staff**: anyone may link an account they can sign in to; create their Mnemerynne user first.
2. An admin *of that tenant* opens its **Grant consent** link once and accepts. **Check** confirms the app can sign in there.
3. A tenant whose admins won't consent to an outside app can register its own single-tenant app: enter its client ID and secret on the tenant's row; it gets its own Link button on Preferences.

Linked accounts show on Preferences and in each user's dialog on Access → Accounts, where admins can unlink them. Removing a tenant unlinks everyone in it and removes its SharePoint libraries. Refresh tokens are stored encrypted with `MNEMERYNNE_SECRET_KEY`; when Microsoft stops accepting one, the account shows *needs linking again*.

## Entra groups

Every group box (Administrators, the agent group, each API, each shared mailbox) accepts Entra groups as `Tenant / Group`, suggested by the box, alongside AD groups, separated by `;`. A Mnemerynne group can *also include an Entra group* so library grants reach its members. The worker reads every tenant's groups and each linked account's memberships (nested included) every `MNEMERYNNE_M365_SYNC_SECONDS` (300) with `GroupMember.Read.All`. Memberships older than 30 minutes stop counting, so a tenant that stops answering removes rights rather than keeping stale ones. Entra groups only count through an account the person linked.

## SharePoint

Access → Settings → Microsoft 365 → SharePoint: pick the tenant, paste a site's address, tick its document libraries, add them.

1. First grant the app read access to that site in the tenant (under `Sites.Selected` it can't see any site by itself):
   ```powershell
   Connect-MgGraph -Scopes "Sites.FullControl.All"
   $site = Get-MgSite -SiteId "contoso.sharepoint.com:/sites/FrontOffice"
   New-MgSitePermission -SiteId $site.Id -BodyParameter @{roles=@("read");
     grantedToIdentities=@(@{application=@{id="<client id>"; displayName="Mnemerynne"}})}
   ```
2. Each library becomes a read-only Mnemerynne library, "Site · Library". The worker keeps it in step every few minutes (Graph delta): new and changed files are downloaded and indexed, renames and moves follow, deletions are removed. **Sync now** does it at once.
3. **Who sees what is SharePoint's call**: the library is open to people with a linked account in that tenant; each file is checked with that person's own account before it is listed, cited or opened (remembered 15 minutes); grants don't apply; Claude only sees it when its Claude switch is on, with the same per-file check.
4. Nothing is written to SharePoint. Citations open the page in Mnemerynne's viewer and offer *Open in SharePoint*.

## Mail and calendar

People in the Erynne agent group with a linked account get tools to search and read their mail, see their calendar, search SharePoint live, and draft emails and replies. Drafts are cards under the answer; **Save to Drafts** puts the draft in that account's Outlook Drafts. Nothing is sent, moved or deleted. Mail text never goes to Claude through the connector (people who use *Use External API* in chat send it under their own key); the tool log keeps message ids, not text.

**Shared mailboxes** are listed per tenant with an optional group. Exchange still decides who may open one: the person needs Full Access there too.

## The helper script

`deploy/m365/SharePointAccess.ps1` in the repository wraps the site-permission step.
