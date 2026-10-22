<#
.SYNOPSIS
    List SharePoint sites (including every Microsoft 365 group's and Team's site) and grant or
    revoke the Mnemerynne app's access to them (Sites.Selected).

.DESCRIPTION
    Sign in as a SharePoint admin. The script lists every site it can find, shows whether the app
    can read it, and lets you grant or revoke access by number.

    Interactive commands:
      g 1,3-5     grant the listed sites
      r 2         revoke
      f hotel     filter by name, group or URL (f alone clears it)
      a           show only sites the app can reach (a again: all)
      l           reload from Microsoft 365
      q           quit

.EXAMPLE
    .\SharePointAccess.ps1 -AppId 1a2b3c4d-0000-0000-0000-000000000000
.EXAMPLE
    .\SharePointAccess.ps1 -AppId 1a2b... -List | Export-Csv sites.csv -NoTypeInformation
.EXAMPLE
    .\SharePointAccess.ps1 -AppId 1a2b... -SitesCsv ..\..\data\Sites_20261022203100063.csv
#>
[CmdletBinding()]
param(
    [Parameter(Mandatory)] [ValidatePattern('^[0-9a-fA-F-]{36}$')] [string] $AppId,
    [ValidateSet('read', 'write')] [string] $Role = 'read',
    [switch] $List,   # print the sites and their access, then exit
    # The admin centre's site list (Active sites -> Export). Graph only finds sites you can open; this has them all.
    [ValidateScript({ Test-Path $_ })] [string] $SitesCsv
)

$ErrorActionPreference = 'Stop'
foreach ($m in 'Microsoft.Graph.Authentication', 'Microsoft.Graph.Sites', 'Microsoft.Graph.Groups') {
    if (-not (Get-Module -ListAvailable $m)) { throw "Missing module $m. Run: Install-Module Microsoft.Graph -Scope CurrentUser" }
}
Connect-MgGraph -Scopes 'Sites.FullControl.All', 'Group.Read.All' -NoWelcome
$appName = (Get-MgServicePrincipal -Filter "appId eq '$AppId'" -Property DisplayName).DisplayName
if (-not $appName) { throw "No app with ID $AppId in this tenant." }

function Get-AppPermission([string] $SiteId) {
    # The site's permission entry for our app, if any.
    Get-MgSitePermission -SiteId $SiteId -All -ErrorAction Stop | Where-Object {
        ($_.GrantedToIdentitiesV2 | ForEach-Object { $_.Application.Id }) -contains $AppId -or
        ($_.GrantedToIdentities | ForEach-Object { $_.Application.Id }) -contains $AppId
    } | Select-Object -First 1
}

function New-SiteRow($Id, $Name, $Group, $Url, $Access = '') {
    [pscustomobject]@{ Id = $Id; Name = $Name; Group = $Group; Url = $Url; Access = $Access; PermissionId = $null }
}

function Get-Sites {
    $byId = @{}
    Write-Host 'Finding sites...' -ForegroundColor DarkGray
    # Every site the search can see (only ones you can open)...
    try {
        foreach ($s in Get-MgSite -Search '*' -All -Property Id, DisplayName, Name, WebUrl -ErrorAction Stop) {
            $byId[$s.Id] = New-SiteRow $s.Id ($s.DisplayName, $s.Name | Where-Object { $_ } | Select-Object -First 1) '' $s.WebUrl
        }
    } catch { Write-Warning "Site search failed: $($_.Exception.Message)" }
    # ...plus each Microsoft 365 group's site (Teams sites don't always show up in search).
    # Groups whose site you can't open (you're not a member or owner) are listed as "denied".
    $groups = Get-MgGroup -Filter "groupTypes/any(c:c eq 'Unified')" -All -Property Id, DisplayName
    foreach ($g in $groups) {
        try { $s = Get-MgGroupSite -GroupId $g.Id -SiteId root -Property Id, DisplayName, WebUrl -ErrorAction Stop }
        catch {
            $byId["group:$($g.Id)"] = New-SiteRow $null $g.DisplayName $g.DisplayName '(you cannot open this site)' 'denied'
            continue
        }
        if (-not $s -or -not $s.Id) { continue }
        if ($byId.ContainsKey($s.Id)) { $byId[$s.Id].Group = $g.DisplayName; continue }
        $byId[$s.Id] = New-SiteRow $s.Id $s.DisplayName $g.DisplayName $s.WebUrl
    }
    # ...plus every site in the admin centre's export (Active sites -> Export), which lists them all.
    if ($SitesCsv) {
        $known = @{}
        foreach ($r in $byId.Values) { if ($r.Url -like 'https://*') { $known[$r.Url.TrimEnd('/').ToLower()] = $r } }
        foreach ($row in Import-Csv $SitesCsv) {
            $url = "$($row.URL)".Trim().TrimEnd('/')
            if (-not $url -or $known.ContainsKey($url.ToLower())) { continue }
            # The first column is "Site name" (its header may carry a stray byte-order mark).
            $name = ($row.PSObject.Properties | Select-Object -First 1).Value
            $isGroup = $row.'Microsoft 365 group' -eq 'True'
            $uri = [uri]$url
            $path = $uri.AbsolutePath.TrimEnd('/')
            try {
                $s = Get-MgSite -SiteId ($(if ($path) { "$($uri.Host):$path" } else { $uri.Host })) -Property Id, DisplayName, WebUrl -ErrorAction Stop
                if ($byId.ContainsKey($s.Id)) { continue }
                $byId[$s.Id] = New-SiteRow $s.Id $s.DisplayName '' $s.WebUrl
            } catch {
                # A group site we already listed as denied: give it its URL rather than a second row.
                $dup = if ($isGroup) { $byId.Values | Where-Object { -not $_.Id -and $_.Name -eq $name -and $_.Url -notlike 'https://*' } | Select-Object -First 1 }
                if ($dup) { $dup.Url = $url }
                else { $byId["url:$url"] = New-SiteRow $null $name $(if ($isGroup) { $name } else { '' }) $url 'denied' }
            }
            $known[$url.ToLower()] = $true
        }
    }
    $sites = @($byId.Values | Sort-Object Name)
    $i = 0
    foreach ($s in $sites) {
        $i++
        if (-not $s.Id) { continue }
        Write-Progress -Activity "Checking $appName's access" -Status $s.Name -PercentComplete (100 * $i / $sites.Count)
        try {
            $p = Get-AppPermission $s.Id
            if ($p) { $s.Access = ($p.Roles -join ','); $s.PermissionId = $p.Id }
        } catch { $s.Access = '?' }
    }
    Write-Progress -Activity "Checking $appName's access" -Completed
    $sites
}

function Expand-Selection([string] $Text, [int] $Max) {
    # "1,3-5" -> 1,3,4,5
    foreach ($part in $Text -split '[,\s]+' | Where-Object { $_ }) {
        if ($part -match '^(\d+)-(\d+)$') { [int]$matches[1]..[int]$matches[2] }
        elseif ($part -match '^\d+$') { [int]$part }
        else { Write-Warning "Skipping '$part'" }
    }
}

function Grant-Site($Site) {
    if (-not $Site.Id) {
        Write-Warning "$($Site.Name): make yourself a site admin first (SharePoint admin centre -> Active sites -> the site -> Permissions -> Site admins), then reload (l)."
        return
    }
    if ($Site.PermissionId) {
        Update-MgSitePermission -SiteId $Site.Id -PermissionId $Site.PermissionId -Roles @($Role) -ErrorAction Stop | Out-Null
    } else {
        $p = New-MgSitePermission -SiteId $Site.Id -ErrorAction Stop -BodyParameter @{
            roles = @($Role)
            grantedToIdentities = @(@{ application = @{ id = $AppId; displayName = $appName } }) }
        $Site.PermissionId = $p.Id
    }
    $Site.Access = $Role
    Write-Host "  granted $Role : $($Site.Name)" -ForegroundColor Green
}

function Revoke-Site($Site) {
    if (-not $Site.PermissionId) { Write-Host "  no access to revoke: $($Site.Name)" -ForegroundColor DarkGray; return }
    Remove-MgSitePermission -SiteId $Site.Id -PermissionId $Site.PermissionId -ErrorAction Stop
    $Site.Access = ''; $Site.PermissionId = $null
    Write-Host "  revoked: $($Site.Name)" -ForegroundColor Yellow
}

$sites = Get-Sites
if ($List) { $sites | Select-Object Name, Group, Url, Access; return }

$filter = ''; $onlyGranted = $false
while ($true) {
    $shown = @($sites | Where-Object {
        (-not $onlyGranted -or $_.PermissionId) -and
        (-not $filter -or "$($_.Name) $($_.Group) $($_.Url)" -like "*$filter*")
    })
    Write-Host ''
    Write-Host ("{0,4}  {1,-6} {2,-40} {3,-28} {4}" -f '#', 'Access', 'Site', 'Group', 'URL') -ForegroundColor Cyan
    for ($n = 0; $n -lt $shown.Count; $n++) {
        $s = $shown[$n]
        $color = if ($s.Access -eq 'denied') { 'Red' } elseif ($s.PermissionId) { 'Green' } else { 'Gray' }
        $name = if ($s.Name.Length -gt 40) { $s.Name.Substring(0, 39) + '~' } else { $s.Name }
        $group = if ($s.Group.Length -gt 28) { $s.Group.Substring(0, 27) + '~' } else { $s.Group }
        Write-Host ("{0,4}  {1,-6} {2,-40} {3,-28} {4}" -f ($n + 1), $s.Access, $name, $group, $s.Url) -ForegroundColor $color
    }
    $granted = @($sites | Where-Object PermissionId).Count
    Write-Host ("{0} of {1} sites shown; {2} can reach {3}." -f $shown.Count, $sites.Count, $appName, $granted) -ForegroundColor DarkGray
    $cmd = (Read-Host "g <#> grant $Role | r <#> revoke | f <text> filter | a granted only | l reload | q quit").Trim()
    if (-not $cmd) { continue }
    $verb = $cmd.Substring(0, 1).ToLower(); $rest = $cmd.Substring(1).Trim()
    switch ($verb) {
        'q' { return }
        'l' { $sites = Get-Sites }
        'a' { $onlyGranted = -not $onlyGranted }
        'f' { $filter = $rest }
        { $_ -in 'g', 'r' } {
            foreach ($i in Expand-Selection $rest $shown.Count) {
                if ($i -lt 1 -or $i -gt $shown.Count) { Write-Warning "No site #$i"; continue }
                try { if ($verb -eq 'g') { Grant-Site $shown[$i - 1] } else { Revoke-Site $shown[$i - 1] } }
                catch { Write-Warning "$($shown[$i - 1].Name): $($_.Exception.Message)" }
            }
        }
        default { Write-Warning "Unknown command '$cmd'" }
    }
}
