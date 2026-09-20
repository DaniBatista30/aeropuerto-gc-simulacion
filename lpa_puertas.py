#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
===============================================================================
 LPA-PUERTAS — Etapa de verificación de tarjeta de embarque (puertas QR)
===============================================================================

 CONTEXTO
 --------
 Aportado por el usuario, con conocimiento directo de LPA: antes de entrar al
 serpentín de seguridad hay que pasar por unas puertas que escanean el QR de
 la tarjeta de embarque; se abren y dan paso al pasillo en serpentín hasta el
 control. Esta etapa NO estaba en el modelo validado, que empezaba
 directamente en la zona de encolamiento (Z1).

 POR QUÉ ESTO NO ES UN PROBLEMA DE GEOMETRÍA PEATONAL
 -----------------------------------------------------
 Una puerta que escanea un código y se abre es un SERVIDOR con un tiempo de
 servicio, exactamente como un mostrador o un arco: encaja en el mismo modelo
 de colas ya validado como una etapa más, no requiere simular cómo camina la
 gente. El pasillo en serpentín que la sigue ya es, de hecho, la zona Z1 que
 el modelo trata como aforo físico.

 MÉTODO
 ------
 Extensión NO INVASIVA: se subclasifica `AirportTerminalModel` y se sobrescribe
 `passenger_flow` insertando la espera de puerta entre facturación y el
 proceso de seguridad. El resto del modelo (ya validado) no se toca.

 Como el número real de puertas es incierto (el usuario estima "de 4 a 10"),
 se aplica el mismo principio que con la superficie del filtro: en vez de
 fijar un valor, se barre todo el rango con réplicas e IC.

 Tiempo de servicio de la puerta: sin dato de LPA. Se usa un rango de
 industria para lectores de QR/tarjeta de embarque en puertas de acceso
 (2-4 s, aquí 3 s de media) -- HIPÓTESIS, igual que otros parámetros
 declarados como tales en CALIBRACION.md.
===============================================================================
"""

from __future__ import annotations

import logging
from dataclasses import replace
from typing import Final, Optional

import numpy as np
import pandas as pd
import simpy
from scipy import stats

from lpa_actual import config_lpa_2018
from lpa_demanda import DemandSource, SCENARIOS
from lpa_des_model import (
    AirportTerminalModel, GammaService, PassengerRecord, PaxType,
    RandomStreams, ServiceTimeDistribution, SimProcess, TerminalConfig,
    configure_logging,
)

LOGGER: Final[logging.Logger] = logging.getLogger("LPA-GATES")

GATE_SERVICE_DEFAULT: Final[ServiceTimeDistribution] = GammaService(mean_s=3.0, cv=0.30)
GATE_COUNT_SWEEP: Final[tuple[int, ...]] = (3, 4, 5, 6, 7, 8, 9, 10, 12)


class GatedTerminalModel(AirportTerminalModel):
    """
    Modelo validado + puertas de verificación de tarjeta de embarque antes
    del serpentín de seguridad. Único cambio: una etapa adicional entre
    facturación y `_security_process`; todo lo demás es la clase base.
    """

    def __init__(self, config: TerminalConfig, n_gates: int,
                 gate_dist: ServiceTimeDistribution = GATE_SERVICE_DEFAULT,
                 env: Optional[simpy.Environment] = None,
                 streams: Optional[RandomStreams] = None) -> None:
        super().__init__(config, env=env, streams=streams)
        self.n_gates: Final[int] = n_gates
        self.gate_dist: Final[ServiceTimeDistribution] = gate_dist
        self.gates: Final[simpy.Resource] = simpy.Resource(self.env, capacity=n_gates)
        self.gate_waits_s: list[float] = []

    def passenger_flow(self, env: simpy.Environment, passenger_id: int,
                       is_schengen: bool) -> SimProcess:
        cfg, st = self.config, self.streams
        pax_type = PaxType.SCHENGEN if is_schengen else PaxType.NON_SCHENGEN

        rec = PassengerRecord(
            passenger_id=passenger_id, pax_type=pax_type,
            uses_check_in=bool(st.routing.random() < cfg.p_uses_check_in),
            t_arrival_s=env.now,
        )

        if rec.uses_check_in:
            t0 = env.now
            with self.check_in_desks.request() as req:
                yield req
                rec.wait_check_in_s = env.now - t0
                service = cfg.dist_check_in.sample(st.check_in)
                rec.service_check_in_s = service
                yield env.timeout(service)

        # ---------------------------- PUERTAS QR (nueva etapa) --------------
        t0 = env.now
        with self.gates.request() as req:
            yield req
            self.gate_waits_s.append(env.now - t0)
            yield env.timeout(self.gate_dist.sample(st.routing))

        yield from self._security_process(env, rec)

        if not is_schengen:
            yield from self._passport_control(env, rec)

        rec.t_exit_s = env.now
        self.passengers.append(rec)


def run_one(cfg: TerminalConfig, n_gates: int, seed: int) -> dict[str, float]:
    model = GatedTerminalModel(cfg, n_gates, streams=RandomStreams(seed))
    model.run(monitor_step=0.5)
    df = model.passengers_dataframe()
    gate_wait_min = np.array(model.gate_waits_s) / 60.0
    return {
        "n_gates": n_gates,
        "gate_P95_min": float(np.percentile(gate_wait_min, 95)) if len(gate_wait_min) else float("nan"),
        "gate_mean_min": float(gate_wait_min.mean()) if len(gate_wait_min) else float("nan"),
        "security_P95_min": float(np.percentile(df["wait_security_min"], 95)) if not df.empty else float("nan"),
        "pax_bloqueados_pct": float(100.0 * (df["blocked_min"] > 1e-9).mean()) if not df.empty else float("nan"),
    }


def sweep_gates(cfg: TerminalConfig, gate_counts=GATE_COUNT_SWEEP, n_reps: int = 12,
                base_seed: int = 20260920) -> pd.DataFrame:
    rows = []
    for n in gate_counts:
        vals = [run_one(cfg, n, base_seed + i * 733) for i in range(n_reps)]
        t_crit = float(stats.t.ppf(0.975, df=n_reps - 1))
        def agg(key):
            v = np.array([r[key] for r in vals])
            v = v[np.isfinite(v)]
            if len(v) < 2:
                return float(v.mean()) if len(v) else float("nan"), 0.0
            half = t_crit * v.std(ddof=1) / np.sqrt(len(v))
            return float(v.mean()), float(half)
        gm, gh = agg("gate_mean_min")
        g95, g95h = agg("gate_P95_min")
        sm, sh = agg("security_P95_min")
        bm, bh = agg("pax_bloqueados_pct")
        rows.append({
            "n_gates": n,
            "espera_puerta_media_min": round(gm, 3), "ic": round(gh, 3),
            "espera_puerta_P95_min": round(g95, 3), "ic_p95": round(g95h, 3),
            "seguridad_P95_min": round(sm, 2),
            "capacidad_teorica_pax_h": round(n * 3600 / GATE_SERVICE_DEFAULT.theoretical_mean()),
        })
        LOGGER.info("n_gates=%d completado", n)
    return pd.DataFrame(rows)


def main() -> None:
    configure_logging(logging.ERROR)
    pd.set_option("display.width", 200)

    print("=" * 90)
    print("ETAPA DE PUERTAS QR — barrido de sensibilidad (3 a 12 puertas)")
    print(f"Servicio por puerta: {GATE_SERVICE_DEFAULT.describe()}  [HIPÓTESIS, rango de industria]")
    print("=" * 90)

    for scenario in SCENARIOS:
        if scenario.source not in (DemandSource.OBSERVED, DemandSource.DORA3):
            continue
        pax_h = scenario.php_departures()
        cfg = config_lpa_2018(security_area_m2=1500.0, pax_php=pax_h, n_passengers=int(pax_h))
        print(f"\n--- {scenario.year} {scenario.source.value} ({pax_h:.0f} pax/h salidas) ---")
        df = sweep_gates(cfg, n_reps=12)
        print(df.to_string(index=False))

        # Umbral: primera n donde la espera en puerta deja de ser relevante.
        base = df.iloc[-1]["espera_puerta_media_min"]
        umbral = None
        for _, row in df.iterrows():
            if abs(row["espera_puerta_media_min"] - base) <= 0.05:
                umbral = row["n_gates"]
                break
        if umbral:
            print(f"\n  Umbral: con {umbral} puertas o más, la espera en puerta deja de depender del número.")
        cap4 = df[df.n_gates == 4]["capacidad_teorica_pax_h"].iloc[0] if (df.n_gates == 4).any() else None
        if cap4:
            print(f"  Capacidad teórica con 4 puertas: {cap4:.0f} pax/h "
                 f"(demanda: {pax_h:.0f} pax/h -> ratio {cap4/pax_h:.2f})")


if __name__ == "__main__":
    main()
