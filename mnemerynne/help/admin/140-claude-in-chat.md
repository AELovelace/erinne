# Claude in chat (people's own API keys)

Members of one group may tick **🌐 Use External API** under the question box on the Ask tab. Their question is then answered by Claude (Anthropic's model) with Erynne's tools, using the person's **own Anthropic API key**, so the cost is billed to them, not to the organisation.

## Setting it up

1. `MNEMERYNNE_SECRET_KEY` must be set: keys are stored encrypted with it.
2. **Access → Settings → Claude in chat**: pick the group (AD or Entra, `;`-separated) and save. The section lists the members and whether each has saved a key. Leave the group empty to turn the feature off; people who already saved a key can still remove it.
3. Each member pastes their key on **Preferences → Claude in chat** and picks a model (Claude Opus 5.5 by default, or Sonnet or Haiku). The key is checked with Anthropic when saved and never shown again.

## What Claude reaches there

The same as through the connector: only what is shared with Claude, with the per-file switches, and never mail, calendar or memories. Reading-heavy parts are handed to up to two local MinErynnes with the same limits. Changes Claude drafts are proposals the person applies, as always. If the key is rejected or rate-limited, the person gets a plain apology that doesn't name the provider and can fall back to Erynne.

## What is sent

The question, the conversation so far, the passages and tool results Claude reads, and the tool definitions go to Anthropic under the person's key. The log records the tool calls like any agent run.
