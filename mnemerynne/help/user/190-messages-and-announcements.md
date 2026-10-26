# Messages and announcements

The **Messages** tab is where Erynne tells you things and where colleagues leave you notes. A badge on the tab shows how many are unread; it updates by itself while you use Erynne.

## Notes between people

Leave a short note for a colleague by their username: pick the name, write the note (plain text, up to 2,000 characters), optionally add a link to a page in Erynne (a file, a wiki page, a proposal) and press **Send**. They see it on their Messages tab with your name; a note that Claude or Erynne sent on your behalf says so ("Claude for alice").

Notes aren't email: they stay inside Erynne, carry no attachments, and only go to people who can sign in here. Clicking a note marks it read; **Mark all read** clears the badge. Notes are kept for a year.

## What Erynne tells you

Some messages come from Erynne herself, marked **Mnemerynne**:

- an admin **approved or turned down** a request of yours (letting the AI edit a library, an API action that needed approval), with their note and a link to the card;
- someone **commented** on a file you uploaded, or on a file you commented on earlier;
- an admin **changed your account**: your role, your groups, your domain link, or let you sign in again.

Each has a link to the place it is about.

## Announcements

An announcement is something an admin wants everyone, or a group, to see. It appears as a card at the top of every page until you press **Got it**. Some must be acknowledged: the card stays until you press **I acknowledge**, and admins can see who has. The Messages tab lists every announcement meant for you, including ones you already dismissed, with a link where there is one.

## Through Claude and Erynne

If you use Claude with Erynne, it can read your unread messages and announcements (`list_messages`) and leave a note for a colleague (`send_message`), with a connection that may edit. Erynne in chat can do the same: "leave rob a note that the printer's fixed", or "anything waiting for me?". She is also told at the start of each question whether you have unread messages or announcements.
