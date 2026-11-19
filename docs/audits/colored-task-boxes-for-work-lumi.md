# Colored task boxes: a guide for work Lumi

**From:** home Lumi (Mnemerynne codebase) · **For:** work Lumi (Eternal Hotels MCP) · **Owner:** Edith · **Date:** 2026-11-19
**Shipped in:** commit 8454016 on `main`

Mnemerynne can draw a security-audit tasklist's tasks as clickable emoji boxes. The color is the task's
criticality. People tick the boxes in the file view, the wiki, or the editor preview, and each tick is saved
straight away as a new version of the file.

## 1. Turn it on for a file

The file must start with this front matter, on the very first lines:

```
---
checkboxes: emoji
---
```

- It applies **only to that file**. Files without it keep plain checkboxes, and in those files a colored
  square is just text.
- The setting is read only from the front matter. The same words in the body do nothing.
- The front matter is hidden when the page is shown.

## 2. Write tasks

A task is a **list item** whose text **starts with a marker followed by a space**:

| Marker | Meaning | Shows as |
|---|---|---|
| `🟥` | open, **critical** | 🟥 |
| `🟧` | open, **high** | 🟧 |
| `🟨` | open, **medium** | 🟨 |
| `[ ]` | open, no criticality | ⬜ |
| `✅` (or `[x]`) | done | ✅ |

```
- 🟥 **T-21** Security feeds into the morning brief
- 🟧 **T-11** Update Git for Windows on the 13 PCs
- 🟨 **T-14** Tidy the Wazuh noise rules
- ✅ **T-05** Teramind on the PCI server *(done 2026-11-18, edith, was 🟥)*
```

Rules:
- The marker must come **right after the bullet** (`- `, `* `, `+ ` or `1. `), followed by a space.
  `- **T-21** 🟥 ...` is **not** a task.
- Only list items count. A marker in the middle of a sentence or a table cell stays text.
- A task can wrap onto more lines. The done stamp goes at the end of its **first paragraph**.
- Other emoji (🟩, 🔴, 🟠, 🟡, ⬛ ...) are **not** markers.

## 3. What a tick writes (so you can read and write it)

When someone ticks a colored task, Mnemerynne rewrites the source lines like this:

```
before:  - 🟧 **T-11** Update Git for Windows on the 13 PCs
after:   - ✅ **T-11** Update Git for Windows on the 13 PCs *(done 2026-11-19, edith, was 🟧)*
```

- The marker becomes `✅`.
- `*(done YYYY-MM-DD, who, was 🟧)*` is added to the end of the first paragraph. The `was` part keeps the
  criticality, and it is only there if the task had a color. A `[ ]` task gets `*(done YYYY-MM-DD, who)*`.
- **Unticking** reverses it: the color from `was` goes back (`- 🟧 ...`) and the stamp is removed. If there is
  no `was`, the task goes back to `[ ]`.

**If you edit the file yourself** (for example with `edit_text`), use exactly this stamp format when you mark
something done. Then a person can untick it in Mnemerynne and get the right color back. Don't change or remove a
`was 🟥` part on a done task: it is the only record of that task's criticality.

## 4. Reading the list in the morning brief

- **Open:** lines starting `- 🟥`, `- 🟧`, `- 🟨`, `- [ ]`. Criticality = the marker (critical / high / medium /
  none). Sort critical first.
- **Done:** lines starting `- ✅` or `- [x]`. Who and when are in the stamp. The original criticality is in
  `was 🟥|🟧|🟨` (missing = none).
- **Done today:** look for the stamp `*(done <today>, ...)*`.
- The file is versioned, so every tick keeps the previous version under Versions if you need history.

## 5. Things to know

- Edith's hardening tasklist (*IT-OPS / Hardening Reports / 000 - Hardening tasklist.md*) needs the front matter
  from section 1 added before the colors work there. If its "How to update" line still says to replace `[ ]` by
  hand, update it: people can now click the box.
- When ticking, Mnemerynne checks that the file hasn't changed since the page was opened. If you edit the file
  while someone has it open, their next click is refused and the page reloads. Nothing is lost.
- Only people who can edit the file can click the boxes. Everyone else sees the state.
