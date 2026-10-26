# Balancer nodes

The answer model can run on one or more **mnemerynne-balancer** boxes (each spreading requests over its own GPUs) instead of, or as well as, the local generator. The embedder always stays on this box, next to the files it indexes.

## Setting it up

1. **On each node**, download and start Erynne's model on its control page (`http://<node>:9180`): LFM2.5 2.6B Q8 for the stock model; a fine-tuned GGUF must be on Hugging Face for the node to download it. The node's LFM2.5 entries start with the no-think chat template and `--reasoning-format deepseek`, so memories and follow-ups work from a node; a fine-tuned LFM2.5 added from Hugging Face needs `"chat_template": "lfm2-no-think.jinja"` in its entry in the node's `data/custom-models.json`. Note the node's API key (`BALANCER_API_KEY`); every node needs the same key.
2. **Access → Settings → Balancer nodes**: the addresses separated by commas (`http://10.0.5.20:9180, http://10.0.5.21:9180`) and the key, then Save. Each node is checked with the key first; if one doesn't answer or rejects the key, nothing is saved and the message says which. The key is stored encrypted (needs `MNEMERYNNE_SECRET_KEY`) and never shown again.

## How it behaves

- **Least busy.** Every node is asked every 2 seconds how busy it is (requests in flight and parallel slots). Each answer goes to the ready node with the fewest requests per slot; requests are counted the moment they're sent, so a burst spreads out. Ties go to the node listed first.
- **Failover.** A node that refuses the connection, times out, rejects the key or answers 5xx sits out for 30 seconds and the request goes to the next. Once a node has started answering, the answer comes from it. If no node answers, or the list is empty, answers come from `MNEMERYNNE_LLM_URL`, so keep the local generator running as the fallback.
- **Status.** The section shows each node: answering or not, the model it runs, requests in flight per slot, answers sent there in the last hour, and when it was last checked. It refreshes while the tab is open.
- **What it covers.** Chat answers, routing, memories, apologies, and the Erynne agent. Not embeddings.
- **Nodes don't know about each other**; spreading between boxes happens here.

## Deploying a new balancer template

A change to the no-think template in the balancer repository has to be deployed to each node for it to take effect; the nodes don't pull it from Mnemerynne.
