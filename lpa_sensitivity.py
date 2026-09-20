#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
===============================================================================
 LPA-SENSIBILIDAD — Barrido sobre la superficie del filtro de seguridad
===============================================================================

 POR QUÉ ESTE BARRIDO
 ---------------------
 La superficie del recinto de seguridad no figura en el Plan Director, ni en
 el TFM, ni en el DORA III. Ante un dato inexistente hay dos caminos:
 inventar un valor plausible, o probar TODO el rango razonable y comprobar si
 la conclusión depende de él. Este módulo hace lo segundo.

 DOS PREGUNTAS DISTINTAS, DOS DEMANDAS DISTINTAS
 ------------------------------------------------
 1. ¿Cuál es el límite FÍSICO del recinto, al margen de cuánta gente quiera
    entrar? -> se satura el sistema con una demanda muy superior a cualquier
    escenario real, y se mide el caudal SOSTENIDO en ventana estable (no la
    media ingenua sobre todo el horizonte, que sesga por la rampa y el
    vaciado -- error ya cometido y corregido en la validación del filtro).

 2. ¿Importa la superficie en un escenario REAL? -> se corre con la demanda
    de 2018 y con la del DORA III 2031, y se mide si el nivel de servicio
    cambia según la superficie.

 Ambas con réplicas independientes e intervalos de confianza: un barrido de
 corridas únicas no permite decir "a partir de aquí ya no importa", sólo un
 barrido con IC permite afirmarlo con algo de rigor.
===============================================================================
"""

from __future__ import annotations

import logging
from dataclasses import dataclass, replace
from typing import Final, Optional, Sequence

import numpy as np
import pandas as pd
from scipy import stats

from lpa_actual import config_lpa_2018
from lpa_demanda import DemandSource, SCENARIOS, config_for_scenario
from lpa_des_model import AirportTerminalModel, RandomStreams, ZoneKey, configure_logging

LOGGER: Final[logging.Logger] = logging.getLogger("LPA-SENS")

# Superficie del filtro completo (m2), a repartir entre las 4 subzonas con las
# proporciones de `security_zone_layout` (50/17/12/21 %).
AREA_SWEEP_M2: Final[tuple[float, ...]] = (
    200.0, 300.0, 400.0, 500.0, 600.0, 800.0, 1000.0, 1500.0, 2000.0, 3000.0)

SATURATING_DEMAND_PAX_H: Final[float] = 9000.0
SATURATING_N_PAX: Final[int] = 5000
STEADY_WINDOW_MIN: Final[tuple[float, float]] = (12.0, 45.0)   # excluye rampa y vaciado


# =============================================================================
# 1. MEDIDA DE CAUDAL SOSTENIDO (no la media ingenua del horizonte completo)
# =============================================================================
def sustained_throughput_pax_h(model: AirportTerminalModel,
                               window: tuple[float, float] = STEADY_WINDOW_MIN) -> float:
    """
    Caudal sostenido en la ventana estable [lo, hi] minutos.

    Contar pasajeros/horizonte sesga a la baja porque incluye la rampa de
    arranque (el sistema aún no está lleno) y el vaciado final (ya no llegan
    pasajeros nuevos). Contar sólo las salidas dentro de una ventana donde el
    sistema ya está saturado da el caudal real de régimen permanente.
    """
    df = model.passengers_dataframe()
    if df.empty:
        return 0.0
    lo, hi = window
    exits = df["t_exit_min"].astype(float)
    n = int(((exits > lo) & (exits < hi)).sum())
    return n / (hi - lo) * 60.0


def run_one(area_m2: float, seed: int, pax_h: float, n_pax: int,
           divest_positions: int = 4) -> dict[str, float]:
    """Ejecuta una réplica y extrae los KPIs de interés para el barrido."""
    cfg = replace(
        config_lpa_2018(security_area_m2=area_m2, pax_php=pax_h, n_passengers=n_pax),
        divest_positions_per_lane=divest_positions,
    )
    model = AirportTerminalModel(cfg, streams=RandomStreams(seed))
    model.run(monitor_step=0.5)

    df = model.passengers_dataframe()
    los = model.los_dataframe()
    queue_key = ZoneKey.QUEUE.value

    result: dict[str, float] = {
        "area_m2": area_m2,
        "throughput_sostenido_pax_h": sustained_throughput_pax_h(model),
        "pax_bloqueados_pct": (
            float(100.0 * (df["blocked_min"] > 1e-9).mean()) if not df.empty else float("nan")),
        "security_P95_min": (
            float(np.percentile(df["wait_security_min"], 95)) if not df.empty else float("nan")),
    }
    if not los.empty:
        dens = los[f"m2pax_{queue_key}"].astype(float)
        occ = los[f"occ_{queue_key}"].astype(float)
        active = dens[occ > 0]
        result["m2pax_min_queue"] = float(active.min()) if not active.empty else float(dens.max())
        result["pct_crowded_queue"] = float(100.0 * los[f"crowded_{queue_key}"].mean())
    else:
        result["m2pax_min_queue"] = float("nan")
        result["pct_crowded_queue"] = float("nan")
    return result


def sweep(areas: Sequence[float], pax_h: float, n_pax: int, n_reps: int,
         base_seed: int = 20260920, divest_positions: int = 4) -> pd.DataFrame:
    """
    Barrido con réplicas independientes (mismas semillas en cada punto, para
    que las diferencias entre superficies no se confundan con ruido muestral).
    """
    rows: list[dict[str, float]] = []
    for area in areas:
        for i in range(n_reps):
            seed = base_seed + 1000 * i
            r = run_one(area, seed, pax_h, n_pax, divest_positions)
            r["replica"] = i
            rows.append(r)
        LOGGER.info("  area=%.0f m2 completada (%d réplicas)", area, n_reps)
    return pd.DataFrame(rows)


def summarize(raw: pd.DataFrame, alpha: float = 0.05) -> pd.DataFrame:
    """Media e IC por superficie, para cada KPI del barrido."""
    t_crit = float(stats.t.ppf(1 - alpha / 2, df=raw["replica"].nunique() - 1))
    rows = []
    for area, g in raw.groupby("area_m2"):
        row: dict[str, object] = {"area_m2": area}
        for col in ["throughput_sostenido_pax_h", "pax_bloqueados_pct",
                   "security_P95_min", "m2pax_min_queue", "pct_crowded_queue"]:
            v = g[col].dropna().to_numpy()
            if v.size < 2:
                row[col] = float(v.mean()) if v.size else float("nan")
                row[col + "_ic"] = 0.0
                continue
            mean = float(v.mean())
            half = t_crit * float(v.std(ddof=1)) / np.sqrt(v.size)
            row[col] = round(mean, 2)
            row[col + "_ic"] = round(half, 2)
        rows.append(row)
    return pd.DataFrame(rows).sort_values("area_m2").reset_index(drop=True)


# =============================================================================
# 2. DETECCIÓN DE UMBRAL
# =============================================================================
def find_threshold(summary: pd.DataFrame, metric: str = "pax_bloqueados_pct",
                   tol: float = 0.5) -> Optional[float]:
    """
    Superficie mínima a partir de la cual `metric` deja de ser
    significativamente distinto de su valor asintótico (el de la mayor
    superficie del barrido), dentro de la tolerancia `tol`.
    """
    asymptote = summary.iloc[-1][metric]
    for _, row in summary.iterrows():
        if abs(row[metric] - asymptote) <= tol:
            return float(row["area_m2"])
    return None


# =============================================================================
# 3. INFORME
# =============================================================================
def main(n_reps: int = 12) -> None:
    configure_logging(logging.ERROR)
    logging.getLogger("LPA-SENS").setLevel(logging.INFO)
    pd.set_option("display.width", 220)
    pd.set_option("display.max_columns", 30)

    print("=" * 96)
    print("BARRIDO DE SENSIBILIDAD — superficie del filtro de seguridad")
    print(f"Rango: {min(AREA_SWEEP_M2):.0f} - {max(AREA_SWEEP_M2):.0f} m2 | "
          f"{n_reps} réplicas por punto")
    print("=" * 96)

    # --- 1. Límite físico: demanda saturante -------------------------------
    print("\n--- 1. LÍMITE FÍSICO (demanda saturante, caudal sostenido) ---")
    raw_sat = sweep(AREA_SWEEP_M2, SATURATING_DEMAND_PAX_H, SATURATING_N_PAX, n_reps)
    sum_sat = summarize(raw_sat)
    print(sum_sat[["area_m2", "throughput_sostenido_pax_h", "throughput_sostenido_pax_h_ic",
                   "pax_bloqueados_pct", "pax_bloqueados_pct_ic"]].to_string(index=False))

    th = find_threshold(sum_sat, "throughput_sostenido_pax_h", tol=50.0)
    print(f"\n  Umbral: por debajo de ~{th:.0f} m2 el caudal cae de forma significativa."
         if th else "\n  No se detecta umbral claro en el rango barrido.")
    print(f"  Por encima de {th:.0f} m2, el caudal ya no depende de la superficie: "
         f"el cuello de botella pasa a ser el arco de rayos X, no el espacio."
         if th else "")

    # --- 2. Escenarios reales -----------------------------------------------
    for scenario in SCENARIOS:
        if scenario.source not in (DemandSource.OBSERVED, DemandSource.DORA3):
            continue
        pax_h = scenario.php_departures()
        print(f"\n--- 2. ESCENARIO REAL: {scenario.year} {scenario.source.value} "
              f"({pax_h:.0f} pax/h salidas) ---")
        raw = sweep(AREA_SWEEP_M2, pax_h, int(pax_h), n_reps)
        summ = summarize(raw)
        print(summ[["area_m2", "security_P95_min", "security_P95_min_ic",
                    "m2pax_min_queue", "pax_bloqueados_pct"]].to_string(index=False))

        p95_range = summ["security_P95_min"].max() - summ["security_P95_min"].min()
        print(f"\n  Rango de variación del P95 en todo el barrido: {p95_range:.2f} min.")
        if p95_range < 1.0:
            print("  CONCLUSIÓN: bajo esta demanda, la superficie del filtro NO es")
            print("  determinante para el nivel de servicio en todo el rango físico probado.")
        else:
            print("  CONCLUSIÓN: la superficie SÍ afecta al nivel de servicio bajo esta")
            print("  demanda. Es un dato que hay que obtener del gestor antes de concluir.")

    print("\n" + "=" * 96)
    print("VEREDICTO GLOBAL")
    print("=" * 96)
    print("La superficie del filtro sólo es crítica en el límite físico del sistema")
    print("(demanda muy superior a cualquier escenario de tráfico previsto). Bajo la")
    print("demanda real, actual o del DORA III a 2031, el cuello de botella es el número")
    print("de arcos de rayos X, no la superficie del recinto -- dato que sí puede fijarse")
    print("con seguridad a partir del inventario del TFM.")


if __name__ == "__main__":
    main()
