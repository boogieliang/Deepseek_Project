"""Dump DOCX structure: paragraphs (style, alignment, runs) and tables."""
import sys
from docx import Document
from docx.shared import Pt
from docx.oxml.ns import qn


def fmt_len(v):
    if v is None:
        return "None"
    try:
        return f"{v.pt:.1f}pt"
    except Exception:
        return str(v)


def main(path):
    doc = Document(path)
    print("=" * 80)
    print("FILE:", path)
    print("=" * 80)
    sec = doc.sections[0]
    print(f"[SECTION] page {fmt_len(sec.page_width)} x {fmt_len(sec.page_height)}")
    print(f"  margins L={fmt_len(sec.left_margin)} R={fmt_len(sec.right_margin)} "
          f"T={fmt_len(sec.top_margin)} B={fmt_len(sec.bottom_margin)}")
    print(f"  header_dist={fmt_len(sec.header_distance)} footer_dist={fmt_len(sec.footer_distance)}")

    normal = doc.styles["Normal"]
    print(f"[STYLE Normal] font={normal.font.name} size={fmt_len(normal.font.size)}")
    try:
        rpr = normal.element.get_or_add_rPr()
        rf = rpr.find(qn('w:rFonts'))
        if rf is not None:
            print("  rFonts:", {k.split('}')[-1]: v for k, v in rf.attrib.items()})
    except Exception as e:
        print("  rFonts err", e)

    print("\n--- BODY ---")
    body = doc.element.body
    p_idx = 0
    t_idx = 0
    for child in body.iterchildren():
        tag = child.tag.split('}')[-1]
        if tag == 'p':
            para = None
            # find paragraph object by index mapping
            para = doc.paragraphs[p_idx] if p_idx < len(doc.paragraphs) else None
            p_idx += 1
            if para is None:
                continue

            pf = para.paragraph_format
            info = [
                f"P{p_idx-1:03d}",
                f"style={para.style.name!r}",
                f"align={para.alignment}",
            ]
            if pf.first_line_indent is not None:
                info.append(f"firstLine={fmt_len(pf.first_line_indent)}")
            if pf.left_indent is not None:
                info.append(f"left={fmt_len(pf.left_indent)}")
            if pf.space_before is not None:
                info.append(f"sb={fmt_len(pf.space_before)}")
            if pf.space_after is not None:
                info.append(f"sa={fmt_len(pf.space_after)}")
            if pf.line_spacing is not None:
                info.append(f"ls={pf.line_spacing}")
            print(" | ".join(info))
            print(f"   TEXT: {para.text!r}")
            for i, run in enumerate(para.runs):
                f = run.font
                rinfo = [f"r{i}"]
                if f.name:
                    rinfo.append(f"font={f.name}")
                if f.size:
                    rinfo.append(f"size={fmt_len(f.size)}")
                if f.bold:
                    rinfo.append("bold")
                if f.italic:
                    rinfo.append("italic")
                if f.underline:
                    rinfo.append("underline")
                col = f.color
                if col is not None and col.rgb is not None:
                    rinfo.append(f"color={col.rgb}")
                if run.text:
                    rinfo.append(f"text={run.text!r}")
                print("   " + " ".join(rinfo))
        elif tag == 'tbl':
            tbl = doc.tables[t_idx] if t_idx < len(doc.tables) else None
            t_idx += 1
            if tbl is None:
                continue
            print(f"\n[TABLE {t_idx-1}] rows={len(tbl.rows)} cols={len(tbl.columns)} style={tbl.style.name if tbl.style else None}")
            for ri, row in enumerate(tbl.rows):
                cells = [c.text.replace("\n", " / ") for c in row.cells]
                print(f"   row{ri}: {cells}")
            print()
    print("\n--- SECTIONS HEADERS/FOOTERS ---")
    for i, s in enumerate(doc.sections):
        print(f"section {i}: header='{s.header.paragraphs[0].text if s.header.paragraphs else ''}' "
              f"footer='{s.footer.paragraphs[0].text if s.footer.paragraphs else ''}'")
    print(f"\n[STATS] paragraphs={len(doc.paragraphs)} tables={len(doc.tables)}")


if __name__ == "__main__":
    main(sys.argv[1])
