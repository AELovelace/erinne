"""Build small, valid PDFs for tests: text pages, blank (scanned-like) pages, bookmarks."""

from __future__ import annotations


def _esc(text: str) -> str:
    return text.replace("\\", "\\\\").replace("(", "\\(").replace(")", "\\)")


def make_pdf(pages: list[list[str] | None], outline: list[tuple[str, int, int]] = ()) -> bytes:
    """pages: lines of text per page (None = a page with no text layer).
    outline: (title, page_index, level) in document order; level 0 = top."""
    objs: dict[int, bytes] = {}
    next_num = 4
    page_nums = []
    for lines in pages:
        page_num, content_num = next_num, next_num + 1
        next_num += 2
        page_nums.append(page_num)
        ops, y = [], 740
        for line in lines or []:
            ops.append(f"BT /F1 11 Tf 72 {y} Td ({_esc(line)}) Tj ET")
            y -= 14
        stream = "\n".join(ops).encode("latin-1")
        objs[content_num] = b"<< /Length %d >>\nstream\n" % len(stream) + stream + b"\nendstream"
        objs[page_num] = (
            f"<< /Type /Page /Parent 2 0 R /MediaBox [0 0 612 792] "
            f"/Resources << /Font << /F1 3 0 R >> >> /Contents {content_num} 0 R >>"
        ).encode()
    objs[2] = f"<< /Type /Pages /Kids [{' '.join(f'{n} 0 R' for n in page_nums)}] /Count {len(pages)} >>".encode()
    objs[3] = b"<< /Type /Font /Subtype /Type1 /BaseFont /Helvetica >>"

    catalog = "<< /Type /Catalog /Pages 2 0 R"
    if outline:
        root = {"num": next_num, "children": [], "level": -1}
        next_num += 1
        stack, items = [root], []
        for title, page, level in outline:
            node = {"num": next_num, "title": title, "page": page, "level": level, "children": []}
            next_num += 1
            while stack[-1]["level"] >= level:
                stack.pop()
            node["parent"] = stack[-1]
            stack[-1]["children"].append(node)
            stack.append(node)
            items.append(node)

        def count(node):
            return len(node["children"]) + sum(count(c) for c in node["children"])

        objs[root["num"]] = (
            f"<< /Type /Outlines /First {root['children'][0]['num']} 0 R "
            f"/Last {root['children'][-1]['num']} 0 R /Count {count(root)} >>"
        ).encode()
        for node in items:
            siblings = node["parent"]["children"]
            i = siblings.index(node)
            parts = [f"/Title ({_esc(node['title'])})", f"/Parent {node['parent']['num']} 0 R",
                     f"/Dest [{page_nums[node['page']]} 0 R /XYZ 0 792 0]"]
            if i > 0:
                parts.append(f"/Prev {siblings[i - 1]['num']} 0 R")
            if i < len(siblings) - 1:
                parts.append(f"/Next {siblings[i + 1]['num']} 0 R")
            if node["children"]:
                parts += [f"/First {node['children'][0]['num']} 0 R",
                          f"/Last {node['children'][-1]['num']} 0 R", f"/Count {count(node)}"]
            objs[node["num"]] = ("<< " + " ".join(parts) + " >>").encode()
        catalog += f" /Outlines {root['num']} 0 R"
    objs[1] = (catalog + " >>").encode()

    out = bytearray(b"%PDF-1.4\n")
    offsets = {}
    for num in sorted(objs):
        offsets[num] = len(out)
        out += f"{num} 0 obj\n".encode() + objs[num] + b"\nendobj\n"
    xref = len(out)
    size = max(objs) + 1
    out += f"xref\n0 {size}\n".encode() + b"0000000000 65535 f \n"
    for num in range(1, size):
        out += f"{offsets[num]:010d} 00000 n \n".encode()
    out += f"trailer\n<< /Size {size} /Root 1 0 R >>\nstartxref\n{xref}\n%%EOF\n".encode()
    return bytes(out)
