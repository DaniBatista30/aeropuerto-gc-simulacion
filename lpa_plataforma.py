#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
===============================================================================
 LPA-PLATAFORMA — DES de la plataforma de estacionamiento (método Horonjeff)
===============================================================================

 FUENTE
 ------
 TFM, Cap. 3.1 "Capacidad de la Plataforma". Datos verificados aritméticamente
 contra el propio TFM antes de escribir este módulo (columna Xi recalculada
 fila a fila; coincide exactamente).

 QUÉ CAMBIA RESPECTO A TODO LO ANTERIOR
 ---------------------------------------
 Hasta ahora el proyecto simulaba PASAJEROS pasando por PROCESOS homogéneos
 (arcos, mostradores). Aquí se simulan AERONAVES ocupando PUESTOS DE
 ESTACIONAMIENTO heterogéneos: hay 9 tipos (0 a VIII, de mayor a menor), y la
 regla física real es que un avión pequeño puede usar un puesto grande, pero
 no al revés. Esa asimetría es la esencia del método de Horonjeff y es lo que
 hace que este módulo no sea una simple reetiquetación del anterior.

 DATOS (TFM, Tablas 3.2 y 3.3, verificados)
 -------------------------------------------
 Tipos ordenados de mayor a menor: 0, I, II, III, IV, V, VI, VII, VIII.
 Un avión de tipo i puede ocupar cualquier puesto de tipo i o MAYOR.

 SIMPLIFICACIÓN DECLARADA: la asignación no es "el puesto más pequeño que
 sirve" (best-fit estricto), sino "el primer puesto compatible que quede
 libre" (todas las categorías compatibles compiten a la vez por la llegada).
 Es una política más realista de lo que parece -- un handler asigna por
 disponibilidad inmediata, no por optimización global -- pero se declara
 como simplificación, no como hecho verificado en LPA.

 Los tiempos de ocupación del TFM son MEDIANAS, no medias; se han usado
 directamente como el parámetro de una Lognormal (cuya mediana es exp(mu)
 independientemente de sigma). La dispersión (sigma) NO está en el TFM: es
 HIPÓTESIS de este modelo.
===============================================================================
"""

from __future__ import annotations

import logging
import math
from dataclasses import dataclass, field
from typing import Final, Optional

import numpy as np
import pandas as pd
import simpy
from scipy import stats

LOGGER: Final[logging.Logger] = logging.getLogger("LPA-PLAT")

# =============================================================================
# 1. DATOS DEL TFM (Tablas 3.1 a 3.4), verificados
# =============================================================================
STAND_TYPES: Final[tuple[str, ...]] = ("0", "I", "II", "III", "IV", "V", "VI", "VII", "VIII")
RANK: Final[dict[str, int]] = {t: i for i, t in enumerate(STAND_TYPES)}  # 0 = más grande

# Tabla 3.2: nº de puestos por tipo, corregido por solapamiento. Total = 56.
STAND_COUNTS: Final[dict[str, int]] = {
    "0": 0, "I": 9, "II": 0, "III": 30, "IV": 2, "V": 1, "VI": 12, "VII": 0, "VIII": 2,
}
TOTAL_STANDS: Final[int] = sum(STAND_COUNTS.values())  # 56

# Tabla 3.3: mezcla de aeronaves (Mi, %) y tiempo de ocupación MEDIANO (horas).
AIRCRAFT_MIX_PCT: Final[dict[str, float]] = {
    "0": 0, "I": 1, "II": 0, "III": 3, "IV": 2, "V": 1, "VI": 51, "VII": 1, "VIII": 41,
}
DWELL_MEDIAN_H: Final[dict[str, float]] = {
    "I": 1.516, "III": 1.339, "IV": 1.291, "V": 1.149, "VI": 1.157, "VII": 1.012, "VIII": 0.953,
}

# Referencias de la propia fuente, para el veredicto de validación.
TFM_THEORETICAL_C_AH: Final[float] = 52.0        # C = Xmin * F, Xmin=1 (verificado)
TFM_PRACTICAL_RANGE_AH: Final[tuple[float, float]] = (52.0, 61.0)  # U=60%/70%, %llegadas=60%
DORA_DECLARED_PLATFORM_AH: Final[float] = 58.0   # DORA 2022-2026, cae dentro del rango TFM


def compatible_types(aircraft_type: str) -> list[str]:
    """Tipos de puesto que puede ocupar un avión de `aircraft_type`: el suyo o mayores."""
    own_rank = RANK[aircraft_type]
    return [t for t in STAND_TYPES if RANK[t] <= own_rank and STAND_COUNTS[t] > 0]


DWELL_SIGMA: Final[float] = 0.35  # HIPÓTESIS: dispersión no publicada en el TFM.


def sample_dwell_s(aircraft_type: str, rng: np.random.Generator) -> float:
    """
    Tiempo de ocupación en SEGUNDOS, Lognormal cuya mediana es la del TFM.

    Para una Lognormal, mediana = exp(mu) con independencia de sigma, así que
    el dato del TFM (mediana) se traslada sin necesidad de estimar una media.
    """
    median_s = DWELL_MEDIAN_H[aircraft_type] * 3600.0
    mu = math.log(median_s)
    return float(stats.lognorm.rvs(s=DWELL_SIGMA, scale=math.exp(mu), random_state=rng))


# =============================================================================
# 2. CONFIGURACIÓN
# =============================================================================
@dataclass(frozen=True)
class PlatformConfig:
    scenario_name: str = "LPA — Plataforma (Horonjeff, TFM)"
    stand_counts: dict[str, int] = field(default_factory=lambda: dict(STAND_COUNTS))
    aircraft_mix_pct: dict[str, float] = field(default_factory=lambda: dict(AIRCRAFT_MIX_PCT))
    dwell_sigma: float = DWELL_SIGMA
    arrival_rate_ah: float = 40.0     # aeronaves/hora, demanda a simular
    peak_duration_min: float = 60.0
    random_seed: int = 20260920

    def __post_init__(self) -> None:
        total = sum(self.stand_counts.values())
        if total <= 0:
            raise ValueError("La plataforma no tiene puestos.")


@dataclass
class AircraftRecord:
    aircraft_id: int
    a_type: str
    t_arrival_s: float
    wait_s: float = 0.0
    dwell_s: float = 0.0
    stand_type_used: str = ""
    t_exit_s: float = 0.0


# =============================================================================
# 3. MODELO
# =============================================================================
class PlatformModel:
    """
    DES de la plataforma: cada puesto es un `simpy.Resource` de capacidad 1
    dentro de un pool por tipo (capacidad = nº de puestos de ese tipo). Un
    avión compite a la vez por todos los pools compatibles (el suyo y los
    mayores) y toma el primero que se libere; los demás intentos se cancelan.
    """

    def __init__(self, config: PlatformConfig, env: Optional[simpy.Environment] = None,
                 rng: Optional[np.random.Generator] = None) -> None:
        self.config: Final[PlatformConfig] = config
        self.env: Final[simpy.Environment] = env or simpy.Environment()
        self.rng: Final[np.random.Generator] = rng or np.random.default_rng(config.random_seed)

        self.pools: Final[dict[str, simpy.Resource]] = {
            t: simpy.Resource(self.env, capacity=max(1, n))
            for t, n in config.stand_counts.items() if n > 0
        }
        self.records: list[AircraftRecord] = []
        self._procs: list[simpy.Process] = []
        self._mix_types = [t for t, p in config.aircraft_mix_pct.items() if p > 0]
        self._mix_probs = np.array([config.aircraft_mix_pct[t] for t in self._mix_types]) / 100.0

    # --------------------------------------------------------------- llegadas
    def source(self, env: simpy.Environment) -> "simpy.events.ProcessGenerator":
        cfg = self.config
        mean_gap = 3600.0 / cfg.arrival_rate_ah
        aid = 0
        while env.now < cfg.peak_duration_min * 60.0:
            yield env.timeout(float(self.rng.exponential(mean_gap)))
            aid += 1
            a_type = str(self.rng.choice(self._mix_types, p=self._mix_probs))
            self._procs.append(env.process(self.aircraft_flow(env, aid, a_type)))

    # --------------------------------------------------------------- aeronave
    def aircraft_flow(self, env: simpy.Environment, aid: int, a_type: str) -> "simpy.events.ProcessGenerator":
        """
        Asignación en dos fases, porque `simpy.Resource.request()` concede al
        instante si hay hueco -- pedir varios pools "a la vez" no es una
        carrera real si alguno ya tiene sitio, siempre ganaría el primero de
        la lista sin mirar disponibilidad. Por eso:

          1) Si algún pool compatible tiene hueco YA, se coge el más pequeño
             que sirva (best-fit real, determinista).
          2) Sólo si TODOS están llenos se lanza una petición a la vez a
             todos (ahí sí quedan en cola de verdad) y se toma el primero que
             se libere, cancelando el resto.
        """
        rec = AircraftRecord(aid, a_type, env.now)
        candidates = compatible_types(a_type)
        if not candidates:
            LOGGER.warning("Tipo %s sin puesto compatible: se descarta.", a_type)
            return

        # --- Fase 1: ¿hay hueco ya en alguno? Preferir el más pequeño que sirva.
        free_now = [t for t in candidates if self.pools[t].count < self.pools[t].capacity]
        if free_now:
            granted_type = max(free_now, key=lambda t: RANK[t])  # más pequeño que sirve
            req = self.pools[granted_type].request()
            yield req  # concede en el mismo paso, no hay espera real
        else:
            # --- Fase 2: todos llenos -> carrera real por cola.
            requests = {t: self.pools[t].request() for t in candidates}
            result = yield simpy.events.AnyOf(env, requests.values())
            granted_type = next(t for t, r in requests.items() if r in result)
            req = requests[granted_type]
            for t, r in requests.items():
                if t == granted_type:
                    continue
                if r.triggered:
                    self.pools[t].release(r)
                else:
                    r.cancel()

        rec.wait_s = env.now - rec.t_arrival_s
        rec.stand_type_used = granted_type
        dwell = sample_dwell_s(a_type, self.rng)
        rec.dwell_s = dwell
        yield env.timeout(dwell)
        self.pools[granted_type].release(req)
        rec.t_exit_s = env.now
        self.records.append(rec)

    # --------------------------------------------------------------- ejecución
    def run(self) -> None:
        life = self.env.process(self._lifecycle())
        self.env.run(until=life)

    def _lifecycle(self) -> "simpy.events.ProcessGenerator":
        yield self.env.process(self.source(self.env))
        if self._procs:
            yield simpy.events.AllOf(self.env, self._procs)

    # --------------------------------------------------------------- analítica
    def dataframe(self) -> pd.DataFrame:
        if not self.records:
            return pd.DataFrame()
        rows = [{
            "id": r.aircraft_id, "tipo": r.a_type, "puesto_usado": r.stand_type_used,
            "espera_min": r.wait_s / 60.0, "ocupacion_min": r.dwell_s / 60.0,
            "t_llegada_min": r.t_arrival_s / 60.0, "t_salida_min": r.t_exit_s / 60.0,
        } for r in self.records]
        return pd.DataFrame(rows).sort_values("id").reset_index(drop=True)
