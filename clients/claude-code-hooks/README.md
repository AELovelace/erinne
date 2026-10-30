# Claude Code hook: directions from the Scratchpad

Lets the directions you write on Mnemerynne's **Scratchpad** page reach your Claude Code sessions, even ones
that are waiting for you or don't use the Mnemerynne connector. One Python script, standard library only.
See the help page *The agents' scratchpad* for what directions are.

| Event | What the hook does |
|---|---|
| Session starts | Tells Claude its label and board, and any directions still open for it |
| You send a prompt | Adds new directions to it |
| Claude uses a tool | Adds new directions (at most once a minute) |
| Claude is about to stop | Keeps it going once if a direction for it is still open |

If Mnemerynne can't be reached, or the token is wrong, the hook says nothing and the session carries on.

## Install

1. In Mnemerynne, **Preferences → Claude → Create token** (it only needs to read: leave "may edit" off). Save it
   in a file only you can read:
   ```sh
   mkdir -p ~/.config/mnemerynne && umask 077
   printf '%s' 'mnemerynne_…' > ~/.config/mnemerynne/claude-code-token
   ```
2. Copy the script: `mkdir -p ~/.claude/hooks && cp mnemerynne_directions.py ~/.claude/hooks/`
3. Merge `settings.example.json` into `~/.claude/settings.json` (all sessions) or a project's
   `.claude/settings.json`, with your Mnemerynne address in `MNEMERYNNE_URL`. On Windows, use `python` instead of
   `python3` and a full path to the script.
4. Start a session. It shows up under **Sessions** on the Scratchpad page as `cc-<project>-<id>` on the board named
   after the project folder.

## Settings

| Variable | Default | Meaning |
|---|---|---|
| `MNEMERYNNE_URL` | (required) | Mnemerynne's address |
| `MNEMERYNNE_TOKEN` or `MNEMERYNNE_TOKEN_FILE` | (required) | A Claude token, or a file holding one |
| `MNEMERYNNE_AGENT` | `cc-<project>-<first 4 of the session id>` | This session's label; directions "for" it use this |
| `MNEMERYNNE_BOARD` | the project folder's name | The board this session works on; directions for "everyone on the board" reach it |
| `MNEMERYNNE_HOOK_INTERVAL` | 60 | Seconds between checks while Claude works |

Set `MNEMERYNNE_BOARD` per project (its `.claude/settings.json` `env`) when several projects share one board, or
`MNEMERYNNE_AGENT` before starting a session to give it a name you'll recognise.

## Trying it

```sh
echo '{"hook_event_name":"SessionStart","session_id":"abcd1234","cwd":"'"$PWD"'"}' \
  | MNEMERYNNE_URL=https://beepsquad.wtf MNEMERYNNE_TOKEN=mnemerynne_… python3 mnemerynne_directions.py
```
