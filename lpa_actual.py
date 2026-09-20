#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
===============================================================================
 LPA-ACTUAL — Línea base contemporánea del Aeropuerto de Gran Canaria
===============================================================================

 FUENTES
 -------
 [TFM]  "Planificación estratégica y análisis de la capacidad actual y futura
        del Aeropuerto de Gran Canaria" (2020). Datos operativos de 2018,
        superficies medidas sobre los planos de planta del aeropuerto.

 [DORA3] Aena S.M.E., S.A. — Información privilegiada de 18/02/2026:
        propuesta del Tercer Documento de Regulación Aeroportuaria (DORA III),
        ejercicios 2027-2031, aprobada por el Consejo de Administración y
        remitida a la DGAC y a la CNMC. Pendiente de aprobación.

 [PD]   Plan Director del Aeropuerto de Gran Canaria (año base 2000).
        Ver `lpa_calibration.py`.

 RELACIÓN ENTRE LAS TRES FUENTES
 -------------------------------
 El Plan Director fijó su horizonte de "Desarrollo Previsible" en 17 millones
 de pasajeros anuales, y su previsión situaba esa cifra hacia 2015. El DORA III
 estima que LPA alcanzará 17,0 MPA en 2031: dieciséis años más tarde que lo
 previsto, y con el Plan Director nunca revisado pese a que el RD 2591/1998
 art. 7 obliga a actualizarlo al menos cada ocho años.

 El TFM aporta la fotografía intermedia (2018) con superficies medidas sobre
 planos y el inventario real de instalaciones. Es la mejor descripción
 disponible del terminal tal como opera hoy.

===============================================================================
"""

from __future__ import annotations

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

# =============================================================================
# 1. GEOMETRÍA Y EQUIPAMIENTO DEL TERMINAL (TFM, datos 2018)
# =============================================================================
TERMINAL_LENGTH_M: Final[float] = 450.0
TERMINAL_WIDTH_MIN_M: Final[float] = 75.0
TERMINAL_WIDTH_MAX_M: Final[float] = 120.0
# Niveles: sótano (instalaciones), planta 0 (llegadas), planta 1 (salidas,
# +5,85 m), planta 2 (+10,85 m).

DEPARTURES_HALL_AREA_M2: Final[SquareMeters] = 13000.0   # medido sobre planos P1
BOARDING_INTL_NORTH_M2: Final[SquareMeters] = 2000.0     # T11 ~800 + T12 ~1200
BOARDING_REST_M2: Final[SquareMeters] = 7900.0


@dataclass(frozen=True)
class CheckInArea:
    """Zona de facturación con su rango de mostradores y tráfico asignado."""

    name: str
    desk_range: str
    desks: int
    floor: int
    traffic: str


CHECK_IN_AREAS: Final[tuple[CheckInArea, ...]] = (
    CheckInArea("A", "101-118", 18, 1, "Interinsular y Marruecos (Binter, Canaryfly)"),
    CheckInArea("B y C", "201-234", 34, 1, "UE y Escandinavia (Groundforce, Iberia)"),
    CheckInArea("D", "301-352", 52, 1, "Internacional (requiere escáner de seguridad)"),
    CheckInArea("Ryanair", "401-406", 6, 0, "Mixto, en planta de llegadas"),
)

TOTAL_CHECKIN_DESKS: Final[int] = sum(a.desks for a in CHECK_IN_AREAS)  # 110


@dataclass(frozen=True)
class SecurityCheckpoint:
    """Zona de control de seguridad con su número de unidades de inspección."""

    name: str
    inspection_units: int
    notes: str


SECURITY_CHECKPOINTS: Final[tuple[SecurityCheckpoint, ...]] = (
    SecurityCheckpoint("Filtros Norte", 4, "Incluye Fast Lane preferente"),
    SecurityCheckpoint("Filtros Central", 16, "Zona principal"),
)

TOTAL_INSPECTION_UNITS: Final[int] = sum(c.inspection_units
                                         for c in SECURITY_CHECKPOINTS)   # 20
# El TFM describe las 20 unidades como "10 filtros dobles".
TOTAL_DOUBLE_LANES: Final[int] = 10

PASSPORT_BOOTHS_P1: Final[int] = 8      # repartidos por la zona de embarque

# Puertas de embarque: B01-B06 (dique Norte), C01-C35 (central), D31-D35 (Sur).
# Llegadas: cintas 11-16 (nacional) y 23-25, 30-36, 41-43 (internacional).


# =============================================================================
# 2. EL DATO DEL GESTOR
# =============================================================================
AENA_FILTER_THROUGHPUT_PAX_H: Final[float] = 350.0
"""
Cifra media con la que trabaja AENA para dimensionar filtros de seguridad,
según el TFM: 350 pax/hora por filtro.

Es el parámetro más valioso de todo el material reunido, porque procede del
GESTOR AEROPORTUARIO y no de un manual. Cualquier resultado del modelo sobre
el filtro debería contrastarse contra esta cifra: si el simulador predice un
caudal muy distinto con el mismo número de filtros, hay que explicar por qué
antes de presentar nada.

Contraste con las otras fuentes:
  * Plan Director (2000): 600 bultos/h -> 444 pax/h por máquina.
  * Fórmula PAX10 con t3 = 8 s:          450 pax/h por control.
  * AENA (operación real):               350 pax/h por filtro.
  * Rango de industria contemporáneo:    180-240 pax/h por línea.

La cifra de AENA se sitúa por debajo de las teóricas y por encima del rango
de industria, lo que es coherente: un "filtro" de AENA agrupa más de una
unidad de inspección (el TFM describe 20 unidades como 10 filtros dobles).
"""

SECURITY_SERVICE_S_TFM: Final[float] = 8.0   # t3 en la fórmula PAX10 del TFM


# =============================================================================
# 3. DEMANDA REAL EN HORA PUNTA (TFM, datos 2018)
# =============================================================================
@dataclass(frozen=True)
class PeakHourDemand2018:
    """Pasajeros Hora Punta de 2018, segregados por sentido."""

    departures: int = 2684
    arrivals: int = 2146
    total: int = 3878

    # Desglose de salidas relevante para frontera (TFM, cap. 3.2.1).
    intl_departures: int = 153
    ue_non_schengen_departures: int = 587

    @property
    def passport_departures(self) -> int:
        """Pasajeros de salida que cruzan control de fronteras."""
        return self.intl_departures + self.ue_non_schengen_departures  # 740


PHP_2018: Final[PeakHourDemand2018] = PeakHourDemand2018()


# Tiempos medios de facturación empleados en el TFM (s/pax).
CHECKIN_SERVICE_S: Final[dict[str, float]] = {
    "UE": 76.0,             # 1 min 16 s
    "Internacional": 120.0,  # 2 min
    "Interinsular": 60.0,    # 1 min
}

PASSPORT_SERVICE_S: Final[float] = 8.0   # salidas, heredado del Plan Director


# =============================================================================
# 4. DORA III (2027-2031)
# =============================================================================
DORA3_LPA_PAX_2031_MILLIONS: Final[float] = 17.0
DORA3_NETWORK_INVESTMENT_MEUR: Final[float] = 9991.0
DORA3_NETWORK_PAX_2027_MILLIONS: Final[float] = 329.0
DORA3_NETWORK_PAX_2031_MILLIONS: Final[float] = 347.0
DORA3_WACC_PRETAX: Final[float] = 0.09
DORA3_IMAP_EUR_PAX: Final[dict[int, float]] = {
    2027: 10.92, 2028: 11.34, 2029: 11.77, 2030: 12.22, 2031: 12.69,
}

DORA3_MAJOR_TERMINAL_WORKS: Final[tuple[str, ...]] = (
    "Adolfo Suárez Madrid-Barajas", "Josep Tarradellas Barcelona-El Prat",
    "Málaga-Costa del Sol", "Alicante-Elche Miguel Hernández", "Tenerife Sur",
    "Valencia", "Ibiza", "César Manrique-Lanzarote", "Bilbao",
    "Tenerife Norte-Ciudad de La Laguna", "Menorca", "Melilla",
)
"""
Aeropuertos con obras mayores de área terminal enumerados en el DORA III.

OBSERVACIÓN: Gran Canaria NO figura en esta lista, pese a ser el sexto
aeropuerto de la red por tráfico estimado en 2031 (17,0 MPA) y pese a que el
propio DORA III reconoce que el crecimiento de varios aeropuertos está
limitado por la capacidad de la infraestructura existente.

Esta ausencia es una observación sobre el contenido del documento, no una
valoración: puede responder a que LPA ya ejecutó su ampliación, a que sus
actuaciones se encuadran en otra categoría de inversión, o a criterios de
priorización no detallados en la información privilegiada. Debe verificarse
contra el documento completo del DORA III cuando se publique.
"""

DORA3_CAPACITY_COMMITMENT: Final[str] = (
    "During the planned works on terminal buildings, current capacity will be "
    "maintained, and this fact represents a firm commitment to all users in an "
    "environment of highly congested infrastructure."
)
"""
Compromiso literal del DORA III. Es directamente relevante para este proyecto:
convierte "mantener la capacidad durante las obras" en un compromiso regulatorio
verificable, y por tanto en algo que un modelo de simulación puede auditar.
"""


def dora3_implied_php(
    annual_pax_millions: float = DORA3_LPA_PAX_2031_MILLIONS,
    php_ratio_2018: Optional[float] = None,
) -> float:
    """
    Escala el PHP de 2018 al tráfico anual estimado por el DORA III para 2031.

    Método: se asume que la relación PHP/pasajeros anuales se mantiene. Es una
    hipótesis fuerte -- el factor de hora punta suele DECRECER al crecer el
    tráfico, porque la demanda se reparte mejor a lo largo del día -- así que
    esta cifra es un LÍMITE SUPERIOR, no una previsión.

    Requiere el tráfico anual de 2018 de LPA para calcular el ratio. Mientras
    no se disponga del dato verificado, la función exige que se lo pasen.
    """
    if php_ratio_2018 is None:
        raise ValueError(
            "Falta el tráfico anual de LPA en 2018 para calcular el ratio "
            "PHP/MPA. Obtenerlo de las estadísticas publicadas por Aena y "
            "pasarlo explícitamente; no debe estimarse de memoria."
        )
    return php_ratio_2018 * annual_pax_millions


# =============================================================================
# 5. CONFIGURACIÓN DES DE LA LÍNEA BASE 2018
# =============================================================================
def security_zone_layout(total_area_m2: SquareMeters) -> tuple[ZoneSpec, ...]:
    """
    Reparto de la superficie del filtro entre las cuatro subzonas.

    ADVERTENCIA: ni el Plan Director, ni el TFM, ni el DORA III publican la
    superficie de la zona de control de seguridad. El TFM da el vestíbulo de
    salidas completo (13.000 m2, que incluye facturación y circulación) y el
    número de unidades de inspección, pero no el recinto del filtro.

    Este reparto es por tanto una HIPÓTESIS DEL MODELO. La respuesta correcta
    no es afinar la hipótesis, sino barrer `total_area_m2` sobre todo el rango
    plausible y comprobar si la conclusión depende del valor.
    """
    shares = {
        ZoneKey.QUEUE: 0.50,
        ZoneKey.DIVEST: 0.17,
        ZoneKey.SCREENING: 0.12,
        ZoneKey.RECOMPOSE: 0.21,
    }
    return tuple(
        ZoneSpec(key, nominal_area_m2=total_area_m2 * share)
        for key, share in shares.items()
    )


def config_lpa_2018(
    security_area_m2: SquareMeters = 900.0,
    n_lanes: int = TOTAL_DOUBLE_LANES,
    n_check_in_desks: int = TOTAL_CHECKIN_DESKS,
    pax_php: float = float(PHP_2018.departures),
    n_passengers: int = 2684,
    lane_service_s: float = 15.0,
    scenario_name: Optional[str] = None,
) -> TerminalConfig:
    """
    Escenario DES de la línea base 2018, con el inventario real del terminal.

    Valores por defecto, todos trazables salvo donde se indica:
      * 110 mostradores de facturación (TFM, suma de las cuatro zonas).
      * 10 filtros dobles / 20 unidades de inspección (TFM).
      * 8 puestos de control de pasaportes en planta 1 (TFM).
      * 2.684 pax/h de salidas en hora punta (TFM, datos 2018).
      * Tiempo de facturación 76 s (UE, el tráfico dominante).
      * `security_area_m2` NO es un dato: es el parámetro a barrer.
    """
    # Fracción de pasajeros de salida que cruza frontera: 740 de 2.684.
    p_schengen = 1.0 - PHP_2018.passport_departures / PHP_2018.departures

    return TerminalConfig(
        scenario_name=scenario_name or (
            f"LPA 2018 — {n_lanes} filtros, {security_area_m2:.0f} m2 de filtro"),
        n_check_in_desks=n_check_in_desks,
        n_security_lanes=n_lanes,
        n_abc_gates=4,
        n_manual_booths=PASSPORT_BOOTHS_P1 - 4,
        zones=security_zone_layout(security_area_m2),
        p_schengen=p_schengen,
        p_uses_check_in=0.55,   # hipótesis: no publicada en ninguna fuente
        dist_check_in=LognormalService(mean_s=CHECKIN_SERVICE_S["UE"], sigma_log=0.45),
        dist_screening=GammaService(mean_s=lane_service_s, cv=0.30),
        dist_passport_abc=GammaService(mean_s=PASSPORT_SERVICE_S, cv=0.30),
        dist_passport_manual=LognormalService(mean_s=PASSPORT_SERVICE_S * 2.5,
                                              sigma_log=0.40),
        arrival_rate_pax_h=pax_php,
        n_passengers=n_passengers,
    )


# =============================================================================
# 6. CONTRASTE DE CAUDALES DEL FILTRO
# =============================================================================
def security_throughput_comparison() -> pd.DataFrame:
    """
    Compara las cuatro estimaciones disponibles del caudal del filtro.

    Es la tabla que hay que enseñar antes que ninguna otra: muestra que el
    parámetro más determinante del modelo tiene cuatro valores distintos según
    la fuente, y obliga a elegir uno de forma explícita y justificada.
    """
    n = TOTAL_DOUBLE_LANES
    rows = [
        {
            "fuente": "Plan Director 2000 (600 bultos/h, w=1,4)",
            "pax_h_por_filtro": round(600.0 / 1.4, 0),
            "capacidad_total_pax_h": round(n * 600.0 / 1.4, 0),
        },
        {
            "fuente": "Fórmula PAX10, t3 = 8 s (TFM)",
            "pax_h_por_filtro": round(600.0 / SECURITY_SERVICE_S_TFM * 6, 0),
            "capacidad_total_pax_h": round(n * 600.0 / SECURITY_SERVICE_S_TFM * 6, 0),
        },
        {
            "fuente": "AENA, cifra de gestión operativa",
            "pax_h_por_filtro": AENA_FILTER_THROUGHPUT_PAX_H,
            "capacidad_total_pax_h": n * AENA_FILTER_THROUGHPUT_PAX_H,
        },
        {
            "fuente": "Rango de industria contemporáneo (15 s/pax en arco)",
            "pax_h_por_filtro": 240.0,
            "capacidad_total_pax_h": n * 240.0,
        },
    ]
    df = pd.DataFrame(rows)
    df["ratio_sobre_demanda_2018"] = (
        df["capacidad_total_pax_h"] / PHP_2018.departures).round(2)
    return df


def inventory_table() -> pd.DataFrame:
    """Inventario del terminal según el TFM."""
    rows = [{"zona": a.name, "mostradores": a.desk_range, "n": a.desks,
             "planta": a.floor, "trafico": a.traffic} for a in CHECK_IN_AREAS]
    return pd.DataFrame(rows)


def main() -> None:
    pd.set_option("display.width", 220)
    pd.set_option("display.max_columns", 30)

    print("=" * 94)
    print("LÍNEA BASE CONTEMPORÁNEA DE LPA")
    print("Fuentes: TFM (datos 2018, superficies sobre planos) + DORA III (2026)")
    print("=" * 94)

    print(f"\nTerminal: {TERMINAL_LENGTH_M:.0f} m de largo x "
          f"{TERMINAL_WIDTH_MIN_M:.0f}-{TERMINAL_WIDTH_MAX_M:.0f} m de ancho, 4 niveles")
    print(f"Vestíbulo de salidas (planta 1): {DEPARTURES_HALL_AREA_M2:,.0f} m2")
    print(f"Salas de embarque: {BOARDING_INTL_NORTH_M2:,.0f} m2 (Internacional Norte) "
          f"+ {BOARDING_REST_M2:,.0f} m2 (resto)")

    print("\n--- Facturación ---")
    print(inventory_table().to_string(index=False))
    print(f"  TOTAL: {TOTAL_CHECKIN_DESKS} mostradores")

    print("\n--- Control de seguridad ---")
    for c in SECURITY_CHECKPOINTS:
        print(f"  {c.name:<18} {c.inspection_units:>3} unidades   ({c.notes})")
    print(f"  TOTAL: {TOTAL_INSPECTION_UNITS} unidades = {TOTAL_DOUBLE_LANES} filtros dobles")
    print(f"  Control de pasaportes: {PASSPORT_BOOTHS_P1} puestos en planta 1")

    print("\n--- Demanda en hora punta (2018) ---")
    print(f"  Salidas {PHP_2018.departures} pax/h | Llegadas {PHP_2018.arrivals} pax/h "
          f"| Total {PHP_2018.total} pax/h")
    print(f"  De salidas, cruzan frontera: {PHP_2018.passport_departures} pax/h "
          f"({100*PHP_2018.passport_departures/PHP_2018.departures:.0f}%)")

    print("\n--- CAUDAL DEL FILTRO: cuatro fuentes, cuatro cifras ---")
    print(security_throughput_comparison().to_string(index=False))
    print("\n  Este es el parámetro más determinante del modelo. Hay que elegir")
    print("  uno de forma explícita; el modelo adopta el más conservador y")
    print("  contrasta el resultado contra la cifra de AENA (350 pax/h/filtro).")

    print("\n--- DORA III (2027-2031), pendiente de aprobación ---")
    print(f"  Tráfico estimado LPA en 2031: {DORA3_LPA_PAX_2031_MILLIONS} millones de pax")
    print(f"  Inversión regulada en red: {DORA3_NETWORK_INVESTMENT_MEUR:,.0f} M EUR | WACC {DORA3_WACC_PRETAX:.0%}")
    print(f"  IMAP: {DORA3_IMAP_EUR_PAX[2027]:.2f} EUR/pax (2027) -> "
          f"{DORA3_IMAP_EUR_PAX[2031]:.2f} EUR/pax (2031)")
    print(f"\n  Gran Canaria NO figura entre los {len(DORA3_MAJOR_TERMINAL_WORKS)} "
          f"aeropuertos con obras mayores de área terminal.")
    print(f"\n  Compromiso literal del DORA III:\n    \"{DORA3_CAPACITY_COMMITMENT}\"")

    print("\n" + "=" * 94)
    print("CONVERGENCIA DE LAS TRES FUENTES")
    print("=" * 94)
    print("  Plan Director (2001): horizonte de Desarrollo Previsible = 17 MPA,")
    print("                        previsto hacia 2015. Nunca revisado.")
    print("  DORA III (2026):      LPA alcanzará 17,0 MPA en 2031.")
    print("  -> El aeropuerto llegará al techo de su planificación 16 años tarde,")
    print("     con el instrumento de planificación sin actualizar pese a que el")
    print("     RD 2591/1998 art. 7 obliga a revisarlo al menos cada 8 años.")

    print("\n[!] ÚNICO DATO GEOMÉTRICO QUE SIGUE FALTANDO:")
    print("    la superficie del recinto de control de seguridad. No figura en")
    print("    ninguna de las tres fuentes. Se resuelve por barrido de")
    print("    sensibilidad, no por estimación.")


if __name__ == "__main__":
    main()
