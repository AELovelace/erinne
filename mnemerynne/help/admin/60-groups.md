# Groups

Three kinds of group give people rights, and most group boxes on the Access page accept any of them.

## Mnemerynne groups (Access → Groups)

Made here, members set here (on the group, or on each account). Grant a library or folder to a group and everyone in it gets that access. A Mnemerynne group can also **include an Entra group**: then the Microsoft 365 group's members (through their linked accounts) get the group's grants too, refreshed every few minutes.

Use these for library access when the people don't map onto a domain group, or when the library isn't on the share.

## Domain (AD) groups

Read from the AD snapshot through each person's linked domain account ([Accounts](accounts.md#group-memberships-the-ad-snapshot)), nested groups included. They are referenced by name wherever a group box appears:

- **Administrators** (Access → Settings)
- **Erynne agent** (who gets tools)
- **Claude in chat** (who may use their own API key)
- each **internal API** (who may query it)
- each **shared mailbox** (who may ask about it)

Domain groups also decide access to **share libraries**, but through Windows permissions, not by name here.

## Entra (Microsoft 365) groups

Available once a tenant is added under Microsoft 365. Written as `Tenant / Group` (e.g. `Gasco / Front Desk`); the boxes suggest them. A person's Entra groups count only through a Microsoft account they linked, and memberships older than 30 minutes stop counting, so a tenant that stops answering removes rights rather than keeping stale ones.

## Several groups in one box

Every group box takes one or more groups separated by `;`, mixing kinds: `IT Staff; Gasco / Front Desk`.

## Where to look when access is wrong

- **Access → Who can see what** shows, per person and library, what they can do and through which grant, group or share permission.
- A person's **Preferences** page lists the groups Mnemerynne knows for them. If a domain group is missing there, the snapshot hasn't caught up (5 minutes), the account isn't linked, or the group is nested in a way AD reports differently.
- **Recent changes** records every grant and group change.
