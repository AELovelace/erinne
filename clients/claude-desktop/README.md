# Mnemerynne extension for Claude Desktop

A small relay that lets Claude Desktop use Mnemerynne's MCP endpoint (`/mcp`).
Claude Desktop runs `server/index.js` with its built-in Node; the relay
forwards each MCP message to Mnemerynne over HTTPS with the user's token. It has
no dependencies.

## Pack it (admin, once per release)

1. In `manifest.json`, set `user_config.mnemerynne_url.default` to the address
   staff use (the same as `MNEMERYNNE_PUBLIC_URL`), and bump `version`.
2. If Mnemerynne's certificate comes from an internal CA, save that CA
   certificate (PEM) here as `ca.pem`. The relay trusts it in addition to the
   system's CAs, so staff don't have to install anything.
3. Pack it:

   ```powershell
   cd C:\Scripts\erinne\clients\claude-desktop
   npx -y @anthropic-ai/mcpb validate manifest.json
   npx -y @anthropic-ai/mcpb pack . mnemerynne.mcpb
   ```

4. Copy `mnemerynne.mcpb` into Mnemerynne's data directory (`/var/lib/mnemerynne/` on the
   Fedora box). The **Claude** page in Mnemerynne then offers it for download.

`npx @anthropic-ai/mcpb sign mnemerynne.mcpb` signs it, if you have a code-signing
certificate; unsigned bundles still install, with a warning.

## Staff setup

1. In Mnemerynne, open **Claude** → **Create token**, copy the token.
2. Download the extension from the same page and double-click it.
3. Paste the token when Claude Desktop asks.

## Trying it without Claude Desktop

```sh
echo '{"jsonrpc":"2.0","id":1,"method":"tools/list"}' \
  | MNEMERYNNE_URL=https://erynne.example.internal MNEMERYNNE_TOKEN=mnemerynne_... node server/index.js
```

Claude Code can skip the relay and talk to the endpoint directly:

```sh
claude mcp add --transport http mnemerynne https://erynne.example.internal/mcp \
  --header "Authorization: Bearer mnemerynne_..."
```
