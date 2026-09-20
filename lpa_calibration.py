#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
===============================================================================
 LPA-CALIBRATION — Datos reales del Plan Director del Aeropuerto de Gran Canaria
===============================================================================

 FUENTE PRIMARIA
 ---------------
 Plan Director del Aeropuerto de Gran Canaria, Ministerio de Fomento /
 Dirección General de Aviación Civil / AENA. Documento sellado por la DGAC.
 Año base de los datos: 2000. Horizontes de estudio: 2005 / 2010 / 2015.

 Capítulos utilizados:
   * Cap. 3   "Estudio de la Situación Actual"  -> superficies, nº de puestos y
              tiempos de proceso MEDIDOS en LPA; capacidades resultantes.
   * Adjunto al Cap. 3 -> fórmulas IATA de capacidad empleadas por AENA.
   * Cap. 4   "Evolución Previsible de la Demanda" -> PHP de diseño.
   * Cap. 5   "Necesidades Futuras" -> superficies requeridas por horizonte.

 ADVERTENCIA CRÍTICA DE VIGENCIA
 -------------------------------
 Estos datos describen el terminal tal como estaba hacia el año 2000 y NO
 representan la configuración actual del aeropuerto. Tres razones:

   1. El terminal ha sido ampliado y remodelado desde entonces; las superficies
      y el número de mostradores, filtros y puestos de frontera han cambiado.
   2. Los tiempos de inspección de seguridad son PRE-2001. El Plan Director
      supone 6 s/bulto y 10 pax/minuto/máquina (600 pax/h/línea). Tras las
      medidas de seguridad introducidas a partir de 2001 y 2006 (líquidos,
      electrónica, calzado), el rendimiento real de una línea moderna está en
      torno a 180-240 pax/h. Usar 600 pax/h hoy sobreestimaría la capacidad
      del filtro por un factor cercano a 2,5.
   3. La previsión de demanda del Plan Director (16,89 MPA en 2015) resultó
      muy superior al tráfico realmente registrado. NO debe usarse como
      escenario de demanda; sí como referencia metodológica.

 USO CORRECTO DE ESTE MÓDULO
 ---------------------------
   * Como LÍNEA BASE DOCUMENTADA Y VERIFICABLE frente a la que validar el
     modelo DES: si el simulador, alimentado con los parámetros del Plan
     Director, reproduce las capacidades que AENA publicó, el motor está
     verificado. Eso es una prueba de verificación defendible ante un revisor.
   * Como fuente de la GEOMETRÍA y de la ESTRUCTURA de áreas funcionales.
   * NO como descripción del aeropuerto de hoy.

===============================================================================
"""

from __future__ import annotations

import math
from dataclasses import dataclass, replace
from enum import Enum
from typing import Final, Optional

import pandas as pd

from lpa_des_model import (
    GammaService,
    LognormalService,
    SquareMeters,
    TerminalConfig,
    ZoneKey,
    ZoneSpec,
)

FUENTE: Final[str] = (
    "Plan Director del Aeropuerto de Gran Canaria (Ministerio de Fomento / DGAC / AENA), "
    "Cap. 3 y Adjunto al Cap. 3. Año base 2000."
)


# =============================================================================
# 1. SEGMENTACIÓN DE TRÁFICO DEL PLAN DIRECTOR
# =============================================================================
class TrafficType(str, Enum):
    """
    El Plan Director segmenta el terminal en tres tráficos con instalaciones
    diferenciadas. Esta segmentación ES la que usa AENA, y conviene
    conservarla en lugar de la dicotomía Schengen / no-Schengen.
    """

    UE = "Union_Europea"
    INTERNACIONAL = "Internacional"
    INTERINSULAR = "Interinsular"


# =============================================================================
# 2. DATOS MEDIDOS — SALIDAS (Cap. 3, tabla de parámetros)
# =============================================================================
@dataclass(frozen=True)
class DeparturesBaseline:
    """
    Parámetros de salidas de un tipo de tráfico, tal como figuran en el
    Plan Director. Todos los campos son transcripción directa de la fuente.
    """

    traffic: TrafficType

    # --- Hall de salidas -----------------------------------------------------
    hall_area_m2: Optional[SquareMeters]
    hall_dwell_min: float
    visitors_per_pax: float

    # --- Facturación ---------------------------------------------------------
    checkin_queue_area_m2: SquareMeters
    checkin_desks: int
    bags_per_pax: float
    checkin_service_s: float          # tiempo medio de atención MEDIDO en LPA

    # --- Control de seguridad ------------------------------------------------
    xray_machines: Optional[int]

    # --- Sala de espera de embarque -----------------------------------------
    boarding_lounge_area_m2: SquareMeters
    boarding_dwell_min: float

    # --- Capacidad publicada por AENA (PHP) ----------------------------------
    capacity_checkin_php: Optional[float] = None
    capacity_security_php: Optional[float] = None
    capacity_hall_php: Optional[float] = None
    capacity_lounge_php: Optional[float] = None


# Transcripción de la tabla "SALIDAS" del Cap. 3.
# Nota de lectura: el OCR del documento escaneado deja ambiguas algunas celdas
# (marcadas como None). Deben confirmarse contra el PDF original antes de usar
# el dato en un informe.
BASELINE_DEPARTURES: Final[dict[TrafficType, DeparturesBaseline]] = {
    TrafficType.UE: DeparturesBaseline(
        traffic=TrafficType.UE,
        hall_area_m2=8239.0,
        hall_dwell_min=45.0,
        visitors_per_pax=0.1,
        checkin_queue_area_m2=4234.0,
        checkin_desks=46,
        bags_per_pax=1.35,
        checkin_service_s=66.0,          # 1 min 6 s
        xray_machines=3,
        boarding_lounge_area_m2=16412.0,
        boarding_dwell_min=40.0,
        capacity_checkin_php=1980.0,
        capacity_security_php=1800.0,
        capacity_hall_php=4035.0,
        capacity_lounge_php=11190.0,
    ),
    TrafficType.INTERNACIONAL: DeparturesBaseline(
        traffic=TrafficType.INTERNACIONAL,
        hall_area_m2=None,               # celda ambigua en el OCR: verificar
        hall_dwell_min=80.0,
        visitors_per_pax=0.2,
        checkin_queue_area_m2=1600.0,
        checkin_desks=19,
        bags_per_pax=1.39,
        checkin_service_s=120.0,         # 2 min
        xray_machines=2,
        boarding_lounge_area_m2=3013.0,
        boarding_dwell_min=50.0,
        capacity_checkin_php=518.0,
        capacity_security_php=1200.0,
        capacity_hall_php=818.0,
        capacity_lounge_php=1643.0,
    ),
    TrafficType.INTERINSULAR: DeparturesBaseline(
        traffic=TrafficType.INTERINSULAR,
        hall_area_m2=2875.0,
        hall_dwell_min=35.0,
        visitors_per_pax=0.5,
        checkin_queue_area_m2=300.0,
        checkin_desks=11,
        bags_per_pax=1.27,
        checkin_service_s=64.0,          # 1 min 4 s
        # Celda ilegible en el OCR, pero DEDUCIBLE: la capacidad publicada
        # (1.200 PHP) es exactamente 2 x 600 PHP/máquina. Confirmar en el PDF.
        xray_machines=2,
        boarding_lounge_area_m2=200.0,
        boarding_dwell_min=30.0,
        capacity_checkin_php=566.0,
        capacity_security_php=1200.0,
        capacity_hall_php=1327.0,
        capacity_lounge_php=182.0,
    ),
}

# Tiempos de frontera medidos (Cap. 3).
PASSPORT_SERVICE_DEPARTURES_S: Final[float] = 8.0    # salidas
PASSPORT_SERVICE_ARRIVALS_S: Final[float] = 15.0     # llegadas

# Parámetros del control de seguridad (Adjunto al Cap. 3).
XRAY_BAG_THROUGHPUT_PER_HOUR: Final[float] = 600.0   # bultos/hora/máquina
XRAY_INSPECTION_S: Final[float] = 6.0                # segundos por bulto


# =============================================================================
# 3. DEMANDA (Cap. 4, Tabla 4.4) — PHP de diseño
# =============================================================================
@dataclass(frozen=True)
class DesignDemand:
    """Demanda en hora punta de diseño prevista por el Plan Director."""

    year: int
    aircraft_php: int
    pax_php_total: int
    pax_php_arrivals: int
    pax_php_departures: int


DESIGN_DEMAND: Final[tuple[DesignDemand, ...]] = (
    DesignDemand(2005, 42, 6012, 3536, 3536),
    DesignDemand(2010, 47, 7148, 4205, 4205),
    DesignDemand(2015, 52, 8274, 4867, 4867),
)

# Horizonte declarado del Plan Director: 17 MPA y 52 aeronaves hora punta.
# Reiteración: previsión NO cumplida. Usar sólo como referencia metodológica.


# =============================================================================
# 4. FÓRMULAS IATA EMPLEADAS POR AENA (Adjunto al Capítulo 3)
# =============================================================================
def xray_machines_required(pax_php: float, bags_per_pax: float,
                           bag_throughput_h: float = XRAY_BAG_THROUGHPUT_PER_HOUR) -> float:
    """
    Número de aparatos de Rayos X (fórmula literal del Plan Director):

        N = (a + b) * w / y

    a+b : pasajeros/hora a inspeccionar
    w   : bultos por pasajero
    y   : capacidad de revisión = 600 bultos/hora/máquina

    Implicación: la capacidad por máquina es y/w pax/h. Con w=1,4 salen
    428 pax/h/máquina. Es un valor PRE-2001; una línea moderna rinde
    180-240 pax/h. Ver advertencia de vigencia en la cabecera.
    """
    return pax_php * bags_per_pax / bag_throughput_h


def security_capacity_php(n_machines: int, bags_per_pax: float,
                          bag_throughput_h: float = XRAY_BAG_THROUGHPUT_PER_HOUR) -> float:
    """Inversa de la anterior: capacidad en PHP de n máquinas."""
    return n_machines * bag_throughput_h / bags_per_pax


def passport_booths_required(pax_php: float, service_s: float,
                             contingency: float = 1.1) -> float:
    """
    Puestos de control de pasaportes (fórmula literal):

        N = (a + b) * t / 60 * 1,1

    con t en MINUTOS y un coeficiente de contingencia de 1,1.
    """
    return pax_php * (service_s / 60.0) / 60.0 * contingency


def boarding_lounge_area_m2(largest_aircraft_seats: int,
                            space_per_pax: float = 1.4) -> SquareMeters:
    """
    Sala de embarque (fórmula literal):  A = m * s

    m : asientos del avión de mayor tamaño que use el preembarque
    s : 1,4 m2/pax
    """
    return largest_aircraft_seats * space_per_pax


def baggage_reclaim_area_m2(pax_php: float, dwell_min: float,
                            space_per_pax: float = 2.0) -> SquareMeters:
    """
    Sala de recogida de equipajes (fórmula literal, excluidos hipódromos):

        A = e * w * s / 60

    e : pax/hora que terminan vuelo
    w : tiempo medio de estancia (UE 25 min, Int 30 min, Interinsular 20 min)
    s : 2 m2/pax
    """
    return pax_php * dwell_min * space_per_pax / 60.0


def arrivals_hall_area_m2(pax_php: float, visitors_per_pax: float,
                          pax_dwell_min: float = 5.0,
                          visitor_dwell_min: float = 30.0,
                          space_per_person: float = 2.0,
                          contingency: float = 1.1) -> SquareMeters:
    """
    Vestíbulo de llegadas (fórmula literal):

        A = s * (w*d/60 + z*d*o/60) * 1,1

    w : ocupación del pasajero = 5 min
    z : ocupación del visitante = 30 min
    o : visitantes por pasajero (UE 0,1 / Int 0,2 / Interinsular 0,5)
    s : 2 m2/persona
    """
    return space_per_person * (
        pax_dwell_min * pax_php / 60.0
        + visitor_dwell_min * pax_php * visitors_per_pax / 60.0
    ) * contingency


def customs_booths_required(pax_php: float,
                            checked_fraction: float = 0.25,
                            service_min: float = 1.5) -> float:
    """Puestos de aduana (fórmula literal): N = e * f * t / 60."""
    return pax_php * checked_fraction * service_min / 60.0


def checkin_capacity_php(n_desks: int, service_s: float,
                         utilisation: float = 1.0) -> float:
    """
    Capacidad de facturación por caudal de servidores.

    NOTA: el Plan Director publica 1.980 PHP para 46 mostradores UE. Con
    66 s/pax el caudal bruto sería 46*3600/66 = 2.509 pax/h, de modo que la
    cifra publicada equivale a una utilización efectiva del 79%. La fórmula
    exacta de área de colas del documento quedó parcialmente ilegible en el
    OCR y debe transcribirse del PDF original antes de usarse.
    """
    return n_desks * 3600.0 / service_s * utilisation


# =============================================================================
# 5. PUENTE HACIA EL MODELO DES
# =============================================================================
def zone_layout_from_plan_director(
    baseline: DeparturesBaseline,
    security_area_m2: Optional[SquareMeters] = None,
) -> tuple[ZoneSpec, ...]:
    """
    Construye la geometría zonal del filtro a partir del Plan Director.

    El Plan Director da la superficie de la ZONA DE COLAS DE FACTURACIÓN, del
    hall y de la sala de embarque, pero NO desglosa la superficie del control
    de seguridad: sólo da el número de aparatos de Rayos X. Esa sigue siendo
    la brecha G4 del anexo matemático, ahora reducida a una sola incógnita.

    Mientras no se disponga del dato real, `security_area_m2` se reparte entre
    las cuatro subzonas con el criterio documentado abajo. Ese reparto es una
    HIPÓTESIS DEL MODELO, no un dato de AENA, y así debe declararse.
    """
    if security_area_m2 is None:
        # Hipótesis de trabajo: 45 m2 por línea de inspección, proporción
        # habitual en filtros con serpentín, preparación y recomposición.
        machines = baseline.xray_machines or 1
        security_area_m2 = 45.0 * machines * 4

    # Reparto hipotético entre subzonas (suma = 1,0).
    shares = {
        ZoneKey.QUEUE: 0.50,
        ZoneKey.DIVEST: 0.17,
        ZoneKey.SCREENING: 0.12,
        ZoneKey.RECOMPOSE: 0.21,
    }
    return tuple(
        ZoneSpec(key, nominal_area_m2=security_area_m2 * share)
        for key, share in shares.items()
    )


def terminal_config_from_plan_director(
    traffic: TrafficType,
    pax_php: float,
    n_passengers: int = 1200,
    modern_security: bool = True,
    security_area_m2: Optional[SquareMeters] = None,
    scenario_name: Optional[str] = None,
) -> TerminalConfig:
    """
    Instancia un escenario DES con los parámetros medidos en LPA.

    `modern_security` es la decisión más importante de esta función:

      * False -> se usan los tiempos del Plan Director (6 s/bulto,
        ~428 pax/h/línea). Sirve para REPRODUCIR las capacidades publicadas
        por AENA y así verificar el motor de simulación.
      * True  -> se sustituye por un tiempo de arco/RX contemporáneo
        (15 s/pax, ~240 pax/h/línea). Es lo que debe usarse para cualquier
        conclusión sobre el aeropuerto de hoy.

    Los tiempos de facturación y de frontera se mantienen en ambos casos:
    son procesos cuya duración no ha cambiado de forma comparable.
    """
    base = BASELINE_DEPARTURES[traffic]

    if modern_security:
        screening = GammaService(mean_s=15.0, cv=0.30)
        tag = "seguridad contemporánea (15 s/pax)"
    else:
        # Tiempo por pasajero derivado de la fórmula del Plan Director:
        # 3600 / (600 / bultos_por_pax) segundos.
        pd_service_s = 3600.0 / (XRAY_BAG_THROUGHPUT_PER_HOUR / base.bags_per_pax)
        screening = GammaService(mean_s=pd_service_s, cv=0.30)
        tag = f"seguridad Plan Director ({pd_service_s:.1f} s/pax)"

    name = scenario_name or f"LPA {traffic.value} — {pax_php:.0f} PHP — {tag}"

    return TerminalConfig(
        scenario_name=name,
        n_check_in_desks=base.checkin_desks,
        n_security_lanes=base.xray_machines or 1,
        n_abc_gates=4,
        n_manual_booths=3,
        zones=zone_layout_from_plan_director(base, security_area_m2),
        # El Plan Director no publica la fracción de pasajeros que factura;
        # se mantiene la hipótesis del modelo hasta obtener el dato real.
        p_uses_check_in=0.55,
        p_schengen=1.0 if traffic is not TrafficType.INTERNACIONAL else 0.0,
        dist_check_in=LognormalService(mean_s=base.checkin_service_s, sigma_log=0.45),
        dist_screening=screening,
        dist_passport_abc=GammaService(mean_s=PASSPORT_SERVICE_DEPARTURES_S, cv=0.30),
        dist_passport_manual=LognormalService(
            mean_s=PASSPORT_SERVICE_DEPARTURES_S * 2.0, sigma_log=0.40),
        arrival_rate_pax_h=pax_php,
        n_passengers=n_passengers,
    )


# =============================================================================
# 6. VERIFICACIÓN CONTRA LAS CAPACIDADES PUBLICADAS
# =============================================================================
# -----------------------------------------------------------------------------
# INCONSISTENCIA DETECTADA EN LA FUENTE
# -----------------------------------------------------------------------------
# El Adjunto al Cap. 3 define el número de máquinas de Rayos X como
#       N = (a + b) * w / y     con y = 600 BULTOS/hora
# lo que implica una capacidad de y/w PAX/hora por máquina (444 pax/h para
# w=1,35). Sin embargo, la tabla de capacidades del Cap. 3 publica 1.800 PHP
# para 3 máquinas (UE) y 1.200 PHP para 2 máquinas (Internacional): es decir,
# exactamente 600 PAX/hora por máquina, ignorando el número de bultos.
#
# Las dos cifras del propio documento no son consistentes entre sí. La nota
# "6 seg (10 PAX/MINUTO/MAQUINA)" de la tabla de parámetros confirma que para
# las capacidades se usó 600 pax/h, no 600 bultos/h.
#
# Esto NO invalida el Plan Director, pero sí obliga a declarar cuál de los dos
# criterios se adopta. El modelo adopta el criterio conservador (por bultos),
# y en cualquier caso ambos quedan muy por encima del rendimiento real de una
# línea contemporánea.
# -----------------------------------------------------------------------------
SECURITY_CAPACITY_CRITERION_NOTE: Final[str] = (
    "Fórmula del Adjunto: 600 bultos/h/máquina -> 444 pax/h (UE). "
    "Tabla de capacidades: 600 pax/h/máquina. Inconsistencia interna de la fuente."
)


def analytic_capacity_table() -> pd.DataFrame:
    """
    Recalcula, con las fórmulas del propio Plan Director, las capacidades que
    el documento publica. Si las cifras no cuadran, el error está en nuestra
    transcripción, no en AENA: es la primera comprobación que hay que pasar.
    """
    rows: list[dict[str, object]] = []
    for traffic, b in BASELINE_DEPARTURES.items():
        if b.xray_machines is None:
            recalculada = float("nan")
        else:
            recalculada = security_capacity_php(b.xray_machines, b.bags_per_pax)

        rows.append({
            "trafico": traffic.value,
            "maquinas_RX": b.xray_machines,
            "bultos_pax": b.bags_per_pax,
            "seguridad_PHP_publicada": b.capacity_security_php,
            "seg_PHP_por_bultos": round(recalculada, 0),
            "seg_PHP_por_pax_600": (b.xray_machines * 600.0
                                    if b.xray_machines else float("nan")),
            "mostradores": b.checkin_desks,
            "t_facturacion_s": b.checkin_service_s,
            "facturacion_PHP_publicada": b.capacity_checkin_php,
            "facturacion_PHP_caudal_bruto": round(
                checkin_capacity_php(b.checkin_desks, b.checkin_service_s), 0),
            "area_colas_facturacion_m2": b.checkin_queue_area_m2,
        })
    return pd.DataFrame(rows)


def demand_table() -> pd.DataFrame:
    """Demanda de diseño del Plan Director (Cap. 4)."""
    return pd.DataFrame([{
        "anio": d.year,
        "aeronaves_HP": d.aircraft_php,
        "pax_HP_total": d.pax_php_total,
        "pax_HP_llegadas": d.pax_php_arrivals,
        "pax_HP_salidas": d.pax_php_departures,
    } for d in DESIGN_DEMAND])


def modern_vs_plan_director_security() -> pd.DataFrame:
    """
    Cuantifica el desfase entre la hipótesis de seguridad del Plan Director y
    el rendimiento de una línea contemporánea. Es el argumento central para
    justificar por qué el modelo NO puede usar los 600 bultos/hora de 2001.
    """
    rows: list[dict[str, object]] = []
    for traffic, b in BASELINE_DEPARTURES.items():
        pd_php_line = XRAY_BAG_THROUGHPUT_PER_HOUR / b.bags_per_pax
        modern_php_line = 3600.0 / 15.0
        rows.append({
            "trafico": traffic.value,
            "PHP_por_linea_Plan_Director": round(pd_php_line, 0),
            "PHP_por_linea_contemporanea": round(modern_php_line, 0),
            "factor_sobreestimacion": round(pd_php_line / modern_php_line, 2),
            "lineas_equivalentes_hoy": (
                round(b.xray_machines * pd_php_line / modern_php_line, 1)
                if b.xray_machines else None),
        })
    return pd.DataFrame(rows)


# =============================================================================
# 7. INFORME
# =============================================================================
def main() -> None:
    pd.set_option("display.width", 220)
    pd.set_option("display.max_columns", 40)

    print("=" * 96)
    print("CALIBRACIÓN LPA — datos del Plan Director")
    print(f"Fuente: {FUENTE}")
    print("=" * 96)

    print("\n--- Demanda de diseño (Cap. 4, Tabla 4.4) ---")
    print(demand_table().to_string(index=False))

    print("\n--- Verificación de las capacidades publicadas ---")
    print(analytic_capacity_table().to_string(index=False))
    print("\n[!] INCONSISTENCIA EN LA FUENTE:")
    print("    " + SECURITY_CAPACITY_CRITERION_NOTE)
    print("    La columna 'por_pax_600' reproduce exactamente la cifra publicada;")
    print("    la columna 'por_bultos' aplica la fórmula del Adjunto. No coinciden.")

    print("\n--- Desfase de la hipótesis de seguridad (2001 vs hoy) ---")
    print(modern_vs_plan_director_security().to_string(index=False))
    print("\nEste factor es la razón por la que el modelo NO puede adoptar los")
    print("600 bultos/hora del Plan Director para conclusiones actuales.")

    print("\n--- Superficies reales disponibles para el modelo (salidas) ---")
    for traffic, b in BASELINE_DEPARTURES.items():
        print(f"  {traffic.value:<16} colas facturación {b.checkin_queue_area_m2:>8.0f} m2 "
              f"| sala embarque {b.boarding_lounge_area_m2:>8.0f} m2 "
              f"| hall {b.hall_area_m2 if b.hall_area_m2 else 'VERIFICAR':>9}")

    print("\n[!] La superficie del CONTROL DE SEGURIDAD no figura en el Plan")
    print("    Director: sólo el número de aparatos de Rayos X. Sigue siendo la")
    print("    única incógnita geométrica del modelo.")


if __name__ == "__main__":
    main()
