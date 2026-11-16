"""
Programmatic DXF test generator using ezdxf.
Creates mathematically exact civil engineering drawings with realistic CAD hygiene traps:
- Hungarian EOV (HD72) coordinates with mismatched $INSUNITS header
- LWPOLYLINE with exact circular arc bulges (rounded corners)
- Closed, open, and 'nearly closed' (4cm gap) island polygons
- Perforated HATCH entities with inner hole loops (for double-deduction immunity testing)
- Exact and partial colinear overlapping curb lines
- 'Flying Z' 3D elevation jump (Z=0.0m to Z=114.5m on a 10m horizontal line)
- Mirrored OCS entity (extrusion Z=-1)
- INSERT manhole blocks with ATTRIB tags and MTEXT formatting codes
- Paperspace layout entities (must be ignored by Modelspace QTO)
"""

from __future__ import annotations

import math
from pathlib import Path

import ezdxf
from ezdxf.enums import TextEntityAlignment

# Exact mathematical bulge for a 90-degree (pi/2) circular arc: tan((pi/2)/4) = tan(pi/8) = sqrt(2) - 1
BULGE_90_DEG = math.tan(math.pi / 8.0)


def create_eov_parking_lot_dxf(file_path: str | Path) -> Path:
    """
    Generate a comprehensive synthetic civil engineering DXF in Hungarian EOV coordinates.
    Origin: EOV Easting (CAD X) = 650,000.0 m, Northing (CAD Y) = 240,000.0 m.
    """
    out_path = Path(file_path)
    doc = ezdxf.new("R2018")
    # Deliberately set $INSUNITS = 4 (Millimeters) to test EOV override & mismatch warning
    doc.header["$INSUNITS"] = 4

    msp = doc.modelspace()
    x0 = 650_000.0
    y0 = 240_000.0

    # Define layers
    for lyr_name, color in [
        ("BURKOLAT_TERKO", 7),
        ("ZOLDSZIGET", 3),
        ("BURKOLAT_HATCH", 8),
        ("ZOLDSZIGET_HATCH", 3),
        ("SZEGELY_KIEMELT", 1),
        ("CSAPADEK_AKNA", 5),
        ("FELIRATOK", 2),
        ("EXPLODED_BOX", 6),
        ("BOWTIE_LAYER", 4),
        ("MIRRORED_OCS", 5),
        ("SCALED_INSERT_LAYER", 3),
    ]:
        doc.layers.add(lyr_name, color=color)

    # -------------------------------------------------------------------------
    # 1. Outer Paving Boundary on 'BURKOLAT_TERKO':
    #    50m x 20m rectangle with R=5m rounded corners using exact 90-deg bulges.
    #    Analytical Gross Area = 50*20 - 4*(5*5) + pi*(5^2) = 900 + 25*pi = 978.539816 m2
    #    Analytical Outer Perimeter = 2*(40) + 2*(10) + 2*pi*5 = 100 + 10*pi = 131.415927 m
    # -------------------------------------------------------------------------
    r = 5.0
    w = 50.0
    h = 20.0
    # Counter-clockwise vertices: (x, y, start_width, end_width, bulge)
    rounded_rect_pts = [
        (x0 + r, y0, 0.0, 0.0, 0.0),
        (x0 + w - r, y0, 0.0, 0.0, BULGE_90_DEG),
        (x0 + w, y0 + r, 0.0, 0.0, 0.0),
        (x0 + w, y0 + h - r, 0.0, 0.0, BULGE_90_DEG),
        (x0 + w - r, y0 + h, 0.0, 0.0, 0.0),
        (x0 + r, y0 + h, 0.0, 0.0, BULGE_90_DEG),
        (x0, y0 + h - r, 0.0, 0.0, 0.0),
        (x0, y0 + r, 0.0, 0.0, BULGE_90_DEG),
    ]
    outer_poly = msp.add_lwpolyline(
        rounded_rect_pts, format="xyseb", dxfattribs={"layer": "BURKOLAT_TERKO"}
    )
    outer_poly.closed = True

    # -------------------------------------------------------------------------
    # 2. Internal Islands on 'ZOLDSZIGET':
    #    - Island 1: Closed 10m x 4m rectangle -> Area = 40.000 m2
    #    - Island 2: Nearly-closed (open with 0.04m gap) 6m x 5m rectangle -> Area = 30.000 m2
    #    - Island 3: Small 0.6m x 0.6m chamber cover -> Area = 0.360 m2 (for VOB/C threshold test)
    # -------------------------------------------------------------------------
    isl1_pts = [
        (x0 + 8.0, y0 + 5.0),
        (x0 + 18.0, y0 + 5.0),
        (x0 + 18.0, y0 + 9.0),
        (x0 + 8.0, y0 + 9.0),
    ]
    isl1 = msp.add_lwpolyline(isl1_pts, dxfattribs={"layer": "ZOLDSZIGET"})
    isl1.closed = True

    # Nearly closed: last point at (x0 + 25.0, y0 + 5.04) leaving a 4cm vertical gap to (x0 + 25.0, y0 + 5.0)
    isl2_pts = [
        (x0 + 25.0, y0 + 5.0),
        (x0 + 31.0, y0 + 5.0),
        (x0 + 31.0, y0 + 10.0),
        (x0 + 25.0, y0 + 10.0),
        (x0 + 25.0, y0 + 5.04),
    ]
    isl2 = msp.add_lwpolyline(isl2_pts, dxfattribs={"layer": "ZOLDSZIGET"})
    isl2.closed = False

    isl3_pts = [
        (x0 + 40.0, y0 + 5.0),
        (x0 + 40.6, y0 + 5.0),
        (x0 + 40.6, y0 + 5.6),
        (x0 + 40.0, y0 + 5.6),
    ]
    isl3 = msp.add_lwpolyline(isl3_pts, dxfattribs={"layer": "ZOLDSZIGET"})
    isl3.closed = True

    # -------------------------------------------------------------------------
    # 3. Perforated HATCH on 'BURKOLAT_HATCH' + Identical Island on 'ZOLDSZIGET_HATCH'
    #    Outer loop: 20m x 10m = 200.0 m2
    #    Inner hole loop inside HATCH: 4m x 3m = 12.0 m2
    #    Duplicate island polyline on 'ZOLDSZIGET_HATCH': 4m x 3m = 12.0 m2
    #    True Net Area MUST be 188.0 m2 (NOT 176.0 m2!).
    # -------------------------------------------------------------------------
    hx0 = x0 + 100.0
    hy0 = y0
    hatch = msp.add_hatch(color=8, dxfattribs={"layer": "BURKOLAT_HATCH"})
    hatch.paths.add_polyline_path(
        [
            (hx0, hy0),
            (hx0 + 20.0, hy0),
            (hx0 + 20.0, hy0 + 10.0),
            (hx0, hy0 + 10.0),
        ],
        is_closed=True,
    )
    hatch.paths.add_polyline_path(
        [
            (hx0 + 5.0, hy0 + 2.0),
            (hx0 + 9.0, hy0 + 2.0),
            (hx0 + 9.0, hy0 + 5.0),
            (hx0 + 5.0, hy0 + 5.0),
        ],
        is_closed=True,
    )
    dup_isl = msp.add_lwpolyline(
        [
            (hx0 + 5.0, hy0 + 2.0),
            (hx0 + 9.0, hy0 + 2.0),
            (hx0 + 9.0, hy0 + 5.0),
            (hx0 + 5.0, hy0 + 5.0),
        ],
        dxfattribs={"layer": "ZOLDSZIGET_HATCH"},
    )
    dup_isl.closed = True

    # -------------------------------------------------------------------------
    # 4. Exploded 4-LINE Polygon on 'EXPLODED_BOX' (10m x 8m = 80.0 m2)
    # -------------------------------------------------------------------------
    ex0 = x0 + 200.0
    ey0 = y0
    msp.add_line((ex0, ey0), (ex0 + 10.0, ey0), dxfattribs={"layer": "EXPLODED_BOX"})
    msp.add_line((ex0 + 10.0, ey0), (ex0 + 10.0, ey0 + 8.0), dxfattribs={"layer": "EXPLODED_BOX"})
    msp.add_line((ex0 + 10.0, ey0 + 8.0), (ex0, ey0 + 8.0), dxfattribs={"layer": "EXPLODED_BOX"})
    msp.add_line((ex0, ey0 + 8.0), (ex0, ey0), dxfattribs={"layer": "EXPLODED_BOX"})

    # -------------------------------------------------------------------------
    # 5. Linear Curb Layer 'SZEGELY_KIEMELT':
    #    - 100.0m main straight line
    #    - 20.0m exact duplicate line on top of [0..20]
    #    - 20.0m another exact duplicate of [0..20] (to trigger exact duplicate line warning)
    #    - 25.0m partially overlapping line on [15..40]
    #    - 10.0m horizontal line with Flying-Z (Z=0.0m to Z=114.5m) -> 2D length = 10.0m
    #    - 90-degree ARC of radius R=10.0m -> curved length = 5*pi = 15.707963m
    #    Total Gross Straight = 100 + 20 + 20 + 25 + 10 = 175.000m
    #    Total Gross Curved   = 15.708m
    #    Total Gross          = 190.708m
    #    Total Deducted Overlap = 20 + 20 + 25 = 65.000m
    #    Total Net Length     = 100 + 10 + 15.708 = 125.708m
    # -------------------------------------------------------------------------
    cy = y0 - 20.0
    msp.add_line((x0, cy, 0.0), (x0 + 100.0, cy, 0.0), dxfattribs={"layer": "SZEGELY_KIEMELT"})
    msp.add_line((x0, cy, 0.0), (x0 + 20.0, cy, 0.0), dxfattribs={"layer": "SZEGELY_KIEMELT"})
    msp.add_line((x0, cy, 0.0), (x0 + 20.0, cy, 0.0), dxfattribs={"layer": "SZEGELY_KIEMELT"})
    msp.add_line(
        (x0 + 15.0, cy, 0.0), (x0 + 40.0, cy, 0.0), dxfattribs={"layer": "SZEGELY_KIEMELT"}
    )
    # Flying Z line (10m in XY, 114.5m jump in Z)
    msp.add_line(
        (x0, cy - 10.0, 0.0),
        (x0 + 10.0, cy - 10.0, 114.50),
        dxfattribs={"layer": "SZEGELY_KIEMELT"},
    )
    # Curved ARC of R=10m, 0 to 90 degrees
    msp.add_arc(
        center=(x0 + 120.0, cy),
        radius=10.0,
        start_angle=0.0,
        end_angle=90.0,
        dxfattribs={"layer": "SZEGELY_KIEMELT"},
    )

    # -------------------------------------------------------------------------
    # 6. Manhole Block INSERT with ATTRIB tags on 'CSAPADEK_AKNA' & MTEXT on 'FELIRATOK'
    # -------------------------------------------------------------------------
    blk = doc.blocks.new(name="AKNA_D1000")
    blk.add_circle((0.0, 0.0), radius=0.50)
    blk.add_attdef("AKNA_ID", (0.5, 0.5), height=0.35)
    blk.add_attdef("FEDLAP", (0.5, 0.1), height=0.35)
    blk.add_attdef("FOLYASFENEK", (0.5, -0.3), height=0.35)

    ins = msp.add_blockref("AKNA_D1000", (x0 + 10.0, y0 - 5.0), dxfattribs={"layer": "CSAPADEK_AKNA"})
    ins.add_auto_attribs({"AKNA_ID": "CS-01", "FEDLAP": "114,50", "FOLYASFENEK": "112,00"})

    mtext = msp.add_mtext(
        r"{\fArial|b1;Kiemelt szegély 15x25x100\P%%c1000 tisztítóakna}",
        dxfattribs={"layer": "FELIRATOK", "char_height": 0.40},
    )
    mtext.set_location((x0 + 15.0, y0 - 5.0))

    txt = msp.add_text(
        "Térkő burkolat 8 cm",
        height=0.50,
        dxfattribs={"layer": "FELIRATOK"},
    )
    txt.set_placement((x0 + 20.0, y0 + 10.0), align=TextEntityAlignment.LEFT)

    # -------------------------------------------------------------------------
    # 7. Self-intersecting 'bowtie' figure-8 polygon on 'BOWTIE_LAYER':
    #    Two 10m x 5m triangles touching at apex -> each triangle has base=10, height=5 -> 25 m2
    #    Total valid area after shapely.make_valid() = 25.0 + 25.0 = 50.0 m2
    # -------------------------------------------------------------------------
    bx0 = x0 + 250.0
    by0 = y0
    bowtie_pts = [
        (bx0, by0),
        (bx0 + 10.0, by0 + 10.0),
        (bx0 + 10.0, by0),
        (bx0, by0 + 10.0),
    ]
    bt = msp.add_lwpolyline(bowtie_pts, dxfattribs={"layer": "BOWTIE_LAYER"})
    bt.closed = True

    # -------------------------------------------------------------------------
    # 8. Mirrored OCS LWPOLYLINE (extrusion = (0, 0, -1), 10m x 5m = 50.0 m2)
    # -------------------------------------------------------------------------
    mir_pts = [
        (-x0 - 270.0, y0),
        (-x0 - 260.0, y0),
        (-x0 - 260.0, y0 + 5.0),
        (-x0 - 270.0, y0 + 5.0),
    ]
    mir_poly = msp.add_lwpolyline(
        mir_pts,
        dxfattribs={"layer": "MIRRORED_OCS", "extrusion": (0.0, 0.0, -1.0)},
    )
    mir_poly.closed = True

    # -------------------------------------------------------------------------
    # 9. Millimeter block (2000 x 1000) inserted with scale=0.001 into meter drawing
    #    Resulting WCS size = 2.0m x 1.0m -> Area = 2.00 m2, Perimeter = 6.00 m
    # -------------------------------------------------------------------------
    mm_blk = doc.blocks.new(name="MM_SLAB_2000x1000")
    mm_poly = mm_blk.add_lwpolyline(
        [(0.0, 0.0), (2000.0, 0.0), (2000.0, 1000.0), (0.0, 1000.0)],
        dxfattribs={"layer": "0"},
    )
    mm_poly.closed = True
    msp.add_blockref(
        "MM_SLAB_2000x1000",
        (x0 + 280.0, y0),
        dxfattribs={
            "layer": "SCALED_INSERT_LAYER",
            "xscale": 0.001,
            "yscale": 0.001,
            "zscale": 0.001,
        },
    )

    # -------------------------------------------------------------------------
    # 10. Paperspace entity on 'Layout1' (must be ignored by Modelspace QTO)
    # -------------------------------------------------------------------------
    psp = doc.layouts.get("Layout1")
    psp.add_lwpolyline([(0, 0), (841, 0), (841, 594), (0, 594)], close=True)

    out_path.parent.mkdir(parents=True, exist_ok=True)
    doc.saveas(str(out_path))
    return out_path


def create_utility_network_dxf(file_path: str | Path) -> Path:
    """
    Generate a synthetic utility network DXF testing:
    1. Node CS-01: INSERT block with ATTRIBs (F=114,50, Ff=112,00, DN1000) -> h1=2.50m, h2=2.33m
    2. Node CS-02: CIRCLE (R=0.50m) at (40, 0) matched via LEADER arrow to MTEXT at (43, 4)
       ("CS-02\\PF: 114,30\\PFf: 111,80") -> h1=2.50m, h2=2.33m
    3. Node CS-03: CIRCLE (R=0.60m) at (80, 0) matched via STRtree spatial proximity to TEXT
       ("CS-03 OK: 114.10 S: 111.10 DN1200") -> h1=3.00m, h2=2.83m
    4. Node VA-01: Closed 0.50x0.50m LWPOLYLINE box gully at (40, -10) with NO level texts
    5. Pipe 1: (0,0)->(40,0) L=40.0m, callout "KG-PVC DN300 L=40,0m"
    6. Pipe 2: (40,0)->(80,0) L=40.0m, callout "PP Pragma DN400"
    7. Pipe 3: (40,-10)->(60,-10) L=20.0m, no endpoint invert levels (tests fallback / MISSING_INVERT_LEVEL)
    """
    out_path = Path(file_path)
    doc = ezdxf.new("R2018")
    doc.header["$INSUNITS"] = 6  # Meters
    msp = doc.modelspace()

    for lyr_name in ["AKNAK", "CSATORNA_GERINC", "KOZMU_FELIRAT"]:
        doc.layers.add(lyr_name)

    # 1. Node CS-01 (INSERT with ATTRIBs) at (0, 0)
    blk = doc.blocks.new(name="AKNA_D1000")
    blk.add_circle((0.0, 0.0), radius=0.50)
    blk.add_attdef("AKNA_ID", (0.5, 0.5), height=0.35)
    blk.add_attdef("FEDLAP", (0.5, 0.1), height=0.35)
    blk.add_attdef("FOLYASFENEK", (0.5, -0.3), height=0.35)

    ins = msp.add_blockref("AKNA_D1000", (0.0, 0.0), dxfattribs={"layer": "AKNAK"})
    ins.add_auto_attribs({"AKNA_ID": "CS-01", "FEDLAP": "114,50", "FOLYASFENEK": "112,00"})

    # 2. Node CS-02 (CIRCLE at (40, 0)) + LEADER arrow from (40.1, 0.1) to (43.0, 4.0) + MTEXT
    msp.add_circle((40.0, 0.0), radius=0.50, dxfattribs={"layer": "AKNAK"})
    msp.add_leader(
        vertices=[(40.1, 0.1), (42.0, 3.0), (43.0, 4.0)],
        dxfattribs={"layer": "KOZMU_FELIRAT"},
    )
    mt2 = msp.add_mtext(
        r"CS-02\PF: 114,30 mBf\PFf: 111,80 mBf\PDN1000",
        dxfattribs={"layer": "KOZMU_FELIRAT", "char_height": 0.35},
    )
    mt2.set_location((43.1, 4.1))

    # 3. Node CS-03 (CIRCLE at (80, 0)) + nearby TEXT with German OK/S syntax
    msp.add_circle((80.0, 0.0), radius=0.60, dxfattribs={"layer": "AKNAK"})
    t3 = msp.add_text(
        "CS-03 OK: 114.10 S: 111.10 DN1200",
        height=0.35,
        dxfattribs={"layer": "KOZMU_FELIRAT"},
    )
    t3.set_placement((80.5, 1.2), align=TextEntityAlignment.LEFT)

    # 4. Node VA-01 (Closed 0.50x0.50m gully box at (40, -10)) without level texts
    gully = msp.add_lwpolyline(
        [(39.75, -10.25), (40.25, -10.25), (40.25, -9.75), (39.75, -9.75)],
        dxfattribs={"layer": "AKNAK"},
    )
    gully.closed = True

    # 5. Pipes on 'CSATORNA_GERINC'
    msp.add_line((0.0, 0.0), (40.0, 0.0), dxfattribs={"layer": "CSATORNA_GERINC"})
    tp1 = msp.add_text(
        "KG-PVC DN300 L=40,0m",
        height=0.35,
        dxfattribs={"layer": "KOZMU_FELIRAT"},
    )
    tp1.set_placement((18.0, 0.6), align=TextEntityAlignment.LEFT)

    msp.add_line((40.0, 0.0), (80.0, 0.0), dxfattribs={"layer": "CSATORNA_GERINC"})
    tp2 = msp.add_text(
        "PP Pragma DN400",
        height=0.35,
        dxfattribs={"layer": "KOZMU_FELIRAT"},
    )
    tp2.set_placement((58.0, 0.6), align=TextEntityAlignment.LEFT)

    # Pipe 3 without invert levels at (40, -10) -> (60, -10)
    msp.add_line((40.0, -10.0), (60.0, -10.0), dxfattribs={"layer": "CSATORNA_GERINC"})

    out_path.parent.mkdir(parents=True, exist_ok=True)
    doc.saveas(str(out_path))
    return out_path

