# Announcements and messages

## Announcements

**Access → Announcements** posts a notice that people see as a card at the top of every page until they dismiss it.

- **Title** and **text** (Markdown; rendered like the file view, raw HTML escaped). An optional **link** to a page in Mnemerynne (`/wiki/40`, `/help/user/files`, `/files/12/view`).
- **For**: everyone, or an AD or Entra group (several with `;`), through people's linked accounts like every other group box.
- **Until**: an expiry date; the card stops showing at once when it passes, and the announcement is deleted `MNEMERYNNE_ANNOUNCEMENT_DAYS` (90) later. Without a date it stays until removed.
- **People must acknowledge it**: the card has no *Got it*, only *I acknowledge*, and stays until pressed. The list shows how many saw and acknowledged each announcement.

The Messages tab lists every announcement meant for a person, dismissed or not. Posting, changing and removing are in Recent changes (`announcement.*`).

## Messages

People leave each other short notes on the Messages tab (by username; every signed-in person can see the list of usernames), and Mnemerynne sends **system messages** on its own:

| When | To whom |
|---|---|
| An admin approves or rejects a request on Access → Approvals | the requester, with the note and a link to the card |
| Someone comments on a file | the file's uploader and earlier commenters, not the author |
| An admin changes a role, groups, a domain link, or reactivates an account | that person |

Notes are plain text up to 2,000 characters with one optional link to a page in Mnemerynne (never another site); a sender may send at most 60 an hour, which stops a looping agent. Messages are deleted after `MNEMERYNNE_MESSAGE_DAYS` (365). Nothing is purged by hand; the worker does it hourly with the other retention.

## Claude and Erynne

Two tools, for the connector and for Erynne in chat: `list_messages` (the person's unread notes, system messages and pending announcements; `mark_read` marks them read) and `send_message` (a note to a username, with a connection that may edit; the sender shows as "Claude for name" or "Erynne for name"). Erynne's system prompt also says at the start of each question how many unread messages and which announcements are waiting, so she can mention them.

## The badge

Every signed-in page loads `notify.js`, which asks `/api/notify` on load, once a minute and when the tab comes back, and shows the unread count on the Messages tab and the pending announcement cards. The help pages' strict Content-Security-Policy allows that one request (`connect-src 'self'`).
