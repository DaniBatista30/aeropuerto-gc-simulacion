#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
===============================================================================
 LPA-LLEGADAS — Modelo DES del proceso de llegadas del Aeropuerto de Gran Canaria
===============================================================================

 POR QUÉ LLEGADAS NO ES SALIDAS AL REVÉS
 ---------------------------------------
 El proceso de salidas es de LLEGADA CONTINUA: los pasajeros se presentan de
 forma difusa a lo largo de las horas previas a su vuelo, y un proceso de
 Poisson lo describe razonablemente.

 Llegadas es un proceso POR LOTES. Un vuelo aterriza y doscientas personas
 aparecen a la vez en el control de fronteras. La cola no crece suavemente:
 salta. Modelar llegadas con un proceso de Poisson subestima gravemente los
 picos, que son justamente lo que se quiere medir.

 Además aparece un recurso que no existe en salidas: el EQUIPAJE. El pasajero
 no puede salir hasta que su maleta llega a la cinta, y la maleta viaja por un
 sistema independiente con su propio tiempo de primera maleta y su propia tasa
 de entrega. El tiempo de permanencia en la sala de recogida lo fija el sistema
 de equipajes, no el pasajero.

 FUENTES
 -------
 [TFM]  Cap. 3.2.2 "Zona de llegadas": superficies medidas sobre planos,
        número de hipódromos, puestos de frontera y aduana, y capacidades
        calculadas con las fórmulas IATA.
 [PD]   Plan Director, Cap. 3 y Adjunto al Cap. 3: tiempos de inspección
        medidos y fórmulas de área.

 Unidad de tiempo del entorno: SEGUNDOS.
===============================================================================
"""

from __future__ import annotations

import logging
import math
from dataclasses import dataclass, field
from enum import Enum
from typing import Final, Generator, Optional, Sequence

import numpy as np
import pandas as pd
import simpy

from lpa_des_model import (
    SECONDS_PER_HOUR,
    SECONDS_PER_MINUTE,
    FruinQueueStandards,
    GammaService,
    IATALoSStandards,
    LognormalService,
    Minutes,
    RandomStreams,
    Seconds,
    ServiceTimeDistribution,
    SimProcess,
    SquareMeters,
    configure_logging,
)

LOGGER: Final[logging.Logger] = logging.getLogger("LPA-ARR")


# =============================================================================
# 1. SEGMENTACIÓN Y GEOMETRÍA (TFM, cap. 3.2.2)
# =============================================================================
class ArrivalTraffic(str, Enum):
    """Segmentación de llegadas por instalación de destino."""

    NACIONAL = "Nacional"            # Península, Canarias, Baleares
    INTERINSULAR = "Interinsular"
    UE_SCHENGEN = "UE_Schengen"
    UE_NO_SCHENGEN = "UE_No_Schengen"
    INTERNACIONAL = "Internacional"

    @property
    def needs_passport(self) -> bool:
        return self in (ArrivalTraffic.UE_NO_SCHENGEN, ArrivalTraffic.INTERNACIONAL)

    @property
    def needs_customs(self) -> bool:
        return self.needs_passport


class ReclaimHall(str, Enum):
    """Las tres salas de recogida de equipajes de LPA."""

    NACIONAL = "Sala_Nacional"
    EUROPEA = "Sala_Europea"
    INTERNACIONAL = "Sala_Internacional"


@dataclass(frozen=True)
class ReclaimHallSpec:
    """
    Sala de recogida: superficie e hipódromos (TFM, medidos sobre planos).

    `capacity_php` es la capacidad publicada en el TFM con la fórmula IATA
    A = e·w·s/60·1,1, y sirve de contraste para el modelo dinámico.
    """

    hall: ReclaimHall
    area_m2: SquareMeters
    carousels: int
    dwell_min: Minutes          # tiempo medio de estancia (PD: UE 25, Int 30, Inter 20)
    capacity_php_tfm: float


RECLAIM_HALLS: Final[dict[ReclaimHall, ReclaimHallSpec]] = {
    ReclaimHall.INTERNACIONAL: ReclaimHallSpec(
        ReclaimHall.INTERNACIONAL, area_m2=1868.0, carousels=3,
        dwell_min=30.0, capacity_php_tfm=1700.0),
    ReclaimHall.EUROPEA: ReclaimHallSpec(
        ReclaimHall.EUROPEA, area_m2=4985.0, carousels=10,
        dwell_min=25.0, capacity_php_tfm=5438.0),
    ReclaimHall.NACIONAL: ReclaimHallSpec(
        ReclaimHall.NACIONAL, area_m2=3275.0, carousels=5,
        dwell_min=20.0, capacity_php_tfm=3970.0),
}

TOTAL_CAROUSELS: Final[int] = sum(h.carousels for h in RECLAIM_HALLS.values())  # 18
TOTAL_RECLAIM_AREA_M2: Final[SquareMeters] = sum(
    h.area_m2 for h in RECLAIM_HALLS.values())                                  # 10.128


def hall_for(traffic: ArrivalTraffic) -> ReclaimHall:
    """Asignación de tráfico a sala de recogida (TFM)."""
    if traffic is ArrivalTraffic.INTERNACIONAL:
        return ReclaimHall.INTERNACIONAL
    if traffic in (ArrivalTraffic.UE_SCHENGEN, ArrivalTraffic.UE_NO_SCHENGEN):
        return ReclaimHall.EUROPEA
    return ReclaimHall.NACIONAL


# --- Control de fronteras en llegadas ---------------------------------------
PASSPORT_BOOTHS_INSTALLED: Final[int] = 4
PASSPORT_BOOTHS_OPERATIONAL: Final[int] = 2
"""
El TFM constata que de los 4 puestos instalados sólo 2 están operativos.
Esta es la diferencia entre capacidad instalada y capacidad disponible, y es
precisamente el tipo de cosa que un modelo estático no captura.
"""
PASSPORT_QUEUE_AREA_M2: Final[SquareMeters] = 1385.0
PASSPORT_SERVICE_S: Final[float] = 15.0      # t4, medido (PD y TFM)

# --- Aduanas -----------------------------------------------------------------
CUSTOMS_AREA_M2: Final[SquareMeters] = 47.0
CUSTOMS_AREA_REQUIRED_M2: Final[SquareMeters] = 54.0   # TFM, para 848 pax/h
CUSTOMS_CAPACITY_PHP_TFM: Final[float] = 732.0
CUSTOMS_CHECK_FRACTION: Final[float] = 0.25
CUSTOMS_SERVICE_S: Final[float] = 90.0        # 1,5 min (PD y TFM)
CUSTOMS_POSITIONS_REQUIRED: Final[int] = 5    # TFM

# --- Vestíbulos de llegadas ---------------------------------------------------
ARRIVALS_HALL_NORTH_M2: Final[SquareMeters] = 1879.0   # nacional + interinsular
ARRIVALS_HALL_SOUTH_M2: Final[SquareMeters] = 1279.0   # UE + internacional
ARRIVALS_HALL_NORTH_CAP_PHP: Final[float] = 3660.0
ARRIVALS_HALL_SOUTH_CAP_PHP: Final[float] = 3919.0
ARRIVALS_HALL_PAX_DWELL_MIN: Final[Minutes] = 5.0
ARRIVALS_HALL_VISITOR_DWELL_MIN: Final[Minutes] = 30.0


# =============================================================================
# 2. DEMANDA EN HORA PUNTA POR TIPO DE TRÁFICO (TFM, datos 2018)
# =============================================================================
PHP_ARRIVALS_BY_TRAFFIC: Final[dict[ArrivalTraffic, int]] = {
    ArrivalTraffic.INTERINSULAR: 544,
    ArrivalTraffic.NACIONAL: 664,
    ArrivalTraffic.INTERNACIONAL: 107,
    ArrivalTraffic.UE_SCHENGEN: 1429,
    ArrivalTraffic.UE_NO_SCHENGEN: 741,
}
"""
ADVERTENCIA METODOLÓGICA IMPORTANTE.

El TFM calcula la hora punta de CADA tipo de tráfico por separado. Esos picos
NO son simultáneos: la hora punta del tráfico interinsular no coincide con la
del internacional. Por eso la suma (3.485 pax/h) supera el PHP total de
llegadas del propio TFM (2.146 pax/h).

Sumarlos, como se hace al dimensionar un vestíbulo compartido, produce un
escenario conservador —válido para diseño— pero que NO debe presentarse como
una hora punta real observada. El modelo permite las dos lecturas mediante el
parámetro `coincidence_factor`, y el informe (`main()`) las ejecuta ambas por
separado para que no se confundan.
"""

PHP_ARRIVALS_TOTAL_OBSERVED: Final[int] = 2146   # TFM, hora punta real de llegadas
PHP_ARRIVALS_SUM_BY_TYPE: Final[int] = sum(PHP_ARRIVALS_BY_TRAFFIC.values())  # 3.485

COINCIDENCE_FACTOR: Final[float] = PHP_ARRIVALS_TOTAL_OBSERVED / PHP_ARRIVALS_SUM_BY_TYPE
"""Factor de no coincidencia de puntas: 0,62."""

PASSPORT_DEMAND_PHP: Final[int] = (PHP_ARRIVALS_BY_TRAFFIC[ArrivalTraffic.INTERNACIONAL]
                                   + PHP_ARRIVALS_BY_TRAFFIC[ArrivalTraffic.UE_NO_SCHENGEN])
# 848 pax/h cruzan frontera en llegadas.


# =============================================================================
# 3. CONFIGURACIÓN
# =============================================================================
@dataclass(frozen=True)
class ArrivalsConfig:
    """Escenario de llegadas."""

    scenario_name: str = "LPA llegadas — configuración actual"

    # --- Recursos ------------------------------------------------------------
    passport_booths: int = PASSPORT_BOOTHS_OPERATIONAL   # dato TFM: 2 de 4
    # HIPÓTESIS DEL MODELO, no dato de fuente: el TFM da los puestos
    # NECESARIOS (5) pero no dice cuántos están operativos hoy, a diferencia
    # de fronteras donde sí lo especifica. Se asume el mismo valor (2) por
    # similitud operativa; debe verificarse con el gestor antes de un informe.
    customs_positions: int = 2
    egates_arrivals: int = 0          # LPA no disponía de ABC en llegadas en 2018

    # --- Demanda --------------------------------------------------------------
    # `coincidence_factor` decide qué lectura de la demanda se simula:
    #   1,0  -> escenario de DISEÑO: los picos de cada tráfico se solapan por
    #          completo (lo que asume el TFM al dimensionar instalaciones
    #          compartidas). Es un techo, no una hora observada.
    #   0,62 -> escenario OBSERVADO: reproduce el PHP total real de llegadas
    #          (2.146 pax/h), aplicando el factor de no-coincidencia medido.
    php_by_traffic: dict[ArrivalTraffic, int] = field(
        default_factory=lambda: dict(PHP_ARRIVALS_BY_TRAFFIC))
    coincidence_factor: float = 1.0

    # Ventana durante la que SE PROGRAMAN vuelos a ritmo de hora punta. La
    # simulación sigue corriendo después de esa ventana hasta que el sistema
    # se vacía: eso es lo que permite medir si la punta deja un remanente
    # (backlog) que se prolonga más allá de la propia hora punta, en vez de
    # forzar una demanda sostenida artificialmente durante todo el horizonte.
    peak_duration_min: Minutes = 60.0

    # --- Estructura de los vuelos ---------------------------------------------
    mean_pax_per_flight: int = 180
    deplaning_rate_pax_min: float = 18.0   # desembarque por pasarela
    walk_to_hall_dist: ServiceTimeDistribution = LognormalService(
        mean_s=300.0, sigma_log=0.35)      # 5 min de andén hasta la sala

    # --- Sistema de equipajes -------------------------------------------------
    first_bag_s: Seconds = 900.0           # 15 min desde calzos hasta 1ª maleta
    bag_delivery_rate_per_min: float = 18.0  # maletas/min por hipódromo
    bags_per_pax: float = 1.3
    p_pax_with_bag: float = 0.80

    # --- Servicios --------------------------------------------------------------
    passport_dist: ServiceTimeDistribution = GammaService(
        mean_s=PASSPORT_SERVICE_S, cv=0.35)
    customs_dist: ServiceTimeDistribution = LognormalService(
        mean_s=CUSTOMS_SERVICE_S, sigma_log=0.45)
    p_customs_check: float = CUSTOMS_CHECK_FRACTION

    # --- Estándares ------------------------------------------------------------
    los: IATALoSStandards = field(default_factory=IATALoSStandards)
    fruin: FruinQueueStandards = field(default_factory=FruinQueueStandards)

    random_seed: int = 20260920

    def scaled_php(self, traffic: ArrivalTraffic) -> float:
        return self.php_by_traffic[traffic] * self.coincidence_factor

    @property
    def total_php(self) -> float:
        return sum(self.scaled_php(t) for t in self.php_by_traffic)


# =============================================================================
# 4. REGISTRO
# =============================================================================
@dataclass
class ArrivingPassenger:
    pax_id: int
    traffic: ArrivalTraffic
    flight_id: int
    has_bag: bool

    t_deplane_s: Seconds = 0.0
    wait_passport_s: Seconds = 0.0
    service_passport_s: Seconds = 0.0
    wait_bag_s: Seconds = 0.0
    wait_customs_s: Seconds = 0.0
    t_exit_s: Seconds = 0.0

    @property
    def total_time_s(self) -> Seconds:
        return self.t_exit_s - self.t_deplane_s

    def as_row(self) -> dict[str, object]:
        m = SECONDS_PER_MINUTE
        return {
            "pax_id": self.pax_id,
            "trafico": self.traffic.value,
            "vuelo": self.flight_id,
            "sala": hall_for(self.traffic).value,
            "con_equipaje": self.has_bag,
            "t_desembarque_min": self.t_deplane_s / m,
            "espera_pasaportes_min": self.wait_passport_s / m,
            "espera_equipaje_min": self.wait_bag_s / m,
            "espera_aduanas_min": self.wait_customs_s / m,
            "t_salida_min": self.t_exit_s / m,
            "tiempo_total_min": self.total_time_s / m,
        }


class ArrivalZone:
    """Zona instrumentada de llegadas: ocupación y densidad."""

    def __init__(self, name: str, area_m2: SquareMeters) -> None:
        self.name: Final[str] = name
        self.area_m2: Final[SquareMeters] = area_m2
        self.occupancy: int = 0
        self.peak_occupancy: int = 0

    def enter(self) -> None:
        self.occupancy += 1
        self.peak_occupancy = max(self.peak_occupancy, self.occupancy)

    def leave(self) -> None:
        self.occupancy -= 1

    @property
    def m2_per_pax(self) -> SquareMeters:
        return self.area_m2 / max(1, self.occupancy)


# =============================================================================
# 5. MODELO
# =============================================================================
class ArrivalsModel:
    """
    Modelo DES de llegadas: frontera, equipajes, aduanas y vestíbulo.

    La diferencia estructural con salidas es que la demanda llega POR LOTES
    (vuelos) y que el tiempo de permanencia en la sala de recogida lo fija el
    sistema de equipajes, no el pasajero.
    """

    def __init__(self, config: ArrivalsConfig,
                 env: Optional[simpy.Environment] = None,
                 streams: Optional[RandomStreams] = None) -> None:
        self.config: Final[ArrivalsConfig] = config
        self.env: Final[simpy.Environment] = env or simpy.Environment()
        self.streams: Final[RandomStreams] = streams or RandomStreams(config.random_seed)

        self.passport_booths: Final[simpy.Resource] = simpy.Resource(
            self.env, capacity=max(1, config.passport_booths))
        self.customs_positions: Final[simpy.Resource] = simpy.Resource(
            self.env, capacity=max(1, config.customs_positions))
        # Un hipódromo atiende a un vuelo cada vez.
        self.carousels: Final[dict[ReclaimHall, simpy.Resource]] = {
            h: simpy.Resource(self.env, capacity=spec.carousels)
            for h, spec in RECLAIM_HALLS.items()
        }

        self.zones: Final[dict[str, ArrivalZone]] = {
            "Frontera": ArrivalZone("Frontera", PASSPORT_QUEUE_AREA_M2),
            "Aduanas": ArrivalZone("Aduanas", CUSTOMS_AREA_M2),
            **{h.value: ArrivalZone(h.value, spec.area_m2)
               for h, spec in RECLAIM_HALLS.items()},
        }

        self.passengers: list[ArrivingPassenger] = []
        self.log: list[dict[str, object]] = []
        self._procs: list[simpy.Process] = []
        self._pax_counter: int = 0

    # ------------------------------------------------------------------ vuelos
    def flight_source(self, env: simpy.Environment) -> SimProcess:
        """
        Genera vuelos. Cada tipo de tráfico produce vuelos a la frecuencia que
        corresponde a su PHP y al tamaño medio de aeronave.
        """
        cfg = self.config
        flight_id = 0
        # Intervalo entre vuelos por tipo de tráfico.
        schedule: list[tuple[float, ArrivalTraffic]] = []
        for traffic in cfg.php_by_traffic:
            php = cfg.scaled_php(traffic)
            if php <= 0:
                continue
            gap_s = SECONDS_PER_HOUR / (php / cfg.mean_pax_per_flight)
            t = float(self.streams.arrivals.uniform(0, gap_s))
            while t < cfg.peak_duration_min * SECONDS_PER_MINUTE:
                schedule.append((t, traffic))
                t += float(self.streams.arrivals.exponential(gap_s))
        schedule.sort()

        now = 0.0
        for t, traffic in schedule:
            yield env.timeout(max(0.0, t - now))
            now = t
            flight_id += 1
            pax = int(self.streams.routing.normal(cfg.mean_pax_per_flight,
                                                  cfg.mean_pax_per_flight * 0.20))
            pax = max(20, pax)
            self._procs.append(env.process(self.flight(env, flight_id, traffic, pax)))

    def flight(self, env: simpy.Environment, flight_id: int,
               traffic: ArrivalTraffic, n_pax: int) -> SimProcess:
        """
        Un vuelo: desembarque progresivo y entrega de equipajes en paralelo.

        El proceso de equipaje arranca en el momento de calzos, con un retardo
        de primera maleta, y entrega a tasa constante. El pasajero espera a su
        maleta, cuyo orden de salida es aleatorio.
        """
        cfg = self.config
        onblock = env.now
        hall = hall_for(traffic)

        # Reserva del hipódromo para este vuelo.
        carousel_req = self.carousels[hall].request()
        yield carousel_req

        # Instante en que la maleta de rango k está en la cinta.
        n_bags = int(n_pax * cfg.p_pax_with_bag * cfg.bags_per_pax)
        def bag_ready(rank: int) -> Seconds:
            return onblock + cfg.first_bag_s + rank * 60.0 / cfg.bag_delivery_rate_per_min

        ranks = list(range(max(1, n_bags)))
        self.streams.routing.shuffle(ranks)

        for i in range(n_pax):
            self._pax_counter += 1
            has_bag = bool(self.streams.routing.random() < cfg.p_pax_with_bag)
            rank = ranks[i % len(ranks)]
            rec = ArrivingPassenger(self._pax_counter, traffic, flight_id, has_bag)
            self._procs.append(env.process(
                self.passenger(env, rec, bag_ready(rank), hall)))
            yield env.timeout(60.0 / cfg.deplaning_rate_pax_min)

        # El hipódromo queda libre cuando ha salido la última maleta.
        yield env.timeout(max(0.0, bag_ready(len(ranks)) - env.now))
        self.carousels[hall].release(carousel_req)

    # --------------------------------------------------------------- pasajero
    def passenger(self, env: simpy.Environment, rec: ArrivingPassenger,
                  bag_ready_s: Seconds, hall: ReclaimHall) -> SimProcess:
        """
        Ruta del pasajero de llegada.

            Desembarque -> andén -> [Frontera si no-Schengen]
                        -> Sala de recogida (espera de su maleta)
                        -> [Aduanas si procede] -> Vestíbulo -> Salida
        """
        cfg, st = self.config, self.streams
        rec.t_deplane_s = env.now

        yield env.timeout(cfg.walk_to_hall_dist.sample(st.arrivals))

        # --- Control de fronteras -------------------------------------------
        if rec.traffic.needs_passport:
            z = self.zones["Frontera"]
            z.enter()
            t0 = env.now
            with self.passport_booths.request() as req:
                yield req
                rec.wait_passport_s = env.now - t0
                s = cfg.passport_dist.sample(st.passport)
                rec.service_passport_s = s
                yield env.timeout(s)
            z.leave()

        # --- Sala de recogida de equipajes -----------------------------------
        z_hall = self.zones[hall.value]
        z_hall.enter()
        if rec.has_bag:
            t0 = env.now
            wait = max(0.0, bag_ready_s - env.now)
            yield env.timeout(wait)
            rec.wait_bag_s = env.now - t0
        z_hall.leave()

        # --- Aduanas ----------------------------------------------------------
        if rec.traffic.needs_customs and st.routing.random() < cfg.p_customs_check:
            z = self.zones["Aduanas"]
            z.enter()
            t0 = env.now
            with self.customs_positions.request() as req:
                yield req
                rec.wait_customs_s = env.now - t0
                yield env.timeout(cfg.customs_dist.sample(st.secondary))
            z.leave()

        rec.t_exit_s = env.now
        self.passengers.append(rec)

    # --------------------------------------------------------------- monitor
    def monitor(self, env: simpy.Environment, step: Minutes = 1.0) -> SimProcess:
        cfg = self.config
        while True:
            row: dict[str, object] = {"t_min": env.now / SECONDS_PER_MINUTE,
                                      "q_pasaportes": len(self.passport_booths.queue),
                                      "q_aduanas": len(self.customs_positions.queue)}
            for name, z in self.zones.items():
                d = z.m2_per_pax
                row[f"occ_{name}"] = z.occupancy
                row[f"m2pax_{name}"] = d
                row[f"fruin_{name}"] = cfg.fruin.grade(d).value
                row[f"crowded_{name}"] = (z.occupancy > 0
                                          and d < cfg.los.space_optimum_m2_pax)
            self.log.append(row)
            yield env.timeout(step * SECONDS_PER_MINUTE)

    # --------------------------------------------------------------- ejecución
    def _lifecycle(self, env: simpy.Environment) -> SimProcess:
        yield env.process(self.flight_source(env))
        while self._procs:
            batch, self._procs = self._procs, []
            yield simpy.events.AllOf(env, batch)

    def run(self, monitor_step: Minutes = 1.0) -> None:
        self.env.process(self.monitor(self.env, monitor_step))
        life = self.env.process(self._lifecycle(self.env))
        self.env.run(until=life)

    # --------------------------------------------------------------- analítica
    def passengers_dataframe(self) -> pd.DataFrame:
        if not self.passengers:
            return pd.DataFrame()
        return pd.DataFrame([p.as_row() for p in self.passengers])

    def log_dataframe(self) -> pd.DataFrame:
        return pd.DataFrame(self.log)

    def kpi_summary(self) -> pd.DataFrame:
        df = self.passengers_dataframe()
        if df.empty:
            return pd.DataFrame()
        rows = []
        for col, label, mask in [
            ("espera_pasaportes_min", "Control de fronteras",
             df["trafico"].isin([ArrivalTraffic.INTERNACIONAL.value,
                                 ArrivalTraffic.UE_NO_SCHENGEN.value])),
            ("espera_equipaje_min", "Espera de equipaje", df["con_equipaje"]),
            ("espera_aduanas_min", "Aduanas", df["espera_aduanas_min"] > 0),
            ("tiempo_total_min", "Tiempo total en llegadas",
             pd.Series(True, index=df.index)),
        ]:
            s = df.loc[mask, col].astype(float)
            if s.empty:
                continue
            rows.append({
                "proceso": label,
                "n_pax": int(s.size),
                "media_min": round(float(s.mean()), 2),
                "P95_min": round(float(np.percentile(s, 95)), 2),
                "P99_min": round(float(np.percentile(s, 99)), 2),
                "max_min": round(float(s.max()), 2),
            })
        return pd.DataFrame(rows)

    def backlog_summary(self) -> dict[str, object]:
        """
        Remanente al final de la hora punta y tiempo de vaciado.

        Es la lectura correcta de un recurso deficitario: no "cuánto se
        espera de media" (que crece sin límite si se sostiene la demanda
        indefinidamente), sino "cuánta gente queda a las 60 minutos" y
        "cuánto tarda el sistema en vaciarse una vez cesan los vuelos".
        """
        df = self.passengers_dataframe()
        if df.empty:
            return {}
        peak_s = self.config.peak_duration_min * SECONDS_PER_MINUTE
        exit_s = df["t_salida_min"].astype(float) * SECONDS_PER_MINUTE
        deplane_s = exit_s - df["tiempo_total_min"].astype(float) * SECONDS_PER_MINUTE
        backlog_at_peak = int(((deplane_s <= peak_s) & (exit_s > peak_s)).sum())
        drain_min = max(0.0, (exit_s.max() - peak_s) / SECONDS_PER_MINUTE)
        return {
            "duracion_punta_min": self.config.peak_duration_min,
            "pax_totales": len(df),
            "remanente_al_final_de_punta": backlog_at_peak,
            "pct_remanente": round(100 * backlog_at_peak / len(df), 1),
            "minutos_hasta_vaciado_total": round(drain_min, 1),
        }

    def zone_summary(self) -> pd.DataFrame:
        log = self.log_dataframe()
        if log.empty:
            return pd.DataFrame()
        rows = []
        for name, z in self.zones.items():
            dens = log[f"m2pax_{name}"].astype(float)
            occ = log[f"occ_{name}"].astype(float)
            active = dens[occ > 0]
            dmin = float(active.min()) if not active.empty else float(dens.max())
            rows.append({
                "zona": name,
                "area_m2": z.area_m2,
                "ocup_pico": z.peak_occupancy,
                "m2pax_min": round(dmin, 2),
                "fruin_peor": self.config.fruin.grade(dmin).value,
                "pct_t_crowded": round(100 * float(log[f"crowded_{name}"].mean()), 1),
            })
        return pd.DataFrame(rows)


# =============================================================================
# 6. CONTRASTE ANALÍTICO CONTRA EL TFM
# =============================================================================
def analytic_capacity_check() -> pd.DataFrame:
    """
    Capacidades publicadas en el TFM frente a la demanda de 2018.

    Reproduce el cálculo estático para poder contrastarlo después con el modelo
    dinámico. Un déficit aquí ya es un incumplimiento en la configuración
    actual, sin necesidad de proyectar a futuro.
    """
    php = PHP_ARRIVALS_BY_TRAFFIC
    ue = php[ArrivalTraffic.UE_SCHENGEN] + php[ArrivalTraffic.UE_NO_SCHENGEN]
    nac = php[ArrivalTraffic.NACIONAL] + php[ArrivalTraffic.INTERINSULAR]
    intl = php[ArrivalTraffic.INTERNACIONAL]

    # Fórmula de puestos de frontera: N = d·t4/60·1,1  =>  d = N·60/(t4/60·1,1)
    def passport_capacity(n_booths: int) -> float:
        return n_booths * 60.0 / ((PASSPORT_SERVICE_S / 60.0) * 1.1)

    rows = [
        {"instalacion": "Frontera (4 puestos instalados)",
         "capacidad_php": round(passport_capacity(PASSPORT_BOOTHS_INSTALLED)),
         "demanda_php": PASSPORT_DEMAND_PHP},
        {"instalacion": "Frontera (2 puestos operativos)",
         "capacidad_php": round(passport_capacity(PASSPORT_BOOTHS_OPERATIONAL)),
         "demanda_php": PASSPORT_DEMAND_PHP},
        {"instalacion": "Aduanas (47 m2)",
         "capacidad_php": CUSTOMS_CAPACITY_PHP_TFM,
         "demanda_php": PASSPORT_DEMAND_PHP},
        {"instalacion": "Recogida Internacional",
         "capacidad_php": RECLAIM_HALLS[ReclaimHall.INTERNACIONAL].capacity_php_tfm,
         "demanda_php": intl},
        {"instalacion": "Recogida Europea",
         "capacidad_php": RECLAIM_HALLS[ReclaimHall.EUROPEA].capacity_php_tfm,
         "demanda_php": ue},
        {"instalacion": "Recogida Nacional",
         "capacidad_php": RECLAIM_HALLS[ReclaimHall.NACIONAL].capacity_php_tfm,
         "demanda_php": nac},
        {"instalacion": "Vestíbulo Sur (UE + Internacional)",
         "capacidad_php": ARRIVALS_HALL_SOUTH_CAP_PHP,
         "demanda_php": ue + intl},
        {"instalacion": "Vestíbulo Norte (Nacional + Interinsular)",
         "capacidad_php": ARRIVALS_HALL_NORTH_CAP_PHP,
         "demanda_php": nac},
    ]
    df = pd.DataFrame(rows)
    df["ratio_C_D"] = (df["capacidad_php"] / df["demanda_php"]).round(2)
    df["estado"] = df["ratio_C_D"].apply(
        lambda r: "DEFICIT" if r < 1.0 else ("AL LIMITE" if r < 1.2 else "holgado"))
    return df


# =============================================================================
# 7. INFORME
# =============================================================================
def main(argv: Optional[Sequence[str]] = None) -> None:
    configure_logging(logging.ERROR)
    pd.set_option("display.width", 220)
    pd.set_option("display.max_columns", 30)

    print("=" * 92)
    print("LLEGADAS — Aeropuerto de Gran Canaria")
    print("=" * 92)

    print("\n--- Instalaciones (TFM, superficies medidas sobre planos) ---")
    for h, s in RECLAIM_HALLS.items():
        print(f"  {h.value:<22}{s.area_m2:>8.0f} m2 | {s.carousels:>2} hipódromos "
              f"| estancia {s.dwell_min:.0f} min")
    print(f"  {'Frontera':<22}{PASSPORT_QUEUE_AREA_M2:>8.0f} m2 | "
          f"{PASSPORT_BOOTHS_INSTALLED} puestos instalados, "
          f"{PASSPORT_BOOTHS_OPERATIONAL} OPERATIVOS")
    print(f"  {'Aduanas':<22}{CUSTOMS_AREA_M2:>8.0f} m2 | "
          f"necesarios {CUSTOMS_AREA_REQUIRED_M2:.0f} m2")
    print(f"  {'Vestíbulo Norte':<22}{ARRIVALS_HALL_NORTH_M2:>8.0f} m2")
    print(f"  {'Vestíbulo Sur':<22}{ARRIVALS_HALL_SOUTH_M2:>8.0f} m2")

    print("\n--- Hora punta por tipo de tráfico (2018) ---")
    for t, v in PHP_ARRIVALS_BY_TRAFFIC.items():
        print(f"  {t.value:<18}{v:>6} pax/h")
    print(f"  {'SUMA':<18}{PHP_ARRIVALS_SUM_BY_TYPE:>6} pax/h  (picos NO simultáneos)")
    print(f"  {'Punta observada':<18}{PHP_ARRIVALS_TOTAL_OBSERVED:>6} pax/h  "
          f"-> factor de coincidencia {COINCIDENCE_FACTOR:.2f}")

    print("\n--- Contraste estático capacidad / demanda (2018) ---")
    print(analytic_capacity_check().to_string(index=False))

    for factor, label in [(1.0, "DISEÑO (picos sumados, techo)"),
                          (COINCIDENCE_FACTOR, "OBSERVADO (PHP real de llegadas)")]:
        print(f"\n--- Simulación dinámica — escenario {label} ---")
        model = ArrivalsModel(ArrivalsConfig(coincidence_factor=factor,
                                             peak_duration_min=60.0))
        model.run()
        print(model.kpi_summary().to_string(index=False))
        print()
        print(model.zone_summary().to_string(index=False))
        print()
        bl = model.backlog_summary()
        print(f"  Remanente al final de la hora punta: {bl.get('remanente_al_final_de_punta')} "
              f"pax ({bl.get('pct_remanente')}% del total) | "
              f"vaciado total en +{bl.get('minutos_hasta_vaciado_total')} min adicionales")


if __name__ == "__main__":
    main()
