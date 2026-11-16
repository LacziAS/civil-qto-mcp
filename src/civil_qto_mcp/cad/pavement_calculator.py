"""
v0.2 Module 3: Pavement Structural Layer & Lateral Step-Out Quantity Calculator.
Tool 8 implementation: calculate_pavement_layer_quantities

Calculates per-layer area (m2), compacted geometric volume (m3), loose/order volume (m3),
and quarry/plant order tonnage (t) from net CAD surface area and active edge perimeter.

Includes standard presets:
- LIDL_BBS_PARKING_PAVING: 8cm paving block + 3cm 2/5 bedding + 20cm CKT-4 (x1.20) + 25cm FZKA 0/63 + Geotextile
- HEAVY_INDUSTRIAL_PAVING: 10cm heavy paving + 3cm bedding + 25cm CKT-4 (x1.20) + 30cm FZKA 0/63 + Geotextile
- URBAN_ASPHALT_ROAD: 4cm AC 11 wearing + 7cm AC 22 binder + 20cm FZKA 0/32 + 20cm FZKA 0/63
"""

from __future__ import annotations

from typing import Literal

from civil_qto_mcp.models.dxf_models import (
    PavementCalculationReport,
    PavementLayerInput,
    PavementLayerQuantityResult,
)

PAVEMENT_PRESETS: dict[str, list[PavementLayerInput]] = {
    "LIDL_BBS_PARKING_PAVING": [
        PavementLayerInput(
            layer_order=1,
            layer_name="Concrete block paving 20x20x8 cm (EN 1338 / Lidl BBS / Térkő 8 cm)",
            layer_category="PAVING_BLOCK",
            thickness_cm=8.0,
            lateral_step_out_m=0.0,
            compaction_factor=1.0,
            waste_or_overlap_factor=1.03,
            bulk_density_t_per_m3=None,
        ),
        PavementLayerInput(
            layer_order=2,
            layer_name="Crushed stone bedding course 2/5 mm (3 cm / NZ 2/5 ágyazat)",
            layer_category="BEDDING_SAND_STONE",
            thickness_cm=3.0,
            lateral_step_out_m=0.0,
            compaction_factor=1.15,
            waste_or_overlap_factor=1.05,
            bulk_density_t_per_m3=1.65,
        ),
        PavementLayerInput(
            layer_order=3,
            layer_name="CBM / CKT-4 hydraulically bound base course (20 cm, EN 14227, x1.20 compaction)",
            layer_category="CONCRETE_OR_CKT",
            thickness_cm=20.0,
            lateral_step_out_m=0.15,
            compaction_factor=1.20,
            waste_or_overlap_factor=1.03,
            bulk_density_t_per_m3=2.25,
        ),
        PavementLayerInput(
            layer_order=4,
            layer_name="Unbound crushed stone subbase 0/63 mm (25 cm, EN 13285 / FZKA 0/63)",
            layer_category="GRANULAR_SUBBASE",
            thickness_cm=25.0,
            lateral_step_out_m=0.30,
            compaction_factor=1.22,
            waste_or_overlap_factor=1.03,
            bulk_density_t_per_m3=2.05,
        ),
        PavementLayerInput(
            layer_order=5,
            layer_name="Separation & filtration geotextile 200 g/m2 (+12% overlap)",
            layer_category="GEOSYNTHETIC",
            thickness_cm=0.0,
            lateral_step_out_m=0.40,
            compaction_factor=1.0,
            waste_or_overlap_factor=1.12,
            bulk_density_t_per_m3=None,
        ),
    ],
    "HEAVY_INDUSTRIAL_PAVING": [
        PavementLayerInput(
            layer_order=1,
            layer_name="Heavy-duty industrial concrete block paving 10 cm (EN 1338 / Ipari térkő)",
            layer_category="PAVING_BLOCK",
            thickness_cm=10.0,
            lateral_step_out_m=0.0,
            compaction_factor=1.0,
            waste_or_overlap_factor=1.03,
            bulk_density_t_per_m3=None,
        ),
        PavementLayerInput(
            layer_order=2,
            layer_name="Crushed stone bedding course 2/5 mm (3 cm / NZ 2/5 ágyazat)",
            layer_category="BEDDING_SAND_STONE",
            thickness_cm=3.0,
            lateral_step_out_m=0.0,
            compaction_factor=1.15,
            waste_or_overlap_factor=1.05,
            bulk_density_t_per_m3=1.65,
        ),
        PavementLayerInput(
            layer_order=3,
            layer_name="CBM / CKT-4 hydraulically bound base course (25 cm, EN 14227, x1.20 compaction)",
            layer_category="CONCRETE_OR_CKT",
            thickness_cm=25.0,
            lateral_step_out_m=0.20,
            compaction_factor=1.20,
            waste_or_overlap_factor=1.03,
            bulk_density_t_per_m3=2.25,
        ),
        PavementLayerInput(
            layer_order=4,
            layer_name="Unbound crushed stone subbase 0/63 mm (30 cm, EN 13285 / FZKA 0/63)",
            layer_category="GRANULAR_SUBBASE",
            thickness_cm=30.0,
            lateral_step_out_m=0.40,
            compaction_factor=1.22,
            waste_or_overlap_factor=1.03,
            bulk_density_t_per_m3=2.05,
        ),
        PavementLayerInput(
            layer_order=5,
            layer_name="Separation geotextile N300 (+12% overlap)",
            layer_category="GEOSYNTHETIC",
            thickness_cm=0.0,
            lateral_step_out_m=0.50,
            compaction_factor=1.0,
            waste_or_overlap_factor=1.12,
            bulk_density_t_per_m3=None,
        ),
    ],
    "URBAN_ASPHALT_ROAD": [
        PavementLayerInput(
            layer_order=1,
            layer_name="AC 11 wearing course asphalt (4 cm, EN 13108-1 / AC 11 kopó)",
            layer_category="ASPHALT",
            thickness_cm=4.0,
            lateral_step_out_m=0.0,
            compaction_factor=1.0,
            waste_or_overlap_factor=1.03,
            bulk_density_t_per_m3=2.40,
        ),
        PavementLayerInput(
            layer_order=2,
            layer_name="AC 22 binder/base course asphalt (8 cm, EN 13108-1 / AC 22 kötő)",
            layer_category="ASPHALT",
            thickness_cm=8.0,
            lateral_step_out_m=0.05,
            compaction_factor=1.0,
            waste_or_overlap_factor=1.03,
            bulk_density_t_per_m3=2.40,
        ),
        PavementLayerInput(
            layer_order=3,
            layer_name="Unbound crushed stone road base 0/32 mm (20 cm, EN 13285 / FZKA 0/32)",
            layer_category="GRANULAR_SUBBASE",
            thickness_cm=20.0,
            lateral_step_out_m=0.20,
            compaction_factor=1.22,
            waste_or_overlap_factor=1.03,
            bulk_density_t_per_m3=2.05,
        ),
        PavementLayerInput(
            layer_order=4,
            layer_name="Frost-protection granular subbase 0/63 mm (20 cm, EN 13285 / FZKA 0/63)",
            layer_category="GRANULAR_SUBBASE",
            thickness_cm=20.0,
            lateral_step_out_m=0.35,
            compaction_factor=1.22,
            waste_or_overlap_factor=1.03,
            bulk_density_t_per_m3=2.05,
        ),
    ],
}


def calculate_pavement_layer_quantities(
    net_surface_area_m2: float,
    active_edge_perimeter_m: float = 0.0,
    preset_name: Literal[
        "LIDL_BBS_PARKING_PAVING", "HEAVY_INDUSTRIAL_PAVING", "URBAN_ASPHALT_ROAD"
    ]
    | None = None,
    custom_layers: list[PavementLayerInput] | None = None,
) -> PavementCalculationReport:
    """
    Calculate multi-layer road and paving structural quantities (m2, m3, metric tons)
    including lateral step-out widening, compaction multipliers (e.g., CKT x1.20),
    and cutting/overlap waste.
    """
    if net_surface_area_m2 <= 0:
        raise ValueError(f"net_surface_area_m2 must be positive, got {net_surface_area_m2}")
    if active_edge_perimeter_m < 0:
        raise ValueError(
            f"active_edge_perimeter_m must be non-negative, got {active_edge_perimeter_m}"
        )

    if custom_layers:
        layer_defs = sorted(custom_layers, key=lambda x: x.layer_order)
        used_preset: str | None = None
    elif preset_name and preset_name in PAVEMENT_PRESETS:
        layer_defs = PAVEMENT_PRESETS[preset_name]
        used_preset = preset_name
    else:
        layer_defs = PAVEMENT_PRESETS["LIDL_BBS_PARKING_PAVING"]
        used_preset = "LIDL_BBS_PARKING_PAVING"

    results: list[PavementLayerQuantityResult] = []
    total_thickness_cm = 0.0
    total_subgrade_exc_m3 = 0.0

    for lyr in layer_defs:
        total_thickness_cm += lyr.thickness_cm
        step_extra_m2 = round(active_edge_perimeter_m * lyr.lateral_step_out_m, 3)
        eff_area_m2 = round(net_surface_area_m2 + step_extra_m2, 3)

        thick_m = lyr.thickness_cm / 100.0
        geom_vol_m3 = round(eff_area_m2 * thick_m, 3)
        total_subgrade_exc_m3 += geom_vol_m3

        order_area_m2 = round(eff_area_m2 * lyr.waste_or_overlap_factor, 2)
        order_vol_m3 = round(
            geom_vol_m3 * lyr.compaction_factor * lyr.waste_or_overlap_factor, 2
        )

        order_tons: float | None = None
        if lyr.bulk_density_t_per_m3 is not None and lyr.bulk_density_t_per_m3 > 0:
            order_tons = round(
                geom_vol_m3 * lyr.bulk_density_t_per_m3 * lyr.waste_or_overlap_factor, 2
            )

        if lyr.layer_category in ("PAVING_BLOCK", "GEOSYNTHETIC"):
            prim_unit: Literal["m2", "m3", "t"] = "m2"
            prim_qty = order_area_m2
            note = (
                f"Area = ({net_surface_area_m2:.2f} + {active_edge_perimeter_m:.2f}*{lyr.lateral_step_out_m:.2f}) "
                f"* {lyr.waste_or_overlap_factor:.2f} (waste/overlap) = {order_area_m2:.2f} m2"
            )
        elif lyr.layer_category == "CONCRETE_OR_CKT":
            prim_unit = "m3"
            prim_qty = order_vol_m3
            note = (
                f"Order Vol = {eff_area_m2:.2f} m2 * {thick_m:.2f} m * {lyr.compaction_factor:.2f} (compaction) "
                f"* {lyr.waste_or_overlap_factor:.2f} (waste) = {order_vol_m3:.2f} m3"
            )
        elif order_tons is not None:
            prim_unit = "t"
            prim_qty = order_tons
            note = (
                f"Order Mass = {geom_vol_m3:.2f} m3 (compacted) * {lyr.bulk_density_t_per_m3:.2f} t/m3 "
                f"* {lyr.waste_or_overlap_factor:.2f} (waste) = {order_tons:.2f} t (Order Vol = {order_vol_m3:.2f} m3)"
            )
        else:
            prim_unit = "m3"
            prim_qty = order_vol_m3
            note = f"Order Vol = {order_vol_m3:.2f} m3"

        results.append(
            PavementLayerQuantityResult(
                layer_order=lyr.layer_order,
                layer_name=lyr.layer_name,
                layer_category=lyr.layer_category,
                thickness_cm=lyr.thickness_cm,
                lateral_step_out_m=lyr.lateral_step_out_m,
                net_plan_area_m2=round(net_surface_area_m2, 3),
                step_out_extra_area_m2=step_extra_m2,
                effective_layer_area_m2=eff_area_m2,
                geometric_compacted_volume_m3=geom_vol_m3,
                compaction_factor=lyr.compaction_factor,
                waste_or_overlap_factor=lyr.waste_or_overlap_factor,
                order_area_m2=order_area_m2,
                order_volume_m3=order_vol_m3,
                order_tonnage_t=order_tons,
                primary_order_unit=prim_unit,
                primary_order_quantity=prim_qty,
                engineering_formula_note=note,
            )
        )

    return PavementCalculationReport(
        preset_used=used_preset,
        input_net_surface_area_m2=round(net_surface_area_m2, 3),
        input_active_edge_perimeter_m=round(active_edge_perimeter_m, 3),
        total_structure_thickness_cm=round(total_thickness_cm, 2),
        total_subgrade_excavation_volume_m3=round(total_subgrade_exc_m3, 2),
        layers=results,
    )
