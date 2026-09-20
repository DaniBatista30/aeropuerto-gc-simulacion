#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
===============================================================================
 LPA-EXPERIMENT — Diseño de experimentos para el modelo LPA-DES
===============================================================================

 Este módulo es el que produce resultados PRESENTABLES. El modelo
 (`lpa_des_model.py`) genera una realización de un proceso estocástico; una
 realización aislada no es una predicción, es una anécdota. Aquí se construye
 el aparato estadístico que convierte corridas en estimaciones con error
 acotado.

 Metodología
 -----------
 1. SIMULACIÓN TERMINANTE. La hora punta de una terminal tiene inicio y fin
    definidos (sistema vacío -> ola de demanda -> sistema vacío). Es por tanto
    una simulación TERMINANTE, y el método correcto es RÉPLICAS INDEPENDIENTES.
    No procede truncar un periodo de calentamiento (warm-up) ni aplicar medias
    por lotes (batch means): esas técnicas son para estimar el régimen
    estacionario de un sistema que nunca se vacía, y aplicarlas aquí sesgaría
    precisamente los picos que se quieren medir.

 2. INTERVALOS DE CONFIANZA. Para cada KPI se estima la media sobre n réplicas
    y su semi-amplitud:

        h = t_{1-alpha/2, n-1} * s / sqrt(n)

    Se usa la t de Student (no la normal) porque n es pequeño y sigma es
    desconocida. La precisión relativa h/|media| es el criterio de parada.

 3. RÉPLICAS SECUENCIALES. En lugar de fijar n a ojo, se añaden réplicas hasta
    alcanzar una precisión relativa objetivo (por defecto 5%) sobre el KPI de
    control. Es el procedimiento recomendado por Law & Kelton y el que permite
    defender ante un revisor cuántas corridas hacen falta y por qué.

 4. NÚMEROS ALEATORIOS COMUNES (CRN). Al comparar dos escenarios, la réplica i
    de ambos usa la MISMA semilla maestra. Como cada fuente estocástica del
    modelo consume su propio flujo independiente (`RandomStreams`), los dos
    escenarios ven esencialmente la misma secuencia de pasajeros y de tiempos
    de servicio. Esto induce correlación positiva entre escenarios y reduce la
    varianza de la DIFERENCIA, que es la magnitud de interés. El contraste es
    entonces una t pareada sobre las diferencias por réplica.

 Advertencia: CRN reduce la varianza de la diferencia pero NO garantiza
 sincronización perfecta; si los escenarios divergen mucho en número de
 eventos, la correlación se degrada. El código reporta la correlación empírica
 para que se pueda comprobar si la técnica está funcionando.

 Uso:
     $ python lpa_experiment.py                  # base vs obras, 30 réplicas
     $ python lpa_experiment.py --reps 60
     $ python lpa_experiment.py --sequential --target-precision 0.05
     $ python lpa_experiment.py --csv-prefix resultados
===============================================================================
"""

from __future__ import annotations

import argparse
import logging
import time
from dataclasses import dataclass
from typing import Callable, Final, Optional, Sequence

import numpy as np
import pandas as pd
from scipy import stats

from lpa_des_model import (
    SECONDS_PER_MINUTE,
    AirportTerminalModel,
    FruinQueueStandards,
    IATALoSStandards,
    PaxType,
    ProcessStage,
    RandomStreams,
    TerminalConfig,
    ZoneKey,
    configure_logging,
)

LOGGER: Final[logging.Logger] = logging.getLogger("LPA-EXP")

# KPI usado por defecto como criterio de parada secuencial.
DEFAULT_CONTROL_KPI: Final[str] = "security_P95_min"


# =============================================================================
# 1. EXTRACCIÓN DE KPIs DE UNA RÉPLICA
# =============================================================================
def extract_kpis(model: AirportTerminalModel) -> dict[str, float]:
    """
    Reduce una réplica completa a un vector de KPIs escalares.

    Cada KPI es una observación independiente entre réplicas, que es lo que
    legitima aplicar la t de Student sobre ellas. NO se deben promediar
    percentiles calculados dentro de una réplica con los de otra sin más: lo
    que se estima es "el P95 esperado de una hora punta", y su incertidumbre
    es la dispersión de ese P95 entre réplicas.
    """
    df = model.passengers_dataframe()
    los_df = model.los_dataframe()
    cfg = model.config

    if df.empty:
        raise RuntimeError("Réplica sin pasajeros procesados.")

    kpis: dict[str, float] = {}

    # --- Esperas por proceso ---------------------------------------------
    stage_columns = {
        "checkin": ("wait_check_in_min", df["uses_check_in"]),
        "security": ("wait_security_min", pd.Series(True, index=df.index)),
        "passport": ("wait_passport_min", df["pax_type"] == PaxType.NON_SCHENGEN.value),
    }
    for tag, (column, mask) in stage_columns.items():
        series = df.loc[mask, column].astype(float)
        if series.empty:
            kpis[f"{tag}_mean_min"] = float("nan")
            kpis[f"{tag}_P95_min"] = float("nan")
            continue
        kpis[f"{tag}_mean_min"] = float(series.mean())
        kpis[f"{tag}_P95_min"] = float(np.percentile(series, 95))

    # --- Tiempo total en sistema ------------------------------------------
    dwell = df["dwell_time_min"].astype(float)
    kpis["dwell_mean_min"] = float(dwell.mean())
    kpis["dwell_P95_min"] = float(np.percentile(dwell, 95))

    # --- Bloqueo físico (spill-back) --------------------------------------
    kpis["blocked_mean_min"] = float(df["blocked_min"].astype(float).mean())
    kpis["pax_bloqueados_pct"] = float(100.0 * (df["blocked_min"] > 1e-9).mean())

    # --- Caudal -----------------------------------------------------------
    horizon_min = max(model.env.now / SECONDS_PER_MINUTE, 1e-9)
    kpis["throughput_pax_h"] = float(len(df) / horizon_min * 60.0)
    kpis["horizonte_min"] = float(horizon_min)

    # --- Densidad por subzona ---------------------------------------------
    for key in model.zones:
        dens = los_df[f"m2pax_{key.value}"].astype(float)
        occ = los_df[f"occ_{key.value}"].astype(float)
        active = dens[occ > 0]
        kpis[f"m2pax_min_{key.name}"] = (
            float(active.min()) if not active.empty else float(dens.max()))
        kpis[f"pct_crowded_{key.name}"] = float(
            100.0 * los_df[f"crowded_{key.value}"].mean())
        kpis[f"ocup_pico_{key.name}"] = float(model.zones[key].peak_occupancy)

    # --- Incumplimiento de LoS del ADRM (fracción de pasajeros) ------------
    kpis["pct_LoS_suboptimo_security"] = float(
        100.0 * (df["los_security"] == "Sub-optimum").mean())
    kpis["pct_LoS_suboptimo_checkin"] = float(
        100.0 * (df.loc[df["uses_check_in"], "los_check_in"] == "Sub-optimum").mean()
        if df["uses_check_in"].any() else 0.0)

    del cfg  # disponible para KPIs derivados adicionales
    return kpis


# =============================================================================
# 2. MOTOR DE RÉPLICAS
# =============================================================================
@dataclass(frozen=True)
class ReplicationPlan:
    """Parámetros del diseño experimental."""

    n_replications: int = 30
    base_seed: int = 20260920
    monitor_step_min: float = 1.0
    alpha: float = 0.05           # 1 - nivel de confianza

    def seed_for(self, replication_index: int) -> int:
        """
        Semilla maestra de la réplica i.

        Determinista y reproducible: el mismo plan produce exactamente los
        mismos resultados, requisito para que un tercero pueda auditar el
        estudio. Al usarse la MISMA semilla para todos los escenarios, se
        aplican Números Aleatorios Comunes.
        """
        return self.base_seed + 1000 * replication_index


def run_replications(
    config: TerminalConfig,
    plan: ReplicationPlan,
    start_index: int = 0,
    progress: bool = True,
) -> pd.DataFrame:
    """
    Ejecuta `plan.n_replications` corridas independientes del mismo escenario.

    Devuelve un DataFrame con una fila por réplica y una columna por KPI.
    """
    rows: list[dict[str, float]] = []
    t_start = time.perf_counter()

    for i in range(start_index, start_index + plan.n_replications):
        seed = plan.seed_for(i)
        model = AirportTerminalModel(config, streams=RandomStreams(seed))
        model.run(monitor_step=plan.monitor_step_min)
        kpis = extract_kpis(model)
        kpis["replica"] = float(i)
        kpis["semilla"] = float(seed)
        rows.append(kpis)

        if progress and (i - start_index + 1) % 10 == 0:
            LOGGER.info("  ... %d réplicas completadas (%.1f s)",
                        i - start_index + 1, time.perf_counter() - t_start)

    df = pd.DataFrame(rows)
    LOGGER.info("Escenario '%s': %d réplicas en %.1f s",
                config.scenario_name, len(df), time.perf_counter() - t_start)
    return df


# =============================================================================
# 3. INFERENCIA: INTERVALOS DE CONFIANZA
# =============================================================================
def confidence_intervals(reps_df: pd.DataFrame, alpha: float = 0.05,
                         kpis: Optional[Sequence[str]] = None) -> pd.DataFrame:
    """
    Intervalo de confianza de la media de cada KPI sobre las réplicas.

        h = t_{1-alpha/2, n-1} * s / sqrt(n)

    `precision_rel` = h / |media| es la métrica que decide si hacen falta más
    réplicas. Un valor > 0.10 significa que la estimación no es lo bastante
    fina para sostener una conclusión de dimensionamiento.
    """
    ignore = {"replica", "semilla"}
    columns = list(kpis) if kpis else [c for c in reps_df.columns if c not in ignore]

    n = len(reps_df)
    if n < 2:
        raise ValueError("Se necesitan al menos 2 réplicas para un IC.")
    t_crit = float(stats.t.ppf(1.0 - alpha / 2.0, df=n - 1))

    rows: list[dict[str, object]] = []
    for col in columns:
        values = reps_df[col].astype(float).to_numpy()
        values = values[np.isfinite(values)]
        if values.size < 2:
            continue
        mean = float(values.mean())
        sd = float(values.std(ddof=1))
        half = t_crit * sd / np.sqrt(values.size)
        rows.append({
            "KPI": col,
            "n": int(values.size),
            "media": round(mean, 3),
            "desv_tip": round(sd, 3),
            "IC_inf": round(mean - half, 3),
            "IC_sup": round(mean + half, 3),
            "semi_amplitud": round(float(half), 3),
            "precision_rel": round(float(half / abs(mean)), 4) if abs(mean) > 1e-12 else np.nan,
        })
    return pd.DataFrame(rows)


def sequential_replications(
    config: TerminalConfig,
    plan: ReplicationPlan,
    control_kpi: str = DEFAULT_CONTROL_KPI,
    target_precision: float = 0.05,
    min_reps: int = 10,
    max_reps: int = 200,
    batch: int = 10,
) -> tuple[pd.DataFrame, bool]:
    """
    Añade réplicas por lotes hasta alcanzar la precisión relativa objetivo.

    Devuelve (réplicas, alcanzado). Si se agota `max_reps` sin converger, el
    estudio debe reportarlo explícitamente: significa que la varianza del
    sistema es alta y que cualquier conclusión fina sobre ese KPI es frágil.
    """
    reps = run_replications(config, ReplicationPlan(
        n_replications=min_reps, base_seed=plan.base_seed,
        monitor_step_min=plan.monitor_step_min, alpha=plan.alpha))

    while True:
        ci = confidence_intervals(reps, alpha=plan.alpha, kpis=[control_kpi])
        if ci.empty:
            raise ValueError(f"KPI de control desconocido: {control_kpi}")
        precision = float(ci.iloc[0]["precision_rel"])
        LOGGER.info("n=%d | %s = %.3f | precision relativa = %.1f%% (objetivo %.1f%%)",
                    len(reps), control_kpi, float(ci.iloc[0]["media"]),
                    100 * precision, 100 * target_precision)

        if precision <= target_precision:
            return reps, True
        if len(reps) >= max_reps:
            LOGGER.warning("Alcanzado max_reps=%d sin converger (precision %.1f%%). "
                           "Reportar esta limitación en el informe.",
                           max_reps, 100 * precision)
            return reps, False

        extra = run_replications(config, ReplicationPlan(
            n_replications=batch, base_seed=plan.base_seed,
            monitor_step_min=plan.monitor_step_min, alpha=plan.alpha),
            start_index=len(reps), progress=False)
        reps = pd.concat([reps, extra], ignore_index=True)


# =============================================================================
# 4. COMPARACIÓN PAREADA DE ESCENARIOS (CRN)
# =============================================================================
def compare_scenarios(
    reps_a: pd.DataFrame,
    reps_b: pd.DataFrame,
    label_a: str = "A",
    label_b: str = "B",
    alpha: float = 0.05,
    kpis: Optional[Sequence[str]] = None,
) -> pd.DataFrame:
    """
    Contraste t pareado sobre las diferencias por réplica (B - A).

    Requiere que ambas tandas se hayan generado con el MISMO plan de semillas
    (CRN). El emparejamiento es por índice de réplica.

    Columnas devueltas:
      * `delta_medio`    : estimación puntual del efecto del escenario B.
      * `IC_inf/IC_sup`  : intervalo de confianza de la diferencia.
      * `significativo`  : el IC excluye el cero al nivel alpha.
      * `correlacion`    : correlación empírica entre escenarios; si es
                           claramente positiva, CRN está reduciendo varianza.
      * `reduccion_var`  : reducción de varianza lograda frente a comparar
                           las dos tandas como independientes.
    """
    if len(reps_a) != len(reps_b):
        raise ValueError("CRN requiere el mismo número de réplicas en ambos escenarios.")

    ignore = {"replica", "semilla"}
    columns = list(kpis) if kpis else [
        c for c in reps_a.columns if c not in ignore and c in reps_b.columns]

    n = len(reps_a)
    t_crit = float(stats.t.ppf(1.0 - alpha / 2.0, df=n - 1))

    rows: list[dict[str, object]] = []
    for col in columns:
        a = reps_a[col].astype(float).to_numpy()
        b = reps_b[col].astype(float).to_numpy()
        ok = np.isfinite(a) & np.isfinite(b)
        a, b = a[ok], b[ok]
        if a.size < 2:
            continue

        d = b - a
        mean_d = float(d.mean())
        sd_d = float(d.std(ddof=1))
        half = t_crit * sd_d / np.sqrt(d.size)

        var_indep = float(a.var(ddof=1) + b.var(ddof=1))
        var_paired = float(d.var(ddof=1))
        corr = (float(np.corrcoef(a, b)[0, 1])
                if a.std() > 1e-12 and b.std() > 1e-12 else np.nan)

        rows.append({
            "KPI": col,
            f"media_{label_a}": round(float(a.mean()), 3),
            f"media_{label_b}": round(float(b.mean()), 3),
            "delta_medio": round(mean_d, 3),
            "IC_inf": round(mean_d - half, 3),
            "IC_sup": round(mean_d + half, 3),
            "significativo": bool(abs(mean_d) > half),
            "correlacion": round(corr, 3) if np.isfinite(corr) else np.nan,
            "reduccion_var_pct": (round(100 * (1 - var_paired / var_indep), 1)
                                  if var_indep > 1e-12 else np.nan),
        })
    return pd.DataFrame(rows)


# =============================================================================
# 5. ESCENARIOS DE ESTUDIO
# =============================================================================
def scenario_baseline(n_pax: int, rate: float) -> TerminalConfig:
    """
    Hora punta de salidas en configuración actual.

    Dimensionado para que el filtro opere en torno al 90-95% de utilización,
    que es donde las colas se vuelven sensibles a cualquier perturbación. Un
    sistema holgado no informa sobre el impacto de unas obras.
    """
    return TerminalConfig(
        scenario_name="ACTUAL — hora punta, configuración completa",
        n_check_in_desks=48,
        n_security_lanes=10,
        n_abc_gates=4,
        n_manual_booths=3,
        arrival_rate_pax_h=rate,
        n_passengers=n_pax,
    )


def scenario_works(n_pax: int, rate: float) -> TerminalConfig:
    """
    Misma demanda, fase de obras: tres líneas fuera de servicio y clausura
    parcial del serpentín de encolamiento y del banco de recomposición.

    La clausura se aplica POR ZONA porque así es como se ejecuta una obra real:
    se acota un pasillo, no "el filtro" en abstracto.
    """
    base = scenario_baseline(n_pax, rate)
    works = base.with_closure({
        ZoneKey.QUEUE: 0.40,
        ZoneKey.RECOMPOSE: 0.35,
    })
    from dataclasses import replace as _replace
    return _replace(
        works,
        scenario_name="OBRAS — 7 líneas operativas, serpentín al 60%",
        n_security_lanes=7,
    )


# =============================================================================
# 6. INFORME
# =============================================================================
HEADLINE_KPIS: Final[tuple[str, ...]] = (
    "security_mean_min", "security_P95_min",
    "checkin_P95_min", "passport_P95_min",
    "dwell_P95_min", "throughput_pax_h",
    "pax_bloqueados_pct", "pct_LoS_suboptimo_security",
    "m2pax_min_QUEUE", "pct_crowded_QUEUE", "ocup_pico_QUEUE",
    "m2pax_min_RECOMPOSE", "pct_crowded_RECOMPOSE",
)


def print_ci_report(title: str, ci_df: pd.DataFrame, n_reps: int, alpha: float) -> None:
    print("\n" + "=" * 100)
    print(f"{title}   |   {n_reps} réplicas   |   IC al {100*(1-alpha):.0f}%")
    print("=" * 100)
    subset = ci_df[ci_df["KPI"].isin(HEADLINE_KPIS)].copy()
    subset["KPI"] = pd.Categorical(subset["KPI"], categories=HEADLINE_KPIS, ordered=True)
    print(subset.sort_values("KPI").to_string(index=False))


def print_comparison_report(cmp_df: pd.DataFrame, label_a: str, label_b: str,
                            alpha: float) -> None:
    print("\n" + "=" * 100)
    print(f"IMPACTO DE LAS OBRAS — diferencia pareada ({label_b} menos {label_a}), "
          f"CRN, IC al {100*(1-alpha):.0f}%")
    print("=" * 100)
    subset = cmp_df[cmp_df["KPI"].isin(HEADLINE_KPIS)].copy()
    subset["KPI"] = pd.Categorical(subset["KPI"], categories=HEADLINE_KPIS, ordered=True)
    print(subset.sort_values("KPI").to_string(index=False))
    print("\nLectura: 'significativo' = el intervalo de confianza de la diferencia "
          "excluye el cero.\n'correlacion' alta confirma que los Números Aleatorios "
          "Comunes están funcionando.")


def print_los_verdict(ci_df: pd.DataFrame, los: IATALoSStandards,
                      fruin: FruinQueueStandards, scenario: str) -> None:
    """Traduce los intervalos de confianza a un veredicto normativo."""
    print(f"\n--- Veredicto normativo: {scenario} ---")

    def get(kpi: str, field: str) -> float:
        row = ci_df[ci_df["KPI"] == kpi]
        return float(row.iloc[0][field]) if not row.empty else float("nan")

    p95_sec = get("security_P95_min", "media")
    p95_sec_hi = get("security_P95_min", "IC_sup")
    grade = los.grade_queue_time(ProcessStage.SECURITY, p95_sec)
    print(f"  Seguridad P95 = {p95_sec:.2f} min (IC sup {p95_sec_hi:.2f}) "
          f"-> LoS ADRM: {grade.value}")
    if los.grade_queue_time(ProcessStage.SECURITY, p95_sec_hi) != grade:
        print("    AVISO: el límite superior del IC cae en otra clase de LoS. "
              "La clasificación NO es estadísticamente concluyente.")

    dens = get("m2pax_min_QUEUE", "media")
    print(f"  Densidad mínima en encolamiento = {dens:.2f} m2/pax "
          f"-> Fruin {fruin.grade(dens).value} | "
          f"IATA {'INCUMPLE' if dens < los.space_optimum_m2_pax else 'cumple'} "
          f"({los.space_optimum_m2_pax} m2/pax)")

    if dens <= los.space_crush_m2_pax + 1e-9:
        print(f"    ATENCIÓN: la zona está en su AFORO FÍSICO "
              f"({los.space_crush_m2_pax} m2/pax). La densidad no puede subir más: "
              f"el exceso de demanda se manifiesta como spill-back aguas arriba, "
              f"no como mayor densidad. NO interpretar este valor como 'el peor "
              f"caso'; es el tope estructural del modelo.")

    blocked = get("pax_bloqueados_pct", "media")
    if blocked > 0.5:
        print(f"  Spill-back: {blocked:.1f}% de los pasajeros sufren bloqueo por "
              f"aforo físico. Hay saturación estructural, no sólo colas.")


# =============================================================================
# 7. CLI
# =============================================================================
def build_cli() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(
        description="Diseño de experimentos para el modelo LPA-DES.")
    p.add_argument("--reps", type=int, default=30, help="Réplicas por escenario.")
    p.add_argument("--n-pax", type=int, default=1200, help="Pasajeros por réplica.")
    p.add_argument("--rate", type=float, default=1800.0, help="Demanda (pax/h).")
    p.add_argument("--alpha", type=float, default=0.05, help="1 - nivel de confianza.")
    p.add_argument("--seed", type=int, default=20260920)
    p.add_argument("--step", type=float, default=1.0, help="Paso del monitor (min).")
    p.add_argument("--sequential", action="store_true",
                   help="Réplicas secuenciales hasta la precisión objetivo.")
    p.add_argument("--target-precision", type=float, default=0.05,
                   help="Precisión relativa objetivo del KPI de control.")
    p.add_argument("--control-kpi", type=str, default=DEFAULT_CONTROL_KPI)
    p.add_argument("--csv-prefix", type=str, default="")
    p.add_argument("--quiet", action="store_true",
                   help="Silencia los WARNING de over-crowding de cada réplica.")
    return p


def main(argv: Optional[Sequence[str]] = None) -> None:
    args, _ = build_cli().parse_known_args(argv)
    configure_logging(logging.ERROR if args.quiet else logging.INFO)
    logging.getLogger("LPA-DES").setLevel(logging.ERROR)  # el ruido por réplica no aporta
    LOGGER.setLevel(logging.INFO)

    pd.set_option("display.width", 240)
    pd.set_option("display.max_columns", 60)

    plan = ReplicationPlan(n_replications=args.reps, base_seed=args.seed,
                           monitor_step_min=args.step, alpha=args.alpha)

    cfg_base = scenario_baseline(args.n_pax, args.rate)
    cfg_works = scenario_works(args.n_pax, args.rate)

    print("=" * 100)
    print("ESTUDIO DE CAPACIDAD — Aeropuerto de Gran Canaria (LPA), Lado Tierra")
    print("=" * 100)
    print(f"Demanda por réplica : {args.n_pax} pax a {args.rate:.0f} pax/h")
    print(f"Caudal teórico      : ACTUAL {cfg_base.theoretical_lane_throughput_pax_h * cfg_base.n_security_lanes:.0f} pax/h"
          f"  |  OBRAS {cfg_works.theoretical_lane_throughput_pax_h * cfg_works.n_security_lanes:.0f} pax/h")
    print(f"Método              : simulación terminante, réplicas independientes, "
          f"CRN para la comparación")

    # --- Escenario actual -------------------------------------------------
    LOGGER.info("Ejecutando escenario ACTUAL...")
    if args.sequential:
        reps_base, converged = sequential_replications(
            cfg_base, plan, control_kpi=args.control_kpi,
            target_precision=args.target_precision)
        n_final = len(reps_base)
        plan = ReplicationPlan(n_replications=n_final, base_seed=args.seed,
                               monitor_step_min=args.step, alpha=args.alpha)
        if not converged:
            print("\n[!] El escenario ACTUAL no alcanzó la precisión objetivo.")
    else:
        reps_base = run_replications(cfg_base, plan)

    # --- Escenario obras (mismas semillas => CRN) -------------------------
    LOGGER.info("Ejecutando escenario OBRAS con las mismas semillas (CRN)...")
    reps_works = run_replications(cfg_works, plan)

    ci_base = confidence_intervals(reps_base, alpha=args.alpha)
    ci_works = confidence_intervals(reps_works, alpha=args.alpha)
    comparison = compare_scenarios(reps_base, reps_works,
                                   label_a="ACTUAL", label_b="OBRAS",
                                   alpha=args.alpha)

    print_ci_report("ESCENARIO ACTUAL", ci_base, len(reps_base), args.alpha)
    print_los_verdict(ci_base, cfg_base.los, cfg_base.fruin, "ACTUAL")

    print_ci_report("ESCENARIO OBRAS", ci_works, len(reps_works), args.alpha)
    print_los_verdict(ci_works, cfg_works.los, cfg_works.fruin, "OBRAS")

    print_comparison_report(comparison, "ACTUAL", "OBRAS", args.alpha)

    if args.csv_prefix:
        reps_base.to_csv(f"{args.csv_prefix}_replicas_actual.csv", index=False)
        reps_works.to_csv(f"{args.csv_prefix}_replicas_obras.csv", index=False)
        ci_base.to_csv(f"{args.csv_prefix}_ic_actual.csv", index=False)
        ci_works.to_csv(f"{args.csv_prefix}_ic_obras.csv", index=False)
        comparison.to_csv(f"{args.csv_prefix}_comparacion.csv", index=False)
        print(f"\n[OK] Resultados exportados con prefijo '{args.csv_prefix}'.")


if __name__ == "__main__":
    main()
