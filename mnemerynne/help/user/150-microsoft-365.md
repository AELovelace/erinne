# Microsoft 365

If your organisation has set it up, the **Microsoft 365** section on [Preferences](preferences.md) lets you link your work account. You keep signing in to Erynne with your Erynne account; the link gives her permission to act *as you* in Microsoft 365, so Microsoft's own permissions decide what she can open.

## Linking

Press **Link** for a tenant (an organisation working with two brands may show two). A normal Microsoft sign-in follows, with your usual MFA. Afterwards the account is listed with its address; **Unlink** removes it. Depending on how the admin set the tenant up, you may only be able to link the account that matches your domain account.

If Microsoft stops accepting the link (you changed your password, the account was disabled), it shows *needs linking again*: link it once more.

## What linking gives you

- **SharePoint in answers.** Libraries an admin added from SharePoint appear in Files and in answers, and each file is checked with your own account before it is shown or cited. Citations open the page in Erynne's viewer with *Open in SharePoint*.
- **Mail and calendar**, for people in Erynne's agent group: ask about your mail and calendar, search SharePoint live, and have replies drafted. **Save to Drafts** puts a draft in Outlook; nothing is ever sent, moved or deleted ([Tools and proposals](tools-and-proposals.md#mail-and-calendar)).
- **Groups.** Your Entra (Microsoft) groups can grant libraries, tools and systems, just like domain groups. Memberships are refreshed every few minutes and only count while the link works.

## Privacy

Mail and calendar text goes to Claude through the connector only if an admin put you in the mail group for Claude ([Using Erynne from Claude](claude.md#mail-and-calendar)); otherwise never. Tool logs keep message ids, not mail text. The other exception is **Use External API** in chat, where you send Erynne's full reach, mail included, to Anthropic under your own key. SharePoint files only reach Claude when an admin switched the library on for Claude, with the same per-file check.
