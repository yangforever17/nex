"""Generate the original, editable README diagram (no external assets).

Run this script, then export docs/assets/overview.drawio using diagrams.net.
The XML is the source of truth; PNG and SVG are presentation exports.
"""

from pathlib import Path
import xml.etree.ElementTree as ET


ROOT = Path(__file__).resolve().parents[1]
NAVY, TEAL, RED, AMBER = "#123F70", "#367C70", "#B64342", "#A5762E"
INK, MUTED = "#263445", "#59677A"


def build():
    document = ET.Element("mxfile", host="app.diagrams.net", version="26.0.9")
    page = ET.SubElement(document, "diagram", id="nex-overview", name="NEX overview")
    model = ET.SubElement(page, "mxGraphModel", page="1", pageWidth="1200", pageHeight="484",
                          grid="1", gridSize="8", background="#FFFFFF")
    cells = ET.SubElement(model, "root")
    ET.SubElement(cells, "mxCell", id="0")
    ET.SubElement(cells, "mxCell", id="1", parent="0")

    def node(key, label, x, y, w, h, *, fill="#FFFFFF", stroke=NAVY, size=18,
             color=INK, bold=False, text=False, extra=""):
        style = (f"rounded=1;arcSize=12;whiteSpace=wrap;html=0;fontFamily=Arial;fontSize={size};"
                 f"fontColor={color};fontStyle={1 if bold else 0};strokeWidth=1.6;spacing=6;"
                 f"fillColor={fill};strokeColor={stroke};{extra}")
        if text:
            style += "text;fillColor=none;strokeColor=none;align=left;spacing=0;"
        cell = ET.SubElement(cells, "mxCell", id=key, value=label, style=style, vertex="1", parent="1")
        ET.SubElement(cell, "mxGeometry", x=str(x), y=str(y), width=str(w), height=str(h), **{"as": "geometry"})

    def edge(key, source, target, *, color=NAVY, ports="", points=()):
        cell = ET.SubElement(cells, "mxCell", id=key, value="",
            style=(f"edgeStyle=orthogonalEdgeStyle;rounded=0;html=0;endArrow=block;endSize=7;"
                   f"strokeWidth=1.8;strokeColor={color};{ports}"), edge="1", parent="1",
            source=source, target=target)
        geometry = ET.SubElement(cell, "mxGeometry", relative="1", **{"as": "geometry"})
        if points:
            array = ET.SubElement(geometry, "Array", **{"as": "points"})
            for x, y in points:
                ET.SubElement(array, "mxPoint", x=str(x), y=str(y))

    node("brand", "NEX", 28, 16, 150, 66, size=44, color=NAVY, bold=True, text=True)
    node("tagline", "Keep the good work.", 204, 24, 780, 52, size=38, color=NAVY, bold=True, text=True)
    node("subtitle", "The model predicts. The runtime decides what survives.", 28, 88, 1100, 32,
         size=21, color=MUTED, text=True)
    node("author_title", "01  MODEL-AUTHORED", 28, 140, 260, 32, size=18, color=NAVY, bold=True, text=True)
    node("runtime_title", "02  EVIDENCE-GATED", 324, 140, 540, 32, size=18, color=TEAL, bold=True, text=True)
    node("sink_title", "03  PUBLICATION", 920, 140, 250, 32, size=18, color=NAVY, bold=True, text=True)
    node("program", '<div style="font-family:monospace;font-size:15px;text-align:left;white-space:pre;">'
         'p = semantic(samples)<br>for site in sites:<br>&nbsp;&nbsp;apply_change(site, p)</div>',
         28, 196, 248, 144, fill="#EDF2F8", size=15, color=NAVY,
         extra="html=1;fontFamily=monospace;align=left;spacingLeft=14;")
    node("guess", "One rule, reused.", 28, 368, 264, 36, size=17, color=MUTED, text=True)
    node("valid_label", "CERTIFIED", 324, 182, 266, 26, size=15, color=TEAL, bold=True, text=True)
    node("pending_label", "STILL UNRESOLVED", 600, 182, 260, 26, size=15, color=AMBER, bold=True, text=True)
    for i in range(1, 12):
        fill = TEAL if i <= 6 else RED if i == 7 else "#F4EAD8"
        border = TEAL if i <= 6 else RED if i == 7 else AMBER
        node(f"site_{i}", f"{i}!" if i == 7 else str(i), 324 + (i - 1) * 46, 220, 36, 44,
             fill=fill, stroke=border, size=18, color="#FFFFFF" if i <= 7 else AMBER, bold=True)
    node("keep", "KEEP  1–6", 324, 296, 266, 48, fill="#DFEEE9", stroke=TEAL, color=TEAL, size=22, bold=True)
    node("replay", "REPLAY  7–11", 600, 296, 220, 48, fill="#F8E7E4", stroke=RED, color=RED, size=22, bold=True)
    node("preserved", "Certificates stay valid.", 324, 370, 266, 34, size=17, color=TEAL, text=True)
    node("remaining", "Continue sites 12–16", 600, 368, 220, 40, fill="#EDF2F8", size=16, color=NAVY)
    node("gate", "FINAL VALIDATION\nmust pass", 920, 204, 252, 68, size=18, color=NAVY, bold=True)
    node("accept_label", "accept", 1066, 282, 94, 28, size=16, color=TEAL, text=True)
    node("sink", "PUBLISH ONCE\nlocal SQLite sink", 920, 326, 252, 78,
         fill=NAVY, color="#FFFFFF", size=20, bold=True,
         extra="shape=cylinder3;boundedLbl=1;size=12;")
    edge("predict", "program", "site_1", ports="exitX=1;exitY=0.319;entryX=0;entryY=0.5;")
    edge("reject", "site_7", "replay", color=RED, ports="exitX=0.5;exitY=1;entryX=0.082;entryY=0;")
    edge("resume", "replay", "remaining", color=RED, ports="exitX=0.5;exitY=1;entryX=0.5;entryY=0;")
    edge("finish", "remaining", "gate", ports="exitX=1;exitY=0.5;entryX=0;entryY=0.5;",
         points=((872, 388), (872, 238)))
    edge("commit", "gate", "sink", color=TEAL, ports="exitX=0.5;exitY=1;entryX=0.5;entryY=0;")
    node("legend", "A delayed reject at site 7 does not erase the evidence for sites 1–6.",
         28, 430, 1136, 30, size=19, color=NAVY, text=True)
    destination = ROOT / "docs/assets/overview.drawio"
    destination.parent.mkdir(parents=True, exist_ok=True)
    ET.indent(document)
    ET.ElementTree(document).write(destination, encoding="utf-8", xml_declaration=True)
    print(destination.relative_to(ROOT))


if __name__ == "__main__":
    build()
