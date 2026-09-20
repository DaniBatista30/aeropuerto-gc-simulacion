#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
===============================================================================
 LPA-DEMANDA — Escenarios de demanda y conversión a hora punta
===============================================================================

 PRINCIPIO METODOLÓGICO
 ----------------------
 Este modelo NO predice la demanda. Un simulador de eventos discretos no tiene
 nada que decir sobre cuántos pasajeros volarán a Gran Canaria en 2031: eso
 depende de la economía, la conectividad aérea, la planta alojativa y la
 política turística.

 La demanda entra SIEMPRE desde fuera y citada. El modelo responde a la
 pregunta que sí le corresponde: dada esa demanda, ¿aguanta la terminal, dónde
 se rompe primero y con cuánto margen?

 Confundir ambas cosas es el error documentado del Plan Director, que proyectó
 16,89 MPA para 2015 y falló por unos 6 millones.

 FUENTES DE DEMANDA
 ------------------
 [DORA3] Aena, DORA III (propuesta, feb. 2026): 17,0 MPA para LPA en 2031.
         Es la cifra OFICIAL del gestor y la que debe encabezar el estudio.
 [TFM]   Extrapolación propia (2020) sobre serie 1999-2019: escenarios
         pesimista / normal / optimista para 2026, 2031 y 2036.
 [DORA1] DORA 2017-2021: capacidad declarada de LPA.

===============================================================================
"""

from __future__ import annotations

from dataclasses import dataclass, replace
from enum import Enum
from typing import Final, Optional

import pandas as pd

from lpa_actual import PHP_2018, config_lpa_2018

# =============================================================================
# 1. TRÁFICO HISTÓRICO OBSERVADO
# =============================================================================
LPA_PAX_2019_TOTAL_M: Final[float] = 13.2
"""Pasajeros totales de LPA en 2019, incluyendo tránsitos (TFM, datos Aena)."""

LPA_PAX_2019_REGULAR_M: Final[float] = 11.3
"""Tráfico regular sin tránsitos en 2019 (TFM, datos Aena)."""

# La hora punta del TFM corresponde a 2018 y el tráfico anual a 2019. La
# diferencia de un año introduce un sesgo pequeño pero debe declararse.
PHP_REFERENCE_YEAR: Final[int] = 2018
ANNUAL_REFERENCE_YEAR: Final[int] = 2019


# =============================================================================
# 2. CAPACIDAD DECLARADA POR AENA (DORA 2017-2021)
# =============================================================================
@dataclass(frozen=True)
class DeclaredCapacity:
    """
    Capacidad declarada de LPA en el DORA 2017-2021, recogida en el TFM.

    Es la referencia contra la que contrastar cualquier resultado del modelo:
    si el simulador contradice la capacidad declarada por el gestor, hay que
    explicar por qué antes de publicar nada.
    """

    airport_mpa: float = 20.0           # millones de pasajeros/año
    terminal_php: float = 8350.0        # pasajeros/hora punta (ambos sentidos)
    airfield_aph: float = 60.0          # aeronaves/hora
    apron_aph: float = 58.0             # aeronaves/hora
    cargo_tonnes_year: float = 74200.0


DECLARED: Final[DeclaredCapacity] = DeclaredCapacity()


# =============================================================================
# 3. FACTOR DE HORA PUNTA
# =============================================================================
def peak_hour_ratio(php_total: float = float(PHP_2018.total),
                    annual_pax_millions: float = LPA_PAX_2019_TOTAL_M) -> float:
    """
    Pasajeros hora punta por cada millón de pasajeros anuales.

    Para LPA: 3.878 PHP / 13,2 MPA = 294 PHP por MPA.

    ADVERTENCIA METODOLÓGICA: este ratio NO es constante. El factor de hora
    punta DECRECE al crecer el tráfico, porque la demanda se reparte mejor a lo
    largo del día y del año (más frecuencias, más destinos, menos estacionalidad
    concentrada). Mantenerlo fijo produce por tanto un LÍMITE SUPERIOR de la
    hora punta futura, no una previsión.

    Se conserva fijo de forma deliberada y declarada: en un estudio de capacidad
    el sesgo conservador es el correcto, porque dimensionar por debajo del pico
    es el error caro.
    """
    return php_total / annual_pax_millions


PEAK_RATIO_LPA: Final[float] = peak_hour_ratio()  # 294 PHP/MPA

DEPARTURES_SHARE: Final[float] = PHP_2018.departures / PHP_2018.total  # 0,692


# =============================================================================
# 4. ESCENARIOS DE DEMANDA
# =============================================================================
class DemandSource(str, Enum):
    DORA3 = "DORA III (Aena, 2026)"
    TFM_PESSIMISTIC = "TFM 2020 — pesimista (+2%/año)"
    TFM_NORMAL = "TFM 2020 — normal (+6%/año)"
    TFM_OPTIMISTIC = "TFM 2020 — optimista (+10%/año)"
    OBSERVED = "Observado"


@dataclass(frozen=True)
class DemandScenario:
    """Un punto de demanda: año, tráfico anual y su procedencia."""

    year: int
    annual_pax_millions: float
    source: DemandSource

    def php_total(self, ratio: float = PEAK_RATIO_LPA) -> float:
        return self.annual_pax_millions * ratio

    def php_departures(self, ratio: float = PEAK_RATIO_LPA) -> float:
        return self.php_total(ratio) * DEPARTURES_SHARE


SCENARIOS: Final[tuple[DemandScenario, ...]] = (
    DemandScenario(2019, LPA_PAX_2019_TOTAL_M, DemandSource.OBSERVED),
    # DORA III: la cifra oficial y la que debe encabezar el estudio.
    DemandScenario(2031, 17.0, DemandSource.DORA3),
    # TFM 2020: extrapolación lineal sobre la serie 1999-2019, sin pandemia.
    DemandScenario(2026, 12.86, DemandSource.TFM_PESSIMISTIC),
    DemandScenario(2026, 16.10, DemandSource.TFM_NORMAL),
    DemandScenario(2026, 19.18, DemandSource.TFM_OPTIMISTIC),
    DemandScenario(2031, 13.99, DemandSource.TFM_PESSIMISTIC),
    DemandScenario(2031, 19.54, DemandSource.TFM_NORMAL),
    DemandScenario(2031, 24.83, DemandSource.TFM_OPTIMISTIC),
    DemandScenario(2036, 15.12, DemandSource.TFM_PESSIMISTIC),
    DemandScenario(2036, 22.98, DemandSource.TFM_NORMAL),
    DemandScenario(2036, 30.47, DemandSource.TFM_OPTIMISTIC),
)


def scenario_table(ratio: float = PEAK_RATIO_LPA) -> pd.DataFrame:
    """Tabla de escenarios con su traducción a hora punta."""
    rows = []
    for s in SCENARIOS:
        php = s.php_total(ratio)
        rows.append({
            "anio": s.year,
            "fuente": s.source.value,
            "MPA": s.annual_pax_millions,
            "PHP_total": round(php),
            "PHP_salidas": round(s.php_departures(ratio)),
            "vs_capacidad_declarada": round(php / DECLARED.terminal_php, 2),
        })
    return pd.DataFrame(rows).sort_values(["anio", "MPA"]).reset_index(drop=True)


def forecast_divergence() -> pd.DataFrame:
    """
    Contrasta la extrapolación del TFM con la previsión oficial del DORA III.

    Es un resultado del estudio por sí mismo: mide cuánto se desvía una
    extrapolación lineal pre-pandemia de la previsión del gestor seis años
    después, y sirve para justificar por qué el estudio adopta la cifra oficial.
    """
    dora = 17.0
    rows = []
    for s in SCENARIOS:
        if s.year == 2031 and s.source is not DemandSource.DORA3:
            rows.append({
                "escenario": s.source.value,
                "MPA_2031": s.annual_pax_millions,
                "DORA_III_MPA_2031": dora,
                "desviacion_pct": round(100 * (s.annual_pax_millions - dora) / dora, 1),
            })
    return pd.DataFrame(rows)


# =============================================================================
# 5. PUENTE AL MODELO DES
# =============================================================================
def config_for_scenario(
    scenario: DemandScenario,
    security_area_m2: float = 1500.0,
    n_arcos: int = 20,
    ratio: float = PEAK_RATIO_LPA,
    n_passengers: Optional[int] = None,
):
    """
    Construye la configuración DES de un escenario de demanda.

    La infraestructura se mantiene en la configuración actual (20 arcos, 110
    mostradores): la pregunta del estudio es precisamente hasta cuándo aguanta
    lo que hay, no qué pasaría si se ampliara.
    """
    php_dep = scenario.php_departures(ratio)
    return config_lpa_2018(
        security_area_m2=security_area_m2,
        n_arcos=n_arcos,
        pax_php=php_dep,
        n_passengers=n_passengers or int(php_dep),
        scenario_name=f"{scenario.year} — {scenario.source.value} — "
                      f"{scenario.annual_pax_millions:.1f} MPA "
                      f"({php_dep:.0f} pax/h salidas)",
    )


# =============================================================================
# 6. INFORME
# =============================================================================
def main() -> None:
    pd.set_option("display.width", 220)
    pd.set_option("display.max_columns", 30)

    print("=" * 92)
    print("ESCENARIOS DE DEMANDA — Aeropuerto de Gran Canaria")
    print("=" * 92)
    print(f"\nFactor de hora punta: {PEAK_RATIO_LPA:.0f} PHP por millón de pax anuales")
    print(f"  (derivado de {PHP_2018.total} PHP en {PHP_REFERENCE_YEAR} y "
          f"{LPA_PAX_2019_TOTAL_M} MPA en {ANNUAL_REFERENCE_YEAR})")
    print(f"Reparto salidas/llegadas: {100*DEPARTURES_SHARE:.0f}% / "
          f"{100*(1-DEPARTURES_SHARE):.0f}%")

    print("\n--- Capacidad declarada por Aena (DORA 2017-2021) ---")
    print(f"  Aeropuerto      {DECLARED.airport_mpa:.0f} millones de pax/año")
    print(f"  Terminal        {DECLARED.terminal_php:.0f} PHP (ambos sentidos)")
    print(f"  Campo de vuelos {DECLARED.airfield_aph:.0f} aeronaves/hora")
    print(f"  Plataforma      {DECLARED.apron_aph:.0f} aeronaves/hora")

    print("\n--- Escenarios de demanda y su hora punta ---")
    print(scenario_table().to_string(index=False))

    print("\n--- Divergencia de previsiones para 2031 ---")
    print(forecast_divergence().to_string(index=False))
    print("\n  El estudio adopta la cifra del DORA III por ser la oficial del")
    print("  gestor y la única elaborada con datos posteriores a la pandemia.")
    print("  Los escenarios del TFM se conservan como banda de sensibilidad.")


if __name__ == "__main__":
    main()
