"""Phase 1 — raw vector extraction. No interpretation; just pull every line
segment (and rectangle edges) with width + color out of the PDF."""
import fitz  # pymupdf


def _xy(p):
    """fitz returns Points (with .x/.y) in newer builds, tuples in some."""
    return (p.x, p.y) if hasattr(p, "x") else (p[0], p[1])


def _seg(i, x1, y1, x2, y2, width, color):
    return {"id": f"L{i}", "x1": x1, "y1": y1, "x2": x2, "y2": y2,
            "width": width, "color": color}


def extract_lines(page) -> list[dict]:
    out, i = [], 0
    for d in page.get_drawings():
        width = float(d.get("width") or 0.0)
        col = d.get("color") or (0.0, 0.0, 0.0)
        color = tuple(round(float(c), 3) for c in col)
        for item in d["items"]:
            kind = item[0]
            if kind == "l":
                (x1, y1), (x2, y2) = _xy(item[1]), _xy(item[2])
                out.append(_seg(i, x1, y1, x2, y2, width, color)); i += 1
            elif kind == "re":
                r = item[1]
                xs = [r.x0, r.x1, r.x1, r.x0]
                ys = [r.y0, r.y0, r.y1, r.y1]
                for k in range(4):
                    out.append(_seg(i, xs[k], ys[k], xs[(k + 1) % 4],
                                    ys[(k + 1) % 4], width, color)); i += 1
            # curves ('c','qu') ignored — text outlines, fixture arcs, door
            # swings. An arc-based door detector was tried against these and
            # dropped (see room_builder.close_wall_gaps); nothing consumes
            # curve data now, so it's not extracted at all.
    return out


def extract_page(pdf_path: str, page_num: int = 0) -> dict:
    doc = fitz.open(pdf_path)
    page = doc[page_num]
    lines = extract_lines(page)
    rect = page.rect
    doc.close()
    return {"lines": lines,
            "page_rect": (rect.x0, rect.y0, rect.x1, rect.y1),
            "page_num": page_num}
