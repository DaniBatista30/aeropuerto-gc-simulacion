# LPA-DES — Simulador de Eventos Discretos del Aeropuerto de Gran Canaria (LPA)

Modelo DES en Python (SimPy) del **Lado Tierra** de la terminal de salidas de LPA,
evaluado contra los estándares **IATA ADRM** (tiempos máximos de cola y espacio por
pasajero) y **Fruin** (niveles de servicio de densidad peatonal).

El módulo de **Lado Aire (OACI Anexo 14)** está previsto en el roadmap.

## Estructura

| Fichero | Contenido |
|---|---|
| `lpa_des_model.py` | El modelo: recursos, zonas físicas, agente pasajero, monitor de LoS. |
| `lpa_experiment.py` | El diseño de experimentos: réplicas, intervalos de confianza, comparación de escenarios. **Es el que produce resultados presentables.** |

## Instalación

```bash
python -m venv .venv && source .venv/bin/activate
pip install -r requirements.txt
```

En Google Colab:

```python
!pip install simpy -q
%run lpa_experiment.py
```

## Uso

```bash
# Corrida única (sólo para inspeccionar el comportamiento del modelo)
python lpa_des_model.py
python lpa_des_model.py --security-lanes 3 --closure-queue 0.4

# Estudio completo: escenario actual vs obras, 30 réplicas, IC al 95 %
python lpa_experiment.py

# Réplicas secuenciales hasta 5 % de precisión relativa
python lpa_experiment.py --sequential --target-precision 0.05

# Exportar todo a CSV
python lpa_experiment.py --csv-prefix resultados
```

## Decisiones de modelización que afectan a los resultados

**1. El filtro no es un servidor único.** Una línea de seguridad se modela como un
pipeline de tres etapas: varias posiciones de preparación en paralelo, **un** arco/RX
que fija el caudal (~240 pax/h/línea), y varias posiciones de recomposición. Modelar
la línea como un único servidor de 18 s da caudales irreales y oculta que la
recomposición es, en la práctica, la causa más frecuente de bloqueo.

**2. Descomposición zonal.** El filtro se divide en cuatro subzonas (encolamiento,
preparación, inspección, recomposición), cada una con su superficie y su aforo físico.
La densidad m²/pax se calcula **por zona**, no como media del recinto: una media global
oculta exactamente lo que se quiere medir. Las obras se aplican **por zona**, que es
como se ejecutan en la realidad — se acota un pasillo, no "el filtro" en abstracto.

**3. Spill-back.** Cada zona tiene aforo físico (`simpy.Container`). Cuando se agota,
el pasajero queda retenido en la zona anterior. Es el mecanismo que colapsa una terminal
en obras y que un modelo de colas sin capacidad física no puede reproducir. Cuando una
zona satura, la densidad se queda clavada en el aforo (0,5 m²/pax) y el exceso aparece
como spill-back: ese valor **no** es "el peor caso", es el tope del modelo.

**4. Simulación terminante, no estacionaria.** Una hora punta empieza y acaba con el
sistema vacío. El método correcto es **réplicas independientes**, no truncar un warm-up
ni aplicar medias por lotes: esas técnicas son para sistemas que nunca se vacían y aquí
sesgarían justo los picos que se quieren medir.

**5. Números Aleatorios Comunes (CRN).** Cada fuente estocástica consume su propio flujo
(`RandomStreams`, vía `SeedSequence.spawn`). Al comparar escenarios, la réplica *i* de
ambos usa la misma semilla maestra, lo que induce correlación positiva y reduce la
varianza de la diferencia. El informe reporta la correlación empírica y la reducción de
varianza lograda, para que se pueda verificar que la técnica está funcionando.

## Parametrización estocástica

- **Gamma(k, θ)** con `k = 1/CV²`, `θ = μ·CV²`. Ley natural de un servicio compuesto por
  micro-tareas secuenciales (la suma de k etapas exponenciales es una Erlang/Gamma).
  Se usa en arco/RX, preparación y e-Gates.
- **Lognormal** con `μ_log = ln(m) − σ²/2` para fijar la media aritmética. Cola derecha
  más pesada: reproduce los casos patológicos del mostrador que gobiernan el P95/P99.
- El muestreo usa los generadores de NumPy (≈50× más rápido); SciPy se reserva para los
  cuantiles teóricos, usados en validación.

## Advertencia de calibración

**Los umbrales normativos y los tiempos de servicio son valores de trabajo basados en
rangos de industria, no mediciones de LPA.** Antes de presentar cualquier resultado:

1. Verificar cada umbral contra la edición vigente del IATA ADRM. Están centralizados en
   `IATALoSStandards` y `FruinQueueStandards`.
2. Sustituir las superficies de `default_zone_layout()` por las del plano real.
3. Sustituir las medias de servicio por mediciones del gestor aeroportuario.
4. Validar el modelo contra un periodo histórico conocido antes de usarlo para predecir.

Sin el paso 4 el modelo es una hipótesis coherente, no una predicción.

## Roadmap

- [x] Descomposición zonal + Fruin LoS.
- [x] Flujos aleatorios independientes y CRN.
- [x] Réplicas, intervalos de confianza y comparación pareada de escenarios.
- [ ] Lado Aire (OACI Anexo 14): pistas, rodaduras, stands, separación por estela.
- [ ] Perfil de show-up no homogéneo por vuelo desde el horario de temporada.
- [ ] Recogida de equipajes (reclaim).
- [ ] Validación contra tiempos medidos por el gestor aeroportuario.
