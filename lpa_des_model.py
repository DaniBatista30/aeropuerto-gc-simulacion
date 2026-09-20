#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
===============================================================================
 LPA-DES v0.2 — Modelo de Eventos Discretos del Aeropuerto de Gran Canaria
===============================================================================

 MÓDULO DE MODELO. La capa experimental (réplicas, intervalos de confianza,
 comparación de escenarios) vive en `lpa_experiment.py`.

 Cambios frente a v0.1
 ---------------------
 1. DESCOMPOSICIÓN ZONAL del filtro en sus cuatro subzonas reales
    (encolamiento, preparación/divest, inspección, recomposición), cada una con
    su superficie, su ocupación y su densidad instantánea. La métrica m2/pax
    deja de ser una media global del recinto -- que oculta exactamente lo que
    se quiere medir -- y pasa a ser local y auditable por zona.
 2. MODELO DE LÍNEA DE FILTRO CORRECTO. Una línea moderna no es un servidor
    único de 18 s: es un pipeline con varias posiciones de preparación en
    paralelo, UN arco/RX que constriñe el caudal, y varias posiciones de
    recomposición. El caudal real (~200-240 pax/h/línea) sólo se reproduce
    modelando las tres etapas por separado. La recomposición es, en la práctica,
    la causa más frecuente de bloqueo aguas arriba.
 3. NIVEL DE SERVICIO DE FRUIN (A-F) para áreas de encolamiento, además del
    umbral IATA de 1.2 m2/pax. Ambos son consistentes: el límite Fruin A/B
    coincide con el óptimo IATA, lo que da una verificación cruzada.
 4. FLUJOS ALEATORIOS INDEPENDIENTES por fuente estocástica (`RandomStreams`),
    condición necesaria para aplicar Números Aleatorios Comunes (CRN) al
    comparar escenarios en `lpa_experiment.py`.

 Marco normativo
 ---------------
 * Lado Tierra : IATA ADRM (tiempos máximos de cola y espacio por pasajero).
 * Densidades  : J.J. Fruin, "Pedestrian Planning and Design" (LoS A-F).
 * Lado Aire   : OACI Anexo 14 -- no implementado (ver ROADMAP).

 ADVERTENCIA DE CALIBRACIÓN
 --------------------------
 Los umbrales normativos están centralizados en `IATALoSStandards` y
 `FruinQueueStandards`, y los tiempos de servicio en `TerminalConfig`. Son
 valores de trabajo basados en rangos de industria, NO mediciones de LPA.
 Antes de presentar resultados: verificar umbrales contra la edición vigente
 del ADRM y sustituir los tiempos por mediciones del gestor aeroportuario.

 Unidad de tiempo interna del `simpy.Environment`: SEGUNDOS.

 Uso:
     $ pip install simpy scipy numpy pandas
     $ python lpa_des_model.py            # corrida única de demostración
     $ python lpa_experiment.py           # análisis con réplicas (recomendado)
===============================================================================
"""

from __future__ import annotations

import argparse
import logging
import math
from dataclasses import dataclass, field, replace
from enum import Enum
from typing import Final, Generator, Mapping, Optional, Protocol, Sequence, runtime_checkable

import numpy as np
import pandas as pd
import simpy
from scipy import stats

# -----------------------------------------------------------------------------
# Alias de tipos
# -----------------------------------------------------------------------------
Seconds = float
Minutes = float
SquareMeters = float
SimProcess = Generator[simpy.Event, None, None]

SECONDS_PER_MINUTE: Final[Seconds] = 60.0
SECONDS_PER_HOUR: Final[Seconds] = 3600.0

LOGGER: Final[logging.Logger] = logging.getLogger("LPA-DES")


# =============================================================================
# 0. ENUMERACIONES
# =============================================================================
class PaxType(str, Enum):
    SCHENGEN = "Schengen"
    NON_SCHENGEN = "No-Schengen"


class ProcessStage(str, Enum):
    CHECK_IN = "Facturacion"
    SECURITY = "Filtro_Seguridad"
    PASSPORT = "Control_Pasaportes"


class PassportRoute(str, Enum):
    ABC = "ABC_eGate"
    MANUAL = "Cabina_Policia"
    NOT_APPLICABLE = "N/A"


class ZoneKey(str, Enum):
    """Subzonas físicas del filtro de seguridad."""

    QUEUE = "Z1_Encolamiento"
    DIVEST = "Z2_Preparacion"
    SCREENING = "Z3_Inspeccion"
    RECOMPOSE = "Z4_Recomposicion"


class LoSGrade(str, Enum):
    """Clasificación IATA ADRM del Nivel de Servicio (tiempos de cola)."""

    OVER_DESIGN = "Over-design"
    OPTIMUM = "Optimum"
    SUB_OPTIMUM = "Sub-optimum"


class FruinLoS(str, Enum):
    """Nivel de Servicio de Fruin para áreas de encolamiento (A = mejor)."""

    A = "A"
    B = "B"
    C = "C"
    D = "D"
    E = "E"
    F = "F"


# =============================================================================
# 1. ESTÁNDARES NORMATIVOS
# =============================================================================
@dataclass(frozen=True)
class IATALoSStandards:
    """
    Umbrales del IATA ADRM.

    `*_mqt_*` son Maximum Queuing Times en MINUTOS:
      - Over-design : por debajo del rango objetivo (sobredimensionado).
      - Optimum     : dentro del rango de diseño.
      - Sub-optimum : incumplimiento de Nivel de Servicio.
    """

    check_in_mqt_optimum_min: Minutes = 10.0
    check_in_mqt_suboptimum_min: Minutes = 20.0
    security_mqt_optimum_min: Minutes = 5.0
    security_mqt_suboptimum_min: Minutes = 10.0
    passport_mqt_optimum_min: Minutes = 5.0
    passport_mqt_suboptimum_min: Minutes = 10.0

    # Espacio en áreas de encolamiento (m2/pax).
    space_optimum_m2_pax: SquareMeters = 1.2      # umbral de alarma "Over-crowded"
    space_over_design_m2_pax: SquareMeters = 1.5
    space_crush_m2_pax: SquareMeters = 0.5        # densidad física máxima admisible

    def grade_queue_time(self, stage: ProcessStage, wait_min: Minutes) -> LoSGrade:
        if stage is ProcessStage.CHECK_IN:
            lo, hi = self.check_in_mqt_optimum_min, self.check_in_mqt_suboptimum_min
        elif stage is ProcessStage.SECURITY:
            lo, hi = self.security_mqt_optimum_min, self.security_mqt_suboptimum_min
        else:
            lo, hi = self.passport_mqt_optimum_min, self.passport_mqt_suboptimum_min
        if wait_min < lo:
            return LoSGrade.OVER_DESIGN
        if wait_min <= hi:
            return LoSGrade.OPTIMUM
        return LoSGrade.SUB_OPTIMUM

    def grade_space(self, m2_per_pax: SquareMeters) -> LoSGrade:
        if m2_per_pax >= self.space_over_design_m2_pax:
            return LoSGrade.OVER_DESIGN
        if m2_per_pax >= self.space_optimum_m2_pax:
            return LoSGrade.OPTIMUM
        return LoSGrade.SUB_OPTIMUM


@dataclass(frozen=True)
class FruinQueueStandards:
    """
    Niveles de Servicio de Fruin para áreas de espera de pie (m2/pax).

    A: circulación libre entre personas en cola.
    B: se puede pasar entre personas sin molestarlas.
    C: paso restringido; movimiento limitado.
    D: paso sólo perturbando a otros; contacto probable.
    E: contacto inevitable; no hay movimiento interno.
    F: densidad de multitud; riesgo de aglomeración.

    El límite A/B (1.2 m2/pax) coincide con el umbral óptimo del ADRM, lo que
    da una verificación cruzada entre ambas normativas.
    """

    a_min: SquareMeters = 1.2
    b_min: SquareMeters = 0.9
    c_min: SquareMeters = 0.7
    d_min: SquareMeters = 0.3
    e_min: SquareMeters = 0.2

    def grade(self, m2_per_pax: SquareMeters) -> FruinLoS:
        if m2_per_pax >= self.a_min:
            return FruinLoS.A
        if m2_per_pax >= self.b_min:
            return FruinLoS.B
        if m2_per_pax >= self.c_min:
            return FruinLoS.C
        if m2_per_pax >= self.d_min:
            return FruinLoS.D
        if m2_per_pax >= self.e_min:
            return FruinLoS.E
        return FruinLoS.F


# =============================================================================
# 2. CAPA ESTOCÁSTICA
# =============================================================================
@runtime_checkable
class ServiceTimeDistribution(Protocol):
    def sample(self, rng: np.random.Generator) -> Seconds: ...
    def theoretical_mean(self) -> Seconds: ...
    def theoretical_quantile(self, q: float) -> Seconds: ...
    def describe(self) -> str: ...


@dataclass(frozen=True)
class GammaService:
    """
    Gamma(k, theta) parametrizada por media y coeficiente de variación.

        k     (shape) = 1 / CV^2
        theta (scale) = mu * CV^2
        => E[X] = mu, Var[X] = (mu*CV)^2, asimetría = 2*CV

    Ley natural de un servicio compuesto por micro-tareas secuenciales: la suma
    de k etapas exponenciales es exactamente una Erlang/Gamma. Adecuada para
    arco/RX, preparación de bandejas y e-Gates ABC.
    """

    mean_s: Seconds
    cv: float
    floor_s: Seconds = 1.0

    @property
    def shape(self) -> float:
        return 1.0 / (self.cv ** 2)

    @property
    def scale(self) -> float:
        return self.mean_s * (self.cv ** 2)

    def sample(self, rng: np.random.Generator) -> Seconds:
        # Se usa el generador de NumPy y no `scipy.stats.gamma.rvs` por
        # rendimiento: es ~50x más rápido por llamada y matemáticamente
        # idéntico. SciPy se reserva para los cuantiles teóricos, que sirven
        # para validar el muestreo (ver `theoretical_quantile`).
        return max(self.floor_s, float(rng.gamma(self.shape, self.scale)))

    def theoretical_mean(self) -> Seconds:
        return self.mean_s

    def theoretical_quantile(self, q: float) -> Seconds:
        return float(stats.gamma.ppf(q, a=self.shape, scale=self.scale))

    def describe(self) -> str:
        return (f"Gamma(k={self.shape:.2f}, theta={self.scale:.2f}s) "
                f"| mu={self.mean_s:.1f}s CV={self.cv:.2f} skew={2*self.cv:.2f}")


@dataclass(frozen=True)
class LognormalService:
    """
    Lognormal parametrizada por la media ARITMÉTICA objetivo.

        mu_log = ln(mean_s) - sigma_log^2 / 2
        SciPy  : lognorm(s=sigma_log, scale=exp(mu_log))

    Cola derecha más pesada que la Gamma: reproduce los casos patológicos del
    mostrador (exceso de equipaje, documentación de menores, mascotas) que
    gobiernan los percentiles P95/P99 de la cola.
    """

    mean_s: Seconds
    sigma_log: float
    floor_s: Seconds = 5.0

    @property
    def mu_log(self) -> float:
        return math.log(self.mean_s) - 0.5 * (self.sigma_log ** 2)

    def sample(self, rng: np.random.Generator) -> Seconds:
        return max(self.floor_s, float(rng.lognormal(self.mu_log, self.sigma_log)))

    def theoretical_mean(self) -> Seconds:
        return self.mean_s

    def theoretical_quantile(self, q: float) -> Seconds:
        return float(stats.lognorm.ppf(q, s=self.sigma_log, scale=math.exp(self.mu_log)))

    def describe(self) -> str:
        cv = math.sqrt(math.exp(self.sigma_log ** 2) - 1.0)
        return (f"Lognormal(mu_log={self.mu_log:.3f}, sigma_log={self.sigma_log:.2f}) "
                f"| mu={self.mean_s:.1f}s CV={cv:.2f}")


class RandomStreams:
    """
    Flujos aleatorios independientes, uno por fuente estocástica.

    Por qué importa: para comparar dos escenarios con Números Aleatorios
    Comunes (CRN) hace falta que cada fuente de aleatoriedad consuma su propio
    flujo. Con un único generador compartido, un cambio en el número de eventos
    de un proceso desplaza el flujo de todos los demás y destruye la
    sincronización, que es justamente lo que produce la reducción de varianza.

    Se construyen con `SeedSequence.spawn`, que garantiza independencia
    estadística entre subflujos derivados de una misma semilla maestra.
    """

    STREAM_NAMES: Final[tuple[str, ...]] = (
        "arrivals", "routing", "check_in", "divest", "screening",
        "recompose", "secondary", "passport",
    )

    def __init__(self, seed: int) -> None:
        self.seed: Final[int] = seed
        children = np.random.SeedSequence(seed).spawn(len(self.STREAM_NAMES))
        self._streams: Final[dict[str, np.random.Generator]] = {
            name: np.random.default_rng(child)
            for name, child in zip(self.STREAM_NAMES, children)
        }

    def __getitem__(self, name: str) -> np.random.Generator:
        return self._streams[name]

    @property
    def arrivals(self) -> np.random.Generator:
        return self._streams["arrivals"]

    @property
    def routing(self) -> np.random.Generator:
        return self._streams["routing"]

    @property
    def check_in(self) -> np.random.Generator:
        return self._streams["check_in"]

    @property
    def divest(self) -> np.random.Generator:
        return self._streams["divest"]

    @property
    def screening(self) -> np.random.Generator:
        return self._streams["screening"]

    @property
    def recompose(self) -> np.random.Generator:
        return self._streams["recompose"]

    @property
    def secondary(self) -> np.random.Generator:
        return self._streams["secondary"]

    @property
    def passport(self) -> np.random.Generator:
        return self._streams["passport"]


# =============================================================================
# 3. GEOMETRÍA ZONAL
# =============================================================================
@dataclass(frozen=True)
class ZoneSpec:
    """
    Especificación física de una subzona del filtro.

    `closure_ratio` permite clausurar parcialmente ESA zona concreta, que es
    como se ejecutan las obras reales: no se cierra "el filtro", se cierra el
    pasillo de encolamiento o la mitad del banco de recomposición.
    """

    key: ZoneKey
    nominal_area_m2: SquareMeters
    closure_ratio: float = 0.0
    crush_m2_pax: SquareMeters = 0.5

    def __post_init__(self) -> None:
        if not 0.0 <= self.closure_ratio < 1.0:
            raise ValueError(f"{self.key}: closure_ratio fuera de [0, 1).")
        if self.nominal_area_m2 <= 0:
            raise ValueError(f"{self.key}: superficie nominal debe ser > 0.")

    @property
    def effective_area_m2(self) -> SquareMeters:
        return self.nominal_area_m2 * (1.0 - self.closure_ratio)

    @property
    def physical_capacity_pax(self) -> int:
        """Aforo físico a densidad de aglomeración (Fruin F)."""
        return max(1, int(self.effective_area_m2 // self.crush_m2_pax))


class SecurityZone:
    """
    Subzona instrumentada: aforo físico, ocupación instantánea y densidad.

    El aforo se implementa como `simpy.Container` de plazas. Cuando se agota,
    el pasajero queda retenido en la zona anterior (spill-back), que es el
    mecanismo que colapsa una terminal en obras y que un modelo de colas sin
    capacidad física no puede reproducir.
    """

    def __init__(self, env: simpy.Environment, spec: ZoneSpec) -> None:
        self.env: Final[simpy.Environment] = env
        self.spec: Final[ZoneSpec] = spec
        self.occupancy: int = 0
        self.peak_occupancy: int = 0
        self.slots: Final[simpy.Container] = simpy.Container(
            env, capacity=spec.physical_capacity_pax, init=spec.physical_capacity_pax)

    @property
    def key(self) -> ZoneKey:
        return self.spec.key

    @property
    def area_m2(self) -> SquareMeters:
        return self.spec.effective_area_m2

    @property
    def m2_per_pax(self) -> SquareMeters:
        return self.area_m2 / max(1, self.occupancy)

    @property
    def blocked_upstream(self) -> int:
        """Pasajeros esperando plaza física en esta zona."""
        return len(self.slots.get_queue)

    def enter(self) -> simpy.Event:
        """Evento de adquisición de plaza física (puede bloquear)."""
        return self.slots.get(1)

    def confirm_entry(self) -> None:
        self.occupancy += 1
        self.peak_occupancy = max(self.peak_occupancy, self.occupancy)

    def leave(self) -> simpy.Event:
        self.occupancy -= 1
        return self.slots.put(1)


# =============================================================================
# 4. CONFIGURACIÓN DEL ESCENARIO
# =============================================================================
def default_zone_layout() -> tuple[ZoneSpec, ...]:
    """
    Geometría de referencia del filtro (superficies de trabajo, m2).

    Reemplazar por las superficies reales del plano de LPA antes de usar el
    modelo para dimensionar.
    """
    return (
        ZoneSpec(ZoneKey.QUEUE, nominal_area_m2=260.0),      # serpentín de espera
        ZoneSpec(ZoneKey.DIVEST, nominal_area_m2=90.0),      # bancos de preparación
        ZoneSpec(ZoneKey.SCREENING, nominal_area_m2=60.0),   # arcos, RX, cacheo
        ZoneSpec(ZoneKey.RECOMPOSE, nominal_area_m2=110.0),  # banco de recomposición
    )


@dataclass(frozen=True)
class TerminalConfig:
    """
    Escenario inmutable. Comparar "actual" vs "obras" = instanciar dos configs.

    Modelo de línea de filtro
    -------------------------
    Una línea NO es un servidor único. Se modela como:
      * `divest_positions_per_lane` posiciones de preparación en paralelo,
      * UN arco/RX por línea -- el cuello de botella que fija el caudal,
      * `recompose_positions_per_lane` posiciones de recomposición.
    Con arco de ~15 s el caudal teórico es ~240 pax/h/línea, coherente con el
    rendimiento observado en líneas modernas (200-240 pax/h).
    """

    scenario_name: str = "LPA-T1 Salidas / Escenario base"

    # --- Recursos ------------------------------------------------------------
    n_check_in_desks: int = 16
    n_security_lanes: int = 4
    # OJO: `n_security_lanes` cuenta ARCOS (unidades de inspección), no
    # "filtros". En LPA, 20 unidades = 10 filtros dobles -> n_security_lanes=20.
    # El registro secundario NO se hace en el arco, sino en una mesa lateral.
    # Modelarlo dentro del arco lo bloquea y hunde el caudal un 27%.
    secondary_positions_per_lane: float = 0.25
    divest_positions_per_lane: int = 3
    # 4 posiciones, no 3: con recomposición de 75 s, 3 posiciones rinden
    # 144 pax/h y estrangulan el arco por debajo del rango de industria.
    recompose_positions_per_lane: int = 4
    n_abc_gates: int = 6
    n_manual_booths: int = 4

    # --- Geometría zonal -----------------------------------------------------
    zones: tuple[ZoneSpec, ...] = field(default_factory=default_zone_layout)

    # --- Mix de pasajeros ----------------------------------------------------
    p_schengen: float = 0.72
    p_uses_check_in: float = 0.55
    p_abc_eligible: float = 0.65
    p_abc_reject: float = 0.06
    p_secondary_search: float = 0.08

    # --- Leyes de servicio ---------------------------------------------------
    dist_check_in: ServiceTimeDistribution = LognormalService(mean_s=150.0, sigma_log=0.45)
    # Preparación: rango de industria 20 s (mín) / 45-60 s (media) / 120 s (máx).
    # Con media 52,5 s y sigma_log 0,40 la Lognormal reproduce ese mínimo y ese
    # máximo como percentiles 1 y 99 (P1 = 19 s, P99 = 123 s).
    dist_divest: ServiceTimeDistribution = LognormalService(mean_s=52.5, sigma_log=0.40)
    # Rayos X: el tiempo es POR BANDEJA, no por pasajero. Muy poco variable.
    dist_screening_per_tray: ServiceTimeDistribution = GammaService(mean_s=12.5, cv=0.25)
    # Bandejas por pasajero: 1, 2 o 3. Media 1,55.
    trays_per_pax_probs: tuple[float, ...] = (0.55, 0.35, 0.10)
    dist_secondary: ServiceTimeDistribution = GammaService(mean_s=75.0, cv=0.50)
    # Recomposición: 30 s (mín) / 60-90 s (media). Es el cuello de botella real.
    dist_recompose: ServiceTimeDistribution = LognormalService(mean_s=75.0, sigma_log=0.40)
    dist_passport_abc: ServiceTimeDistribution = GammaService(mean_s=18.0, cv=0.30)
    dist_passport_manual: ServiceTimeDistribution = LognormalService(mean_s=28.0, sigma_log=0.40)

    # --- Demanda -------------------------------------------------------------
    arrival_rate_pax_h: float = 600.0
    n_passengers: int = 100

    # --- Estándares ----------------------------------------------------------
    los: IATALoSStandards = field(default_factory=IATALoSStandards)
    fruin: FruinQueueStandards = field(default_factory=FruinQueueStandards)

    # --- Reproducibilidad ----------------------------------------------------
    random_seed: int = 20260920

    def __post_init__(self) -> None:
        for name in ("p_schengen", "p_uses_check_in", "p_abc_eligible",
                     "p_abc_reject", "p_secondary_search"):
            if not 0.0 <= getattr(self, name) <= 1.0:
                raise ValueError(f"{name} debe estar en [0, 1].")
        if min(self.n_check_in_desks, self.n_security_lanes,
               self.n_abc_gates, self.n_manual_booths) < 1:
            raise ValueError("Todos los recursos deben tener capacidad >= 1.")
        if len({z.key for z in self.zones}) != len(self.zones):
            raise ValueError("Zonas duplicadas en el layout.")

    @property
    def mean_interarrival_s(self) -> Seconds:
        return SECONDS_PER_HOUR / self.arrival_rate_pax_h

    @property
    def mean_trays_per_pax(self) -> float:
        return sum((i + 1) * p for i, p in enumerate(self.trays_per_pax_probs))

    @property
    def theoretical_lane_throughput_pax_h(self) -> float:
        """
        Caudal teórico por ARCO, fijado por el tiempo de banda de rayos X.

            Theta = 3600 / (bandejas_por_pax * t_bandeja)

        Con 1,55 bandejas/pax y 12,5 s/bandeja: 186 pax/h por arco, dentro del
        rango de industria de 150-200 pax/h.
        """
        return SECONDS_PER_HOUR / (self.mean_trays_per_pax
                                   * self.dist_screening_per_tray.theoretical_mean())

    @property
    def recompose_throughput_pax_h(self) -> float:
        """Caudal de la zona de recomposición por arco. Debe superar al arco."""
        return (self.recompose_positions_per_lane * SECONDS_PER_HOUR
                / self.dist_recompose.theoretical_mean())

    def with_closure(self, closures: Mapping[ZoneKey, float]) -> "TerminalConfig":
        """Devuelve una copia con clausuras parciales aplicadas por zona."""
        new_zones = tuple(
            replace(z, closure_ratio=closures.get(z.key, z.closure_ratio))
            for z in self.zones
        )
        return replace(self, zones=new_zones)


# =============================================================================
# 5. REGISTRO DE PASAJERO
# =============================================================================
@dataclass
class PassengerRecord:
    passenger_id: int
    pax_type: PaxType
    uses_check_in: bool
    passport_route: PassportRoute = PassportRoute.NOT_APPLICABLE
    secondary_search: bool = False
    n_trays: int = 0

    t_arrival_s: Seconds = 0.0
    t_exit_s: Seconds = 0.0

    wait_check_in_s: Seconds = 0.0
    service_check_in_s: Seconds = 0.0

    # Espera de seguridad = desde la entrada al serpentín hasta el acceso al
    # arco. Es la definición operativa que usa el gestor aeroportuario.
    wait_security_s: Seconds = 0.0
    service_security_s: Seconds = 0.0
    time_recompose_s: Seconds = 0.0

    wait_passport_s: Seconds = 0.0
    service_passport_s: Seconds = 0.0

    blocked_s: Seconds = 0.0   # bloqueo por aforo físico (spill-back)

    @property
    def total_wait_s(self) -> Seconds:
        return self.wait_check_in_s + self.wait_security_s + self.wait_passport_s

    @property
    def dwell_time_s(self) -> Seconds:
        return self.t_exit_s - self.t_arrival_s

    def as_row(self, los: IATALoSStandards) -> dict[str, object]:
        m = SECONDS_PER_MINUTE
        return {
            "passenger_id": self.passenger_id,
            "pax_type": self.pax_type.value,
            "uses_check_in": self.uses_check_in,
            "passport_route": self.passport_route.value,
            "secondary_search": self.secondary_search,
            "n_trays": self.n_trays,
            "t_arrival_min": self.t_arrival_s / m,
            "t_exit_min": self.t_exit_s / m,
            "wait_check_in_min": self.wait_check_in_s / m,
            "wait_security_min": self.wait_security_s / m,
            "wait_passport_min": self.wait_passport_s / m,
            "blocked_min": self.blocked_s / m,
            "service_check_in_s": self.service_check_in_s,
            "service_security_s": self.service_security_s,
            "time_recompose_s": self.time_recompose_s,
            "service_passport_s": self.service_passport_s,
            "total_wait_min": self.total_wait_s / m,
            "dwell_time_min": self.dwell_time_s / m,
            "los_check_in": los.grade_queue_time(
                ProcessStage.CHECK_IN, self.wait_check_in_s / m).value,
            "los_security": los.grade_queue_time(
                ProcessStage.SECURITY, self.wait_security_s / m).value,
            "los_passport": los.grade_queue_time(
                ProcessStage.PASSPORT, self.wait_passport_s / m).value,
        }


# =============================================================================
# MÓDULO 1 — ARQUITECTURA DEL MODELO
# =============================================================================
class AirportTerminalModel:
    """
    Modelo DES del Lado Tierra (salidas) de LPA.

    Recursos
    --------
    check_in_desks      : mostradores de facturación / bag-drop
    divest_positions    : posiciones de preparación (todas las líneas)
    screening_lanes     : arcos + RX -- servidor que fija el caudal
    recompose_positions : posiciones de recomposición
    abc_gates           : e-Gates de control automatizado de fronteras
    manual_booths       : cabinas de Policía Nacional

    Zonas
    -----
    `self.zones[ZoneKey.X]` -> `SecurityZone` con aforo físico, ocupación y
    densidad instantánea en m2/pax.
    """

    def __init__(
        self,
        config: TerminalConfig,
        env: Optional[simpy.Environment] = None,
        streams: Optional[RandomStreams] = None,
    ) -> None:
        self.config: Final[TerminalConfig] = config
        self.env: Final[simpy.Environment] = env if env is not None else simpy.Environment()
        self.streams: Final[RandomStreams] = (
            streams if streams is not None else RandomStreams(config.random_seed))

        # --- Recursos --------------------------------------------------------
        self.check_in_desks: Final[simpy.Resource] = simpy.Resource(
            self.env, capacity=config.n_check_in_desks)
        self.divest_positions: Final[simpy.Resource] = simpy.Resource(
            self.env, capacity=config.n_security_lanes * config.divest_positions_per_lane)
        self.screening_lanes: Final[simpy.Resource] = simpy.Resource(
            self.env, capacity=config.n_security_lanes)
        self.recompose_positions: Final[simpy.Resource] = simpy.Resource(
            self.env, capacity=config.n_security_lanes * config.recompose_positions_per_lane)
        self.secondary_positions: Final[simpy.Resource] = simpy.Resource(
            self.env, capacity=max(1, int(config.n_security_lanes
                                          * config.secondary_positions_per_lane)))
        self.abc_gates: Final[simpy.Resource] = simpy.Resource(
            self.env, capacity=config.n_abc_gates)
        self.manual_booths: Final[simpy.Resource] = simpy.Resource(
            self.env, capacity=config.n_manual_booths)

        # --- Zonas físicas ---------------------------------------------------
        self.zones: Final[dict[ZoneKey, SecurityZone]] = {
            spec.key: SecurityZone(self.env, spec) for spec in config.zones
        }

        # --- Acumuladores ----------------------------------------------------
        self.passengers: list[PassengerRecord] = []
        self.los_log: list[dict[str, object]] = []
        self.overcrowded_samples: int = 0
        self._pax_processes: list[simpy.Process] = []

    # ------------------------------------------------------------------ estado
    @property
    def total_security_area_m2(self) -> SquareMeters:
        return sum(z.area_m2 for z in self.zones.values())

    @property
    def total_security_occupancy(self) -> int:
        return sum(z.occupancy for z in self.zones.values())

    def clock(self) -> str:
        t = int(self.env.now)
        return f"T+{t // 3600:02d}:{(t % 3600) // 60:02d}:{t % 60:02d}"

    # =========================================================================
    # MÓDULO 2 — LÓGICA DEL AGENTE PASAJERO
    # =========================================================================
    def passenger_flow(
        self,
        env: simpy.Environment,
        passenger_id: int,
        is_schengen: bool,
    ) -> SimProcess:
        """
        Ruta lógica de un pasajero de salida.

            Llegada
              -> [Facturación si factura equipaje]
              -> Z1 Encolamiento  (aforo físico; espera del arco)
              -> Z2 Preparación   (posición de divest)
              -> Z3 Inspección    (arco/RX + eventual registro secundario)
              -> Z4 Recomposición (posición de banco)
              -> [Control de fronteras si NO-Schengen: ABC o cabina manual]
              -> Zona de embarque

        Contención: el patrón `with resource.request() as req: yield req` hace
        que el tiempo de espera sea exactamente `t_grant - t_request` y libera
        el servidor automáticamente al salir del bloque, incluso ante
        excepciones o interrupciones. El aforo zonal usa `Container.get/put`,
        de modo que el bloqueo por falta de superficie se propaga aguas arriba.
        """
        cfg, st = self.config, self.streams
        pax_type = PaxType.SCHENGEN if is_schengen else PaxType.NON_SCHENGEN

        rec = PassengerRecord(
            passenger_id=passenger_id,
            pax_type=pax_type,
            uses_check_in=bool(st.routing.random() < cfg.p_uses_check_in),
            t_arrival_s=env.now,
        )

        # ------------------------------------------------- 1) FACTURACIÓN ----
        if rec.uses_check_in:
            t0 = env.now
            with self.check_in_desks.request() as req:
                yield req
                rec.wait_check_in_s = env.now - t0
                service = cfg.dist_check_in.sample(st.check_in)
                rec.service_check_in_s = service
                yield env.timeout(service)

        # ------------------------------------- 2) FILTRO DE SEGURIDAD --------
        yield from self._security_process(env, rec)

        # ------------------------------------- 3) CONTROL DE PASAPORTES ------
        if not is_schengen:
            yield from self._passport_control(env, rec)

        rec.t_exit_s = env.now
        self.passengers.append(rec)

    def _security_process(self, env: simpy.Environment,
                          rec: PassengerRecord) -> SimProcess:
        """Recorrido zonal por el filtro, con paso de testigo entre subzonas."""
        cfg, st = self.config, self.streams
        z_queue = self.zones[ZoneKey.QUEUE]
        z_divest = self.zones[ZoneKey.DIVEST]
        z_screen = self.zones[ZoneKey.SCREENING]
        z_recomp = self.zones[ZoneKey.RECOMPOSE]

        # --- Z1: entrada al serpentín (puede bloquear aguas arriba) ----------
        t_block = env.now
        yield z_queue.enter()
        rec.blocked_s += env.now - t_block
        z_queue.confirm_entry()

        t_wait_start = env.now

        # --- Z2: posición de preparación -------------------------------------
        with self.divest_positions.request() as req_divest:
            yield req_divest
            t_block = env.now
            yield z_divest.enter()
            rec.blocked_s += env.now - t_block
            z_divest.confirm_entry()
            yield z_queue.leave()          # abandona el serpentín

            divest_time = cfg.dist_divest.sample(st.divest)
            rec.service_security_s += divest_time
            yield env.timeout(divest_time)

            # --- Z3: arco / RX (servidor que fija el caudal) -----------------
            with self.screening_lanes.request() as req_lane:
                yield req_lane
                # Espera operativa: entrada al serpentín -> acceso al arco.
                rec.wait_security_s = env.now - t_wait_start

                t_block = env.now
                yield z_screen.enter()
                rec.blocked_s += env.now - t_block
                z_screen.confirm_entry()
                yield z_divest.leave()

                # El arco/RX procesa BANDEJAS. El tiempo del pasajero es la
                # suma de los tiempos de sus bandejas.
                n_trays = 1 + int(st.screening.choice(
                    len(cfg.trays_per_pax_probs), p=cfg.trays_per_pax_probs))
                screen_time = sum(cfg.dist_screening_per_tray.sample(st.screening)
                                  for _ in range(n_trays))
                rec.n_trays = n_trays
                rec.secondary_search = bool(
                    st.secondary.random() < cfg.p_secondary_search)
                rec.service_security_s += screen_time
                yield env.timeout(screen_time)

        # --- Registro secundario: mesa lateral, con el arco ya liberado ------
        if rec.secondary_search:
            with self.secondary_positions.request() as req_sec:
                yield req_sec
                extra = cfg.dist_secondary.sample(st.secondary)
                rec.service_security_s += extra
                yield env.timeout(extra)

        # --- Z4: recomposición. Fuera del bloque de línea: el arco queda libre
        #     para el siguiente pasajero, pero la zona sigue ocupada. ---------
        with self.recompose_positions.request() as req_bench:
            yield req_bench
            t_block = env.now
            yield z_recomp.enter()
            rec.blocked_s += env.now - t_block
            z_recomp.confirm_entry()
            yield z_screen.leave()

            recomp_time = cfg.dist_recompose.sample(st.recompose)
            rec.time_recompose_s = recomp_time
            yield env.timeout(recomp_time)

        yield z_recomp.leave()

    def _passport_control(self, env: simpy.Environment,
                          rec: PassengerRecord) -> SimProcess:
        """
        Control fronterizo de salida (no-Schengen).

        Dos canales: e-Gates ABC (elegibilidad parcial) y cabinas manuales. Un
        rechazo de la e-Gate reencamina a cabina manual acumulando ambas
        esperas -- efecto de segundo orden que domina el P99 de fronteras.
        """
        cfg, st = self.config, self.streams

        if st.routing.random() < cfg.p_abc_eligible:
            t0 = env.now
            with self.abc_gates.request() as req:
                yield req
                rec.wait_passport_s += env.now - t0
                service = cfg.dist_passport_abc.sample(st.passport)
                rec.service_passport_s += service
                yield env.timeout(service)

            if st.routing.random() >= cfg.p_abc_reject:
                rec.passport_route = PassportRoute.ABC
                return
            LOGGER.debug("%s | PAX %d rechazado por e-Gate -> cabina manual",
                         self.clock(), rec.passenger_id)

        t0 = env.now
        with self.manual_booths.request() as req:
            yield req
            rec.wait_passport_s += env.now - t0
            service = cfg.dist_passport_manual.sample(st.passport)
            rec.service_passport_s += service
            yield env.timeout(service)
        rec.passport_route = PassportRoute.MANUAL

    # =========================================================================
    # GENERACIÓN DE DEMANDA
    # =========================================================================
    def passenger_source(self, env: simpy.Environment) -> SimProcess:
        """
        Proceso de llegadas de Poisson homogéneo: intervalos Exp(1/lambda).

        Sustituible por un perfil de show-up no homogéneo (curva de antelación
        respecto a la STD de cada vuelo) sin tocar el resto del modelo.
        """
        cfg = self.config
        mean_gap = cfg.mean_interarrival_s
        for pid in range(1, cfg.n_passengers + 1):
            yield env.timeout(float(self.streams.arrivals.exponential(mean_gap)))
            is_schengen = bool(self.streams.routing.random() < cfg.p_schengen)
            self._pax_processes.append(
                env.process(self.passenger_flow(env, pid, is_schengen)))

    # =========================================================================
    # MÓDULO 3 — MONITORIZACIÓN DE NIVEL DE SERVICIO
    # =========================================================================
    def monitor_los(self, env: simpy.Environment, step: Minutes = 1.0) -> SimProcess:
        """
        Proceso de fondo que muestrea el sistema cada `step` MINUTOS.

        Registra por instante t:
          * Q(t) y utilización de cada recurso.
          * Ocupación, m2/pax y LoS de Fruin de CADA subzona.
          * Bandera "Over-crowded" cuando alguna zona baja de 1.2 m2/pax
            (umbral IATA), emitida además como WARNING en el log.

        Nota metodológica: muestreo a intervalo fijo, no media temporal
        ponderada. Para KPIs de área bajo la curva usar `step` <= 0.5 min o
        instrumentar directamente los eventos de request/release.
        """
        cfg = self.config
        step_s = step * SECONDS_PER_MINUTE

        while True:
            row: dict[str, object] = {
                "t_min": env.now / SECONDS_PER_MINUTE,
                "q_check_in": len(self.check_in_desks.queue),
                "q_divest": len(self.divest_positions.queue),
                "q_screening": len(self.screening_lanes.queue),
                "q_recompose": len(self.recompose_positions.queue),
                "q_abc": len(self.abc_gates.queue),
                "q_manual": len(self.manual_booths.queue),
                "util_check_in": self.check_in_desks.count / cfg.n_check_in_desks,
                "util_screening": self.screening_lanes.count / cfg.n_security_lanes,
                "util_abc": self.abc_gates.count / cfg.n_abc_gates,
                "util_manual": self.manual_booths.count / cfg.n_manual_booths,
                "security_occupancy_total": self.total_security_occupancy,
            }

            any_overcrowded = False
            for key, zone in self.zones.items():
                density = zone.m2_per_pax
                fruin = cfg.fruin.grade(density)
                crowded = zone.occupancy > 0 and density < cfg.los.space_optimum_m2_pax
                row[f"occ_{key.value}"] = zone.occupancy
                row[f"m2pax_{key.value}"] = density
                row[f"fruin_{key.value}"] = fruin.value
                row[f"blocked_{key.value}"] = zone.blocked_upstream
                row[f"crowded_{key.value}"] = crowded

                if crowded:
                    any_overcrowded = True
                    LOGGER.warning(
                        "%s | OVER-CROWDED %s: %.2f m2/pax (< %.2f IATA) "
                        "| Fruin %s | %d pax en %.0f m2",
                        self.clock(), key.value, density,
                        cfg.los.space_optimum_m2_pax, fruin.value,
                        zone.occupancy, zone.area_m2)

            row["over_crowded"] = any_overcrowded
            if any_overcrowded:
                self.overcrowded_samples += 1

            self.los_log.append(row)
            yield env.timeout(step_s)

    # =========================================================================
    # EJECUCIÓN
    # =========================================================================
    def _lifecycle(self, env: simpy.Environment) -> SimProcess:
        """
        Proceso maestro: genera demanda y espera al vaciado del sistema.

        `monitor_los` es un bucle infinito; usar este proceso como `until` hace
        que la simulación termine exactamente cuando sale el último pasajero.
        """
        yield env.process(self.passenger_source(env))
        if self._pax_processes:
            yield simpy.events.AllOf(env, self._pax_processes)

    def run(self, until: Optional[Seconds] = None, monitor_step: Minutes = 1.0) -> None:
        self.env.process(self.monitor_los(self.env, step=monitor_step))
        lifecycle = self.env.process(self._lifecycle(self.env))
        self.env.run(until=until if until is not None else lifecycle)

    # =========================================================================
    # ANALÍTICA
    # =========================================================================
    def passengers_dataframe(self) -> pd.DataFrame:
        if not self.passengers:
            return pd.DataFrame()
        df = pd.DataFrame([p.as_row(self.config.los) for p in self.passengers])
        return df.sort_values("passenger_id").reset_index(drop=True)

    def los_dataframe(self) -> pd.DataFrame:
        return pd.DataFrame(self.los_log)

    def kpi_summary(self) -> pd.DataFrame:
        """Percentiles de espera por proceso y LoS del ADRM evaluado sobre P95."""
        df = self.passengers_dataframe()
        if df.empty:
            return pd.DataFrame()

        stages = [
            (ProcessStage.CHECK_IN, "wait_check_in_min", df["uses_check_in"]),
            (ProcessStage.SECURITY, "wait_security_min", pd.Series(True, index=df.index)),
            (ProcessStage.PASSPORT, "wait_passport_min",
             df["pax_type"] == PaxType.NON_SCHENGEN.value),
        ]
        rows: list[dict[str, object]] = []
        for stage, column, mask in stages:
            s = df.loc[mask, column].astype(float)
            if s.empty:
                continue
            p95 = float(np.percentile(s, 95))
            rows.append({
                "proceso": stage.value,
                "n_pax": int(s.size),
                "media_min": round(float(s.mean()), 2),
                "P50_min": round(float(np.percentile(s, 50)), 2),
                "P95_min": round(p95, 2),
                "P99_min": round(float(np.percentile(s, 99)), 2),
                "max_min": round(float(s.max()), 2),
                "LoS_ADRM_P95": self.config.los.grade_queue_time(stage, p95).value,
            })
        return pd.DataFrame(rows)

    def zone_summary(self) -> pd.DataFrame:
        """KPIs espaciales por subzona: densidad mínima, LoS Fruin y bloqueo."""
        los_df = self.los_dataframe()
        if los_df.empty:
            return pd.DataFrame()

        rows: list[dict[str, object]] = []
        for key, zone in self.zones.items():
            dens = los_df[f"m2pax_{key.value}"].astype(float)
            occ = los_df[f"occ_{key.value}"].astype(float)
            active = dens[occ > 0]
            min_density = float(active.min()) if not active.empty else float(dens.max())
            rows.append({
                "zona": key.value,
                "area_nom_m2": zone.spec.nominal_area_m2,
                "cierre_pct": round(zone.spec.closure_ratio * 100, 1),
                "area_util_m2": round(zone.area_m2, 1),
                "aforo_pax": zone.spec.physical_capacity_pax,
                "ocup_pico": zone.peak_occupancy,
                "m2pax_min": round(min_density, 2),
                "fruin_peor": self.config.fruin.grade(min_density).value,
                "pct_t_crowded": round(
                    100 * float(los_df[f"crowded_{key.value}"].mean()), 1),
                "bloqueo_max": int(los_df[f"blocked_{key.value}"].max()),
            })
        return pd.DataFrame(rows)


# =============================================================================
# UTILIDADES
# =============================================================================
def configure_logging(level: int = logging.INFO) -> None:
    logging.basicConfig(level=level, format="%(levelname)-8s %(message)s", force=True)


def run_single(config: TerminalConfig, monitor_step: Minutes = 1.0,
               streams: Optional[RandomStreams] = None) -> AirportTerminalModel:
    """Ejecuta UNA corrida. Para resultados presentables, usar lpa_experiment.py."""
    model = AirportTerminalModel(config, streams=streams)
    model.run(monitor_step=monitor_step)
    return model


def print_report(model: AirportTerminalModel) -> None:
    pd.set_option("display.width", 220)
    pd.set_option("display.max_columns", 50)
    cfg = model.config

    print("\n" + "=" * 92)
    print(f"CORRIDA ÚNICA — {cfg.scenario_name}")
    print("=" * 92)
    print(f"Pasajeros procesados : {len(model.passengers)}")
    print(f"Horizonte simulado   : {model.env.now / SECONDS_PER_MINUTE:.1f} min")
    print(f"Caudal teórico       : {cfg.theoretical_lane_throughput_pax_h:.0f} pax/h/línea "
          f"x {cfg.n_security_lanes} = "
          f"{cfg.theoretical_lane_throughput_pax_h * cfg.n_security_lanes:.0f} pax/h "
          f"(demanda: {cfg.arrival_rate_pax_h:.0f} pax/h)")

    print("\n--- Esperas por proceso (IATA ADRM) ---")
    print(model.kpi_summary().to_string(index=False))

    print("\n--- Nivel de Servicio espacial por subzona (Fruin + IATA) ---")
    print(model.zone_summary().to_string(index=False))

    print("\n!! Una corrida única NO es un resultado. Ejecutar lpa_experiment.py "
          "para obtener intervalos de confianza.")


def build_cli() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(description="LPA-DES: corrida única de demostración.")
    p.add_argument("--n-pax", type=int, default=100)
    p.add_argument("--rate", type=float, default=600.0, help="Llegadas (pax/h).")
    p.add_argument("--check-in-desks", type=int, default=16)
    p.add_argument("--security-lanes", type=int, default=4)
    p.add_argument("--seed", type=int, default=20260920)
    p.add_argument("--step", type=float, default=1.0, help="Paso del monitor (min).")
    p.add_argument("--closure-queue", type=float, default=0.0,
                   help="Fracción clausurada de la zona de encolamiento.")
    p.add_argument("--closure-recompose", type=float, default=0.0,
                   help="Fracción clausurada de la zona de recomposición.")
    return p


def main(argv: Optional[Sequence[str]] = None) -> None:
    args, _ = build_cli().parse_known_args(argv)
    configure_logging()

    cfg = TerminalConfig(
        scenario_name="LPA-T1 Salidas / demostración",
        n_check_in_desks=args.check_in_desks,
        n_security_lanes=args.security_lanes,
        arrival_rate_pax_h=args.rate,
        n_passengers=args.n_pax,
        random_seed=args.seed,
    )
    if args.closure_queue or args.closure_recompose:
        cfg = cfg.with_closure({
            ZoneKey.QUEUE: args.closure_queue,
            ZoneKey.RECOMPOSE: args.closure_recompose,
        })

    print_report(run_single(cfg, monitor_step=args.step))


# =============================================================================
# ROADMAP
# =============================================================================
# [x] Descomposición zonal + Fruin LoS.
# [x] Flujos aleatorios independientes (base para CRN).
# [ ] Lado Aire (OACI Anexo 14): pistas, rodaduras, stands, estela turbulenta.
# [ ] Perfil de show-up no homogéneo por vuelo desde el horario de temporada.
# [ ] Recogida de equipajes (reclaim).
# [ ] Validación contra tiempos medidos por el gestor aeroportuario.
# =============================================================================

if __name__ == "__main__":
    main()
