# Approvals

Most things Claude or Erynne propose are decided by the person who asked: they press Apply, Run it or Dismiss on the card. Two kinds can't be theirs to decide and land on **Access → Approvals**:

| Request | Why it waits for an admin |
|---|---|
| **Let the AI edit a whole library** (Claude asked with `request_edit_access` for a library and the person isn't an admin) | Allowing AI edits library-wide is the *Claude and Erynne can edit files here* switch, an admin setting. |
| **An API action marked "needs an admin's approval"** | The admin decided on the operation that someone should check each run (closing tickets, resolving alerts for everyone). |

## Deciding

Waiting requests are listed first, oldest first, each with who asked, what exactly would happen, and a note box:

- **Approve** runs it on the requester's behalf. For an API action the requester's access is checked again at that moment (operation still enabled, still in its group), so an admin can't run something for someone who has lost access. For a library edit request it flips the library's switch and logs it.
- **Reject** turns it down. The note is shown to the requester and to their Claude (`get_proposal` reports *rejected* with the note).

The person can **withdraw** a request while it waits (their Claude can too, with `withdraw_proposal`). Decided requests stay listed for a week; every decision goes in Recent changes, as does every proposal a person applies or dismisses (`proposal.apply`, `proposal.dismiss`, with the note). A batch (several API actions under one approval) is approved or rejected as a whole here.

## What Claude is told

When Claude proposes something that needs an admin, it gets status `awaiting_admin` and no approve link, and its instructions say to tell the person it's with the admins and to check `get_proposal` for the outcome rather than assume. Pending proposals of every kind expire after `MNEMERYNNE_PROPOSAL_DAYS` (7) if nobody decides them; they then show as *expired*, and Claude is told so.

## Related

- Marking an action as needing approval: on the operation, on [Access → APIs](internal-apis.md#actions-operations-that-change-data).
- The library-wide switch itself: [Libraries, grants and inheritance](libraries-and-grants.md#claude-switches-on-a-library).
- What proposals look like to people: the user help on tools and proposals.
