# DC-2: RDS roaming profiles in Mnemerynne home folders

## Verified layout

- DC-2.sadgirlsclub.local is the Windows Server 2019 domain controller and RD Session Host. It also has the RD Connection Broker role.
- M: in the administrator's session maps to \\mnemerynne.sadgirlsclub.local\share.
- Existing M:\Users\<username> folders match SADGIRLSCLUB sign-in names. The pilot account is aedith.
- Samba serves [share] from /srv/share/Files, with writes enabled for SADGIRLSCLUB\Domain Users and inherited ACLs.
- QuickSessionCollection has User Profile Disks disabled. No RDS roaming-profile or FSLogix profile policy was found in the checks supplied.
- The ordinary AD ProfilePath, HomeDirectory, and HomeDrive attributes for aedith are blank. A Windows icacls query against the Samba home returned "The parameter is incorrect", so it did not establish the folder ACL.
- The NAS ACL on Users/aedith grants rwx to aedith and mnemerynne, with the same default ACLs for new files and folders; group and other have no access.
- tsprof /q reports an empty RDS profile path for aedith. The fQueryUserConfigFromDC registry value is absent on DC-2.
- A tsprof /update attempt saved a single leading backslash and was corrected through ADSI. The verified current RDS profile path is the intended UNC path, and a direct ADSI check returned StartsUNC=True. RCM is still disabled, so this profile path is not yet active on DC-2.
- DC-2 is in OU=Domain Controllers. The current aedith local profile is C:\Users\aedith. The aedith sessions were signed out, and quser showed only the Administrator session; verify Loaded=False before backing it up.
- A reported GetFolderPath('MyDocuments') result was C:\Users\aedith (the profile root), while C:\Users\aedith\Documents contains one 402-byte file. Direct registry inspection found the raw User Shell Folders Personal value is REG_EXPAND_SZ %USERPROFILE%\Documents, and Shell Folders Personal is C:\Users\aedith\Documents. The apparent Administrator path from Get-ItemProperty was expansion in Administrator's process. These results conflict with the reported GetFolderPath value; leave automatic content moving off for the pilot and copy the known local Documents file after redirecting.
- After aedith signed out, Win32_UserProfile reported Loaded=False and C: had 101.82 GiB free. Robocopy copied 407 files (57.68 MB) from C:\Users\aedith to C:\Users\Administrator.SADGIRLSCLUB\RDS-Pilot-Backup\aedith-2026-10-28 with 0 failed files and exit code 1 (files copied).

Use the same existing SMB share and each user's existing home folder:

| Data | Path for aedith |
| --- | --- |
| Windows Documents | \\mnemerynne.sadgirlsclub.local\share\Users\aedith\Documents |
| RDS roaming profile base path | \\mnemerynne.sadgirlsclub.local\share\Users\aedith\.rds-profile |
| Expected Server 2019 profile folder | \\mnemerynne.sadgirlsclub.local\share\Users\aedith\.rds-profile.V6 |

The last path is the expected result of Windows profile versioning; verify the actual folder during the pilot. Mnemerynne's share scanner ignores names beginning with a dot, so the profile folder is omitted from the File box. The Documents folder remains visible in Mnemerynne. Do not set the profile base path to the home folder itself: that could mingle NTUSER.DAT and AppData with documents. Use UNC paths because mapped drives may not exist when a profile loads.

Microsoft recommends a separate share for profiles and redirected folders because Windows Offline Files might cache the profile folder. With this single-share layout, check whether anyone uses Offline Files on the share. If nobody needs offline access, Samba's share-level csc policy = disable prevents client-side caching; it also disables offline caching for Documents on this share. Do not change that setting until its effect on current users is understood.

**Path limitation:** The RDS computer policy takes one root path and appends the account name. Pointing it at \\mnemerynne.sadgirlsclub.local\share\Users would create Users\aedith.V6 beside the existing Users\aedith home folder. To nest each profile inside its own home folder, use the per-account **Remote Desktop Services Profile** path and enable the Server 2019 RCM lookup described below. This is distinct from the ordinary **Profile** tab, which applies roaming profiles to other Windows sign-ins too.

## 1. Pilot the home-folder permissions and Documents

From an aedith RDS session, open \\mnemerynne.sadgirlsclub.local\share\Users\aedith and create, reopen, then remove a small test file. Confirm another ordinary user cannot open this folder. Preserve the existing home ACLs: Mnemerynne requires Unix User\mnemerynne to retain Modify access. Confirm aedith already has a Mnemerynne File box attached before creating profile data in that home folder; the app does not adopt a pre-existing nonempty folder as a new File box.

Read the existing ACL directly on nas with sudo getfacl -p /srv/share/Files/Users/aedith. On DC-2, tsprof /q /domain:SADGIRLSCLUB aedith reports the current per-user RDS profile path. Read the current RCM setting with reg query "HKLM\SOFTWARE\Policies\Microsoft\Windows NT\Terminal Services" /v fQueryUserConfigFromDC. These are read-only checks; inspect their output before changing the profile.

The raw aedith User Shell Folders Personal value is already the Windows default, %USERPROFILE%\Documents. No registry repair is indicated. Back up the current profile before changing Folder Redirection. Because the reported GetFolderPath result conflicts with the registry and Shell Folders values, disable automatic content moving for this pilot and copy the known local Documents file manually after the redirect is verified.

On DC-2, create a computer GPO linked to the **Domain Controllers** OU and security-filter it to DC-2$ with **Read** and **Apply Group Policy**. Keep **Authenticated Users** with **Read** only on the Delegation tab. Enable **Computer Configuration > Policies > Administrative Templates > System > Group Policy > Configure user Group Policy loopback processing mode** in **Merge** mode.

Create a security group containing only aedith, then a user GPO linked to the same OU. Filter this user GPO to the pilot group and keep **Authenticated Users** with **Read** only on Delegation. In **User Configuration > Policies > Windows Settings > Folder Redirection > Documents > Properties** choose:

- **Basic — Redirect everyone's folder to the same location**.
- **Create a folder for each user under the root path**, with root \\mnemerynne.sadgirlsclub.local\share\Users.
- Clear **Grant the user exclusive rights** to preserve the existing service access and home ACL. For aedith, clear **Move the contents of Documents to the new location**. Copy the file from C:\Users\aedith\Documents to the new UNC Documents folder after verifying that the redirect works; leave the local source in place until the network copy has been reopened and checked.
- **Policy Removal: Leave the folder in the new location**.

After gpupdate /force on DC-2, sign aedith out and in. In that session, run each check on one line:

~~~powershell
gpresult /r /scope user
[Environment]::GetFolderPath('MyDocuments')
Test-Path '\\mnemerynne.sadgirlsclub.local\share\Users\aedith\Documents'
~~~

The Documents path should be the UNC path in the table. Create and reopen a test document, sign out and in, and confirm that it remains in Mnemerynne's File box. Folder Redirection covers only the known folders selected in policy, not arbitrary application paths.

## 2. Pilot the classic RDS roaming profile in the same home folder

On Server 2019, the RD Session Host does not read the per-user **Remote Desktop Services Profile** tab by default. Microsoft documents enabling that lookup with fQueryUserConfigFromDC=1; changing it affects how DC-2 reads all per-user RDS settings. First save work and fully sign out every aedith RDS session. Back up the local profile while it is unloaded. Schedule a maintenance window because Remote Desktop Services must restart, and record the previous registry value for rollback. Run these separately on DC-2 in an elevated command prompt:

~~~cmd
reg query "HKLM\SOFTWARE\Policies\Microsoft\Windows NT\Terminal Services" /v fQueryUserConfigFromDC
reg add "HKLM\SOFTWARE\Policies\Microsoft\Windows NT\Terminal Services" /v fQueryUserConfigFromDC /t REG_DWORD /d 1 /f
~~~

Restart **Remote Desktop Services** during the window, or reboot DC-2 through the usual domain-controller maintenance process. A reboot is the clearer verification point on this all-roles domain controller. Do not set the **Set path for Remote Desktop Services Roaming User Profile** computer policy; it overrides per-account paths.

In **Active Directory Users and Computers > aedith > Properties > Remote Desktop Services Profile**, set **Profile Path** to \\mnemerynne.sadgirlsclub.local\share\Users\aedith\.rds-profile. This is the base path; Windows Server 2019 should use .rds-profile.V6. Do not manually create the profile folder; let Windows create and own it. Keep aedith signed out during the change and back up the existing local profile data that matters before the first roaming sign-in.

Use ADSI for a scripted update. In this environment tsprof /update saved only one leading backslash, producing an invalid UNC path. The command below was used to correct the pilot and verify the stored value. This change stores the per-user path in AD; it does not activate the path on DC-2 until the RCM lookup is enabled and Remote Desktop Services restarts.

~~~powershell
$ErrorActionPreference='Stop'; $u=[ADSI]('LDAP://'+(Get-ADUser aedith).DistinguishedName); $target='\\mnemerynne.sadgirlsclub.local\share\Users\aedith\.rds-profile'; if(-not $target.StartsWith('\\')){throw 'Invalid UNC'}; $u.psbase.InvokeSet('TerminalServicesProfilePath',$target); $u.psbase.CommitChanges(); $u.psbase.RefreshCache(); $p=[string]$u.psbase.InvokeGet('TerminalServicesProfilePath'); "Path=[$p]; StartsUNC=$($p.StartsWith('\\'))"
~~~

Sign aedith in to DC-2, make a harmless desktop or application-setting change, and sign out fully. Confirm that .rds-profile.V6 appears under the existing home folder, contains profile files including NTUSER.DAT, and that its file timestamps update at sign-out. In **System Properties > Advanced > User Profiles > Settings**, confirm the profile type is **Roaming**. Sign in again and verify the setting persists and Documents still points to the UNC path above. If profile type stays **Local**, check RCM, RDS policy precedence, and **Event Viewer > Applications and Services Logs > Microsoft > Windows > User Profile Service > Operational** before expanding the rollout. Also inspect **Folder Redirection > Operational** if Documents fails.

## 3. Roll out to the remaining RDS users

For each RDS account, verify its existing Users\<sign-in-name> home, its write access, and its Mnemerynne File box. Back up existing local profile and Documents data. Set that account's **Remote Desktop Services Profile > Profile Path** to \\mnemerynne.sadgirlsclub.local\share\Users\<sign-in-name>\.rds-profile. Windows should create a separate .rds-profile.V6 inside that user's existing home. Test sign-in, sign-out, and a second sign-in before moving to the next account. Add the user to the Documents redirection group after confirming its home-folder ACL. Include administrator accounts only if their home folders exist and the same checks pass. Make the home folder, File box, and per-account RDS path part of onboarding for future RDS users; this path method is not automatic for new accounts.

Profile files and Documents need NAS backups; Mnemerynne's application backup does not cover the share. Roaming profiles copy settings at sign-in and sign-out, while redirected Documents is read and written directly at the UNC path.

## References

- [Microsoft: Configure Folder Redirection using Group Policy](https://learn.microsoft.com/en-us/windows-server/storage/folder-redirection/folder-redirection-using-group-policy)
- [Microsoft: Apply Group Policy objects to Remote Desktop Services](https://learn.microsoft.com/en-us/troubleshoot/windows-server/group-policy/apply-group-policy-objects-rds)
- [Microsoft: Deploy roaming user profiles](https://learn.microsoft.com/en-us/windows-server/storage/folder-redirection/deploy-roaming-user-profiles)
- [Microsoft: Set path for RDS Roaming User Profile](https://learn.microsoft.com/en-us/windows/client-management/mdm/policy-csp-admx-terminalserver)
- [Microsoft: Changes to Remote Connection Manager starting with Windows Server 2016](https://learn.microsoft.com/en-us/troubleshoot/windows-server/remote/remote-connection-manager-changes)
