"""
v0.2 Module 2: Precast Concrete Manhole & Road Gully Element Optimizer.
Tool 7 implementation: optimize_precast_manhole_elements

Supports:
- Hungarian SzeBeton T2 (mortar-joint, step-less precast system: AFE, ASZ, AFT, AGY, SZB)
- International DIN EN 1917 / DIN V 4034-1 precast manhole modules
- Automatic shallow manhole detection (switches from 60cm cone ASZ to 20cm flat cover slab AFT)
- Combinatorial ring optimization (minimizes joint count while keeping SZB stack in [6..30] cm)
- Surface-aware D400 cover selection (600x600mm square-frame HYDROTEC circular cover in paving blocks)
- 50x50cm precast road gully (VA-F + VA-GY + DUNA 500x500 D400 grate) decomposition
- Consolidated Bill of Materials (BOM) with optional +1 spare SZB 60/10 ring per manhole
"""

from __future__ import annotations

from collections import OrderedDict
from typing import Literal

from civil_qto_mcp.models.dxf_models import (
    ManholeAssemblyBreakdown,
    ManholeOptimizationReport,
    ManholeSpecInput,
    PrecastBomSummaryItem,
    PrecastElementLine,
)


def _solve_shaft_and_adjustment_rings(
    target_stack_cm: int,
    shaft_ring_heights_cm: tuple[int, ...] = (100, 75, 50, 25),
    adj_ring_heights_cm: tuple[int, ...] = (10, 8, 6),
    max_adj_stack_cm: int = 30,
) -> tuple[list[int], list[int], int]:
    """
    Find the optimal combination of shaft rings (AGY) and adjustment rings (SZB)
    whose sum is as close as possible to `target_stack_cm` (in integer cm).

    Optimization objective (lexicographic):
    1. Minimize absolute height error |built_cm - target_stack_cm|
    2. Prefer adjustment ring stack within [6..max_adj_stack_cm] cm (or 0 if exact)
    3. Minimize total number of precast elements (joints)
    Returns (chosen_shaft_rings_cm, chosen_adj_rings_cm, built_stack_cm).
    """
    if target_stack_cm <= 0:
        return [], [], 0

    # Generate all valid adjustment ring combinations up to max_adj_stack_cm
    adj_combos: dict[int, list[int]] = {0: []}
    for n10 in range(4):
        for n8 in range(4):
            for n6 in range(4):
                h_adj = n10 * 10 + n8 * 8 + n6 * 6
                if 0 < h_adj <= max_adj_stack_cm:
                    combo = [10] * n10 + [8] * n8 + [6] * n6
                    if h_adj not in adj_combos or len(combo) < len(adj_combos[h_adj]):
                        adj_combos[h_adj] = combo

    # Generate shaft ring combinations (25cm multiples up to target_stack_cm + 25)
    max_shaft_h = max(0, target_stack_cm + 25)
    shaft_combos: dict[int, list[int]] = {0: []}
    for h_s in range(25, max_shaft_h + 1, 25):
        rem = h_s
        s_list: list[int] = []
        for r_h in shaft_ring_heights_cm:
            while rem >= r_h:
                s_list.append(r_h)
                rem -= r_h
        if rem == 0:
            shaft_combos[h_s] = s_list

    best_score: tuple[int, int, int] | None = None
    best_shaft: list[int] = []
    best_adj: list[int] = []
    best_total = 0

    for h_s, s_rings in shaft_combos.items():
        for h_a, a_rings in adj_combos.items():
            total_h = h_s + h_a
            err = abs(total_h - target_stack_cm)
            # Prefer having at least one adjustment ring (6..24cm) for fine site leveling
            no_adj_penalty = 1 if h_a == 0 and target_stack_cm >= 6 else 0
            piece_count = len(s_rings) + len(a_rings)
            score = (err, no_adj_penalty, piece_count)
            if best_score is None or score < best_score:
                best_score = score
                best_shaft = s_rings
                best_adj = a_rings
                best_total = total_h

    return best_shaft, best_adj, best_total


def optimize_precast_manhole_elements(
    manholes: list[ManholeSpecInput],
    catalog_profile: Literal[
        "DIN_EN_1917_STD", "ASTM_C478_US", "SZEBETON_T2_HU"
    ] = "SZEBETON_T2_HU",
    cover_frame_height_cm: float = 17.0,
    spare_adjustment_ring_per_manhole: bool = True,
) -> ManholeOptimizationReport:
    """
    Decompose a list of manholes and road gullies into optimal precast concrete elements
    and generate an aggregated manufacturer Bill of Materials (BOM).
    """
    assemblies: list[ManholeAssemblyBreakdown] = []
    warnings: list[str] = []
    manholes_count = 0
    gullies_count = 0
    shallow_count = 0

    # Ordered dictionary for aggregated BOM: element_code -> PrecastBomSummaryItem
    bom_map: OrderedDict[str, PrecastBomSummaryItem] = OrderedDict()

    def _add_to_bom(line: PrecastElementLine, spare: int = 0) -> None:
        if line.element_code not in bom_map:
            bom_map[line.element_code] = PrecastBomSummaryItem(
                element_code=line.element_code,
                category=line.category,
                description=line.description,
                unit="pcs",
                net_quantity=0,
                spare_quantity=0,
                total_order_quantity=0,
            )
        entry = bom_map[line.element_code]
        entry.net_quantity += line.quantity
        entry.spare_quantity += spare
        entry.total_order_quantity = entry.net_quantity + entry.spare_quantity

    for spec in manholes:
        # Determine total depth h1 in cm
        if spec.total_depth_h1_m is not None and spec.total_depth_h1_m > 0:
            h1_cm = round(spec.total_depth_h1_m * 100.0, 1)
        elif spec.cover_level_m is not None and spec.invert_level_m is not None:
            h1_cm = round((spec.cover_level_m - spec.invert_level_m) * 100.0, 1)
        else:
            warnings.append(
                f"Structure '{spec.node_id}': missing both (cover_level_m, invert_level_m) and total_depth_h1_m; skipped."
            )
            continue

        if h1_cm <= 0:
            warnings.append(
                f"Structure '{spec.node_id}': invalid depth h1={h1_cm} cm (<= 0); skipped."
            )
            continue

        # ---------------------------------------------------------------------
        # CASE A: 50x50 cm Precast Road Gully (Víznyelő akna)
        # ---------------------------------------------------------------------
        if spec.node_type == "GULLY_50X50" or spec.nominal_diameter_mm <= 600:
            gullies_count += 1
            elements: list[PrecastElementLine] = []
            notes: list[str] = []

            # Standard 50/50 gully: VA-F 80cm base + VA-GY 100cm / 50cm shaft sections
            base_line = PrecastElementLine(
                element_code="VA-F 50/50/80",
                category="BASE",
                description="Előregyártott víznyelő aknafenék iszapfogóval 50x50 cm, H=80 cm",
                height_cm=80.0,
                quantity=1,
            )
            elements.append(base_line)
            _add_to_bom(base_line)

            grate_h_cm = 12.0
            h2_gully_cm = max(0.0, h1_cm - grate_h_cm)
            rem_gully_cm = max(0.0, h2_gully_cm - 80.0)

            n_100 = int(round(rem_gully_cm) // 100)
            rem_after_100 = round(rem_gully_cm) - n_100 * 100
            n_50 = 1 if rem_after_100 >= 25 else 0

            if n_100 > 0:
                gy100 = PrecastElementLine(
                    element_code="VA-GY 50/50/100",
                    category="SHAFT_RING",
                    description="Víznyelő aknagyűrű elem 50x50 cm, H=100 cm",
                    height_cm=100.0,
                    quantity=n_100,
                )
                elements.append(gy100)
                _add_to_bom(gy100)

            if n_50 > 0:
                gy50 = PrecastElementLine(
                    element_code="VA-GY 50/50/50",
                    category="SHAFT_RING",
                    description="Víznyelő aknagyűrű elem 50x50 cm, H=50 cm",
                    height_cm=50.0,
                    quantity=n_50,
                )
                elements.append(gy50)
                _add_to_bom(gy50)

            grate_line = PrecastElementLine(
                element_code="DUNA 500x500 D400",
                category="COVER_OR_GRATE",
                description="Öntöttvas víznyelőrács kerettel 500x500 mm, D400 (400 kN)",
                height_cm=grate_h_cm,
                quantity=1,
            )
            elements.append(grate_line)
            _add_to_bom(grate_line)

            built_precast = 80.0 + n_100 * 100.0 + n_50 * 50.0
            built_total = built_precast + grate_h_cm
            assemblies.append(
                ManholeAssemblyBreakdown(
                    node_id=spec.node_id,
                    node_type="GULLY_50X50",
                    nominal_diameter_mm=500,
                    surface_type=spec.surface_type,
                    target_total_depth_h1_cm=h1_cm,
                    cover_deduction_cm=grate_h_cm,
                    target_net_precast_height_h2_cm=round(h2_gully_cm, 1),
                    built_precast_height_cm=built_precast,
                    built_total_height_cm=built_total,
                    height_residual_cm=round(built_total - h1_cm, 1),
                    used_flat_cover_slab=False,
                    elements=elements,
                    engineering_notes=notes,
                )
            )
            continue

        # ---------------------------------------------------------------------
        # CASE B: Circular Precast Manhole (DN1000 / DN1200 / ASTM C478 48")
        # ---------------------------------------------------------------------
        manholes_count += 1
        dn = 1200 if (spec.nominal_diameter_mm >= 1100 or catalog_profile == "ASTM_C478_US") else 1000
        d_tag = "D120" if dn == 1200 else "D100"
        d_num = "120" if dn == 1200 else "100"
        if catalog_profile == "SZEBETON_T2_HU":
            sys_suffix = "T2"
        elif catalog_profile == "ASTM_C478_US":
            sys_suffix = "ASTM-C478"
        else:
            sys_suffix = "EN1917"

        h2_cm = round(h1_cm - cover_frame_height_cm, 1)
        base_h_cm = 75.0 if dn == 1200 else 65.0
        cone_h_cm = 60.0
        flat_slab_h_cm = 20.0

        elements = []
        notes = []
        used_flat_slab = False

        # 1. Base element (AFE / Precast Base)
        afe_line = PrecastElementLine(
            element_code=f"AFE {d_tag} {sys_suffix}",
            category="BASE",
            description=f"Precast concrete manhole base DN{dn} ({sys_suffix}), H={int(base_h_cm)} cm",
            height_cm=base_h_cm,
            quantity=1,
        )
        elements.append(afe_line)
        _add_to_bom(afe_line)

        # 2. Check if standard 60cm cone (ASZ) fits, or if a shallow 20cm flat cover slab (AFT) is required
        if h2_cm < base_h_cm + cone_h_cm + 6.0:
            used_flat_slab = True
            shallow_count += 1
            top_h_cm = flat_slab_h_cm
            top_line = PrecastElementLine(
                element_code=f"AFT {d_num}/60 {sys_suffix}",
                category="CONE_OR_SLAB",
                description=f"Reinforced concrete flat cover slab (shallow manhole) DN{dn}/600, H=20 cm",
                height_cm=flat_slab_h_cm,
                quantity=1,
            )
            msg = (
                f"Manhole '{spec.node_id}': shallow net height h2={h2_cm:.1f} cm < {base_h_cm + cone_h_cm + 6.0:.0f} cm; "
                f"switched from 60cm cone (ASZ) to 20cm flat cover slab (AFT {d_num}/60)."
            )
            notes.append(msg)
            warnings.append(msg)
        else:
            top_h_cm = cone_h_cm
            top_line = PrecastElementLine(
                element_code=f"ASZ {d_num}/60 {sys_suffix}",
                category="CONE_OR_SLAB",
                description=f"Precast eccentric cone reducer DN{dn}/600 ({sys_suffix}), H=60 cm",
                height_cm=cone_h_cm,
                quantity=1,
            )

        # 3. Optimize Shaft Rings (AGY) and Adjustment Rings (SZB)
        rem_stack_cm = max(0, round(h2_cm - base_h_cm - top_h_cm))
        shaft_rings, adj_rings, built_stack_cm = _solve_shaft_and_adjustment_rings(rem_stack_cm)

        # Group shaft rings by height descending
        for r_h in (100, 75, 50, 25):
            cnt = shaft_rings.count(r_h)
            if cnt > 0:
                agy_line = PrecastElementLine(
                    element_code=f"AGY {d_num}/{r_h} {sys_suffix}",
                    category="SHAFT_RING",
                    description=f"Precast concrete shaft ring DN{dn} ({sys_suffix}), H={r_h} cm",
                    height_cm=float(r_h),
                    quantity=cnt,
                )
                elements.append(agy_line)
                _add_to_bom(agy_line)

        # Append Cone / Flat Slab above shaft rings
        elements.append(top_line)
        _add_to_bom(top_line)

        # Group adjustment rings (SZB 60/10, 60/8, 60/6)
        for a_h in (10, 8, 6):
            cnt = adj_rings.count(a_h)
            if cnt > 0:
                szb_line = PrecastElementLine(
                    element_code=f"SZB 60/{a_h}",
                    category="ADJUSTMENT_RING",
                    description=f"Precast concrete grade adjustment ring DN600, H={a_h} cm",
                    height_cm=float(a_h),
                    quantity=cnt,
                )
                elements.append(szb_line)
                _add_to_bom(szb_line)

        # Add +1 spare SZB 60/10 per manhole if requested
        if spare_adjustment_ring_per_manhole:
            spare_dummy = PrecastElementLine(
                element_code="SZB 60/10",
                category="ADJUSTMENT_RING",
                description="Precast concrete grade adjustment ring DN600, H=10 cm",
                height_cm=10.0,
                quantity=0,
            )
            _add_to_bom(spare_dummy, spare=1)

        # 4. Select Cover (Paving block -> 600x600 square-frame circular cover HYDROTEC; else circular D400)
        if spec.surface_type == "PAVING_BLOCK":
            cov_line = PrecastElementLine(
                element_code="FEDLAP D400 600x600 HYDROTEC",
                category="COVER_OR_GRATE",
                description="600x600 mm square-frame ductile iron circular cover D400 (for block paving, HYDROTEC)",
                height_cm=cover_frame_height_cm,
                quantity=1,
            )
            notes.append(
                "Block paving surface: selected 600x600 mm square-frame D400 circular cover."
            )
        else:
            cov_line = PrecastElementLine(
                element_code="FEDLAP D400 D600 KOR",
                category="COVER_OR_GRATE",
                description="Ø600 mm ductile iron manhole cover and frame D400 (400 kN, EN 124)",
                height_cm=cover_frame_height_cm,
                quantity=1,
            )
        elements.append(cov_line)
        _add_to_bom(cov_line)

        built_precast_cm = base_h_cm + top_h_cm + float(built_stack_cm)
        built_total_cm = built_precast_cm + cover_frame_height_cm
        residual_cm = round(built_total_cm - h1_cm, 1)

        assemblies.append(
            ManholeAssemblyBreakdown(
                node_id=spec.node_id,
                node_type="MANHOLE",
                nominal_diameter_mm=dn,
                surface_type=spec.surface_type,
                target_total_depth_h1_cm=h1_cm,
                cover_deduction_cm=cover_frame_height_cm,
                target_net_precast_height_h2_cm=h2_cm,
                built_precast_height_cm=built_precast_cm,
                built_total_height_cm=built_total_cm,
                height_residual_cm=residual_cm,
                used_flat_cover_slab=used_flat_slab,
                elements=elements,
                engineering_notes=notes,
            )
        )

    return ManholeOptimizationReport(
        catalog_profile=catalog_profile,
        total_structures_processed=len(assemblies),
        manholes_count=manholes_count,
        gullies_count=gullies_count,
        shallow_flat_slab_count=shallow_count,
        assemblies=assemblies,
        consolidated_bom=list(bom_map.values()),
        warnings=warnings,
    )
