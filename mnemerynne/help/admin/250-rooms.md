# Rooms

Rooms is the team chat: channels (open, private, or tied to an AD or Entra group) and direct messages, with threads, reactions, pins, files and Erynne answering **@Erynne**. People see it under **Rooms**; this page is the admin side.

## Settings → Rooms

- **Rooms is on for this organisation:** off until an admin turns it on, because not every organisation wants a chat app. While it's off:
  - nobody sees the Rooms tab;
  - its pages and API answer as if it didn't exist;
  - open Rooms tabs stop;
  - Claude's room tools refuse.

  Messages and files already written are kept, still subject to the keeping rules below, and come back when it's turned on again. Turning it on or off is logged (`settings.rooms`).
- **Who may make channels:** a group, several separated by `;`, or empty for everyone. Anyone can always message people directly. Admins may always make channels.
- **Channels:** every channel, private and archived ones included, with how many messages it has and when it was last used. **Archive** keeps a channel readable but closes it to new messages; **Unarchive** opens it again.
- Admins can open any channel to moderate it, and can delete any message there. Deleting someone else's message, and archiving a channel you don't own, are logged in Recent changes (`rooms.*`).

## Erynne in rooms

When someone mentions **@Erynne**, she answers in a thread with the agent's usual model (balancer nodes, then `MNEMERYNNE_LLM_URL`), two rooms at a time.
- **What she reads:** only what **everyone the room reaches** can read, so she can't pass one person's documents to the others. That means the libraries all of them can read, and in each, only the folders all of them can read.
  - File boxes are never included, and neither are SharePoint libraries.
  - In an open channel, "everyone" is every active user.
- **API operations:** only reading operations that every member may use, and none in open channels.
- **What she can't do:** she never edits, proposes or sends mail from a room.
- A channel's owner can switch her off for that channel.

## Keeping

- **Messages and their files:** kept `MNEMERYNNE_ROOM_MESSAGE_DAYS` (365; 0 keeps them).
- **Files:** each attachment is at most `MNEMERYNNE_ROOM_FILE_BYTES` (25 MB). Files live under `<data_dir>/rooms` and go with their message. Files of deleted messages, and ones uploaded but never sent, go within the hour.
- **The live-delivery log** (`chat_events`) is kept a week.

## Live delivery behind a proxy

Each open Rooms tab holds one server-sent-events stream (`/api/rooms/stream`) with a heartbeat every 20 seconds.
- **nginx** needs the `location = /api/rooms/stream` block from `deploy/linux/nginx-mnemerynne.conf` (no buffering, a one-hour read timeout).
- **Caddy** needs nothing extra.
- After a restart, open pages reconnect by themselves and catch up on what they missed.
