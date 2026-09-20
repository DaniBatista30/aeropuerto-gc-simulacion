# Calibración del modelo con el Plan Director de LPA

## Fuente

**Plan Director del Aeropuerto de Gran Canaria** — Ministerio de Fomento /
Dirección General de Aviación Civil / AENA. Documento sellado por la DGAC.
Año base de los datos: **2000**. Horizontes: 2005 / 2010 / 2015.

Capítulos explotados:

| Capítulo | Aporta |
|---|---|
| 3. Estudio de la Situación Actual | Superficies, nº de mostradores y filtros, **tiempos de proceso medidos en LPA**, capacidades publicadas |
| Adjunto al Cap. 3 | **Fórmulas IATA** que AENA empleó para calcular capacidades |
| 4. Evolución Previsible de la Demanda | PHP de diseño por horizonte |
| 5. Necesidades Futuras | Superficies requeridas por horizonte |
| Anexo 3 | Datos de campo de vuelos: 2 pistas 3.100 × 45 m, franjas 300 m |

Los capítulos 3 y 6 llegaron como PDF **escaneado sin capa de texto**; se han
extraído por OCR. Toda cifra marcada como "verificar" debe confirmarse contra
el PDF original antes de usarse en un informe.

---

## Lo que ahora es dato real y ya no hipótesis

**Tiempos de servicio medidos en LPA** (Cap. 3). Sustituyen a los valores de
rango de industria que usaba el modelo:

| Proceso | UE | Internacional | Interinsular |
|---|---|---|---|
| Facturación | 66 s/pax | 120 s/pax | 64 s/pax |
| Bultos por pasajero | 1,35 | 1,39 | 1,27 |
| Control de pasaportes (salidas) | — | 8 s/pax | — |
| Control de pasaportes (llegadas) | — | 15 s/pax | — |

**Superficies reales (salidas):**

| Área | UE | Internacional | Interinsular |
|---|---|---|---|
| Hall de salidas | 8.239 m² | *verificar* | 2.875 m² |
| Colas de facturación | 4.234 m² | 1.600 m² | 300 m² |
| Sala de espera de embarque | 16.412 m² | 3.013 m² | 200 m² |
| Mostradores de facturación | 46 | 19 | 11 |
| Aparatos de Rayos X | 3 | 2 | 2 *(deducido)* |

**Demanda de diseño (Cap. 4, Tabla 4.4):**

| Año | Aeronaves HP | PHP total | PHP llegadas | PHP salidas |
|---|---|---|---|---|
| 2005 | 42 | 6.012 | 3.536 | 3.536 |
| 2010 | 47 | 7.148 | 4.205 | 4.205 |
| 2015 | 52 | 8.274 | 4.867 | 4.867 |

---

## Inconsistencia detectada en la fuente

El Adjunto al Cap. 3 define el número de aparatos de Rayos X como

$$N = \frac{(a+b)\,w}{y}, \qquad y = 600\ \text{bultos/hora/máquina}$$

lo que implica **444 pax/h por máquina** para $w = 1{,}35$.

Pero la tabla de capacidades del Cap. 3 publica **1.800 PHP con 3 máquinas**
(UE) y **1.200 PHP con 2 máquinas** (Internacional): exactamente **600 pax/h
por máquina**, ignorando el número de bultos. La nota "6 seg (10
PAX/MINUTO/MAQUINA)" confirma que para las capacidades se usó pax, no bultos.

**Las dos cifras del propio documento no son consistentes entre sí.** Esto no
invalida el Plan Director, pero obliga a declarar qué criterio se adopta. El
modelo adopta el conservador (por bultos) y lo documenta.

Ese dato deducido permite además recuperar la celda ilegible: la capacidad
interinsular de 1.200 PHP sólo es compatible con **2 aparatos de Rayos X**.

---

## Por qué NO se pueden usar los tiempos de seguridad del Plan Director

| Tráfico | PHP/línea Plan Director | PHP/línea hoy | Factor |
|---|---|---|---|
| UE | 444 | 240 | **1,85×** |
| Internacional | 432 | 240 | **1,80×** |
| Interinsular | 472 | 240 | **1,97×** |

Los tiempos del Plan Director son **anteriores a 2001**. Las medidas de
seguridad introducidas desde entonces (líquidos en 2006, electrónica, calzado)
han reducido el rendimiento de una línea a 180–240 pax/h. Adoptar los 6 s/bulto
del documento sobreestimaría la capacidad del filtro por un factor cercano a 2.

El módulo expone esto como un interruptor explícito: `modern_security=False`
reproduce el Plan Director (para **verificar** el motor), `modern_security=True`
usa tiempos contemporáneos (para **concluir** sobre el aeropuerto de hoy).

---

## Fórmulas IATA transcritas del Adjunto al Cap. 3

Implementadas en `lpa_calibration.py`:

- **Aparatos de Rayos X:** $N = (a+b)\,w / y$
- **Puestos de pasaportes (salidas):** $N = (a+b)\,t_2/60 \times 1{,}1$, con $t_2 = 8$ s
- **Puestos de pasaportes (llegadas):** $N = (d+b)\,t_3/60 \times 1{,}1$, con $t_3 = 15$ s
- **Sala de embarque:** $A = m \cdot s$, con $s = 1{,}4$ m²/pax
- **Recogida de equipajes:** $A = e\,w\,s/60$, con $s = 2$ m²/pax
- **Vestíbulo de llegadas:** $A = s\,(w d/60 + z d o/60) \times 1{,}1$
- **Puestos de aduana:** $N = e f t_3/60$, con $f = 0{,}25$, $t_3 = 1{,}5$ min

**Pendiente de transcripción:** las fórmulas de área del *vestíbulo de salidas*
y de la *zona de colas de facturación* quedaron parcialmente ilegibles en el
OCR. Hay que copiarlas a mano del PDF original (Adjunto, págs. 13–14).

---

## Brechas que siguen abiertas

| ID | Brecha | Estado |
|---|---|---|
| G4 | Superficie del **control de seguridad** | **Reducida a una sola incógnita.** El Plan Director da hall, colas de facturación y sala de embarque, pero no desglosa el filtro: sólo el nº de máquinas. El reparto entre las cuatro subzonas sigue siendo hipótesis del modelo. |
| G2 | Validación contra datos históricos | Abierta, pero ahora hay un blanco: reproducir las capacidades publicadas por AENA. |
| — | **Vigencia** | **Nueva brecha, y grave.** Estos datos son del año 2000. El terminal se ha ampliado desde entonces y la previsión de 16,89 MPA para 2015 no se cumplió. Hace falta la revisión vigente del Plan Director. |

---

## Advertencia de uso

Este material describe el terminal **hacia el año 2000**, no el de hoy. Úsalo
como línea base documentada y verificable frente a la que validar el motor de
simulación, y como fuente de estructura y metodología. **No lo presentes como
la configuración actual de LPA.**


---

# Ampliación: línea base contemporánea (TFM 2018 + DORA III)

Fuentes incorporadas en `lpa_actual.py`:

- **TFM** "Planificación estratégica y análisis de la capacidad actual y futura
  del Aeropuerto de Gran Canaria" (2020), datos de 2018, superficies medidas
  sobre los planos de planta.
- **DORA III**, información privilegiada de Aena de 18/02/2026, propuesta para
  2027–2031, pendiente de aprobación por DGAC y CNMC.

## Inventario real del terminal (2018)

Terminal único: 450 m de largo × 75–120 m de ancho, cuatro niveles.
Vestíbulo de salidas (planta 1): **13.000 m²**.

| Zona de facturación | Mostradores | Nº | Tráfico |
|---|---|---|---|
| A | 101–118 | 18 | Interinsular y Marruecos |
| B y C | 201–234 | 34 | UE y Escandinavia |
| D | 301–352 | 52 | Internacional |
| Ryanair (planta 0) | 401–406 | 6 | Mixto |
| **Total** | | **110** | |

Control de seguridad: **dos zonas** — Norte (4 unidades, con Fast Lane) y
Central (16 unidades). **20 unidades de inspección = 10 filtros dobles.**
Control de pasaportes: **8 puestos** en planta 1.

Salas de embarque: 2.000 m² (Internacional Norte) + 7.900 m² (resto).

## Demanda real en hora punta (2018)

| | pax/hora |
|---|---|
| Salidas | 2.684 |
| Llegadas | 2.146 |
| Total | 3.878 |
| De salidas, cruzan frontera | 740 (28 %) |

## El dato del gestor

El TFM recoge que **AENA trabaja con 350 pax/hora por filtro de seguridad**.
Es el parámetro más valioso de todo el material: procede del gestor, no de un
manual. Sirve como blanco de validación del modelo.

| Fuente | pax/h por filtro | Capacidad total (10 filtros) | Ratio sobre demanda 2018 |
|---|---|---|---|
| Plan Director 2000 (600 bultos/h) | 429 | 4.286 | 1,60 |
| Fórmula PAX10, t₃ = 8 s | 450 | 4.500 | 1,68 |
| **AENA, gestión operativa** | **350** | **3.500** | **1,30** |
| Industria contemporánea | 240 | 2.400 | 0,89 |

Cuatro fuentes, cuatro cifras, con un rango de 1,9×. Hay que elegir una de
forma explícita y justificada.

## Resultado del primer contraste: el modelo NO reproduce la cifra de AENA

Ejecutado con el inventario real (10 filtros dobles, 110 mostradores,
2.684 pax/h), el modelo predice **158 pax/h por filtro** frente a los 350 de
AENA. Factor 2,2 de discrepancia.

El diagnóstico apunta a la definición de "filtro":

| Configuración | Caudal | pax/h por filtro doble | Bloqueo |
|---|---|---|---|
| 10 filtros dobles como 10 arcos | 1.583 | 158 | 13,5 % |
| 20 unidades como 20 arcos | 2.482 | 248 | 0 % |

Interpretando cada unidad de inspección como un arco independiente, el caudal
sube a 248 pax/h por filtro doble, cerca ya del orden de magnitud de AENA.
La diferencia restante se explica por los tiempos de preparación y
recomposición, que son hipótesis del modelo y no datos medidos.

**Esto es exactamente para lo que sirve un blanco de validación.** El modelo
está diciendo que su representación de la línea de filtro no coincide con la
realidad operativa de LPA, y eso hay que resolverlo antes de usarlo para
predecir nada.

Obsérvese además que la superficie del filtro **no cambia el caudal** en el
rango 900–3.000 m² una vez hay 20 arcos: el cuello de botella es de servidores,
no de espacio. Ese es ya un resultado del barrido de sensibilidad.

## DORA III (2027–2031)

- Tráfico estimado para **Gran Canaria en 2031: 17,0 millones de pasajeros**.
- Inversión regulada en la red: 9.991 M€. WACC antes de impuestos: 9 %.
- IMAP: 10,92 €/pax (2027) → 12,69 €/pax (2031).
- Compromiso literal: *"During the planned works on terminal buildings, current
  capacity will be maintained, and this fact represents a firm commitment to
  all users in an environment of highly congested infrastructure."*
- **Gran Canaria no figura** entre los doce aeropuertos con obras mayores de
  área terminal enumerados en el documento. Verificar contra el DORA III
  completo cuando se publique.

## La convergencia que sostiene el proyecto

El Plan Director fijó su horizonte de Desarrollo Previsible en **17 MPA**, y su
previsión situaba esa cifra hacia **2015**. El DORA III estima que LPA alcanzará
**17,0 MPA en 2031**.

El aeropuerto llegará al techo de su planificación dieciséis años más tarde de
lo previsto, con el instrumento de planificación sin actualizar pese a que el
RD 2591/1998 art. 7 obliga a revisarlo al menos cada ocho años.

## Lo que sigue faltando

La **superficie del recinto de control de seguridad**. No figura en el Plan
Director, ni en el TFM, ni en el DORA III. Se resuelve por barrido de
sensibilidad, no por estimación — y el barrido preliminar ya sugiere que en
este caso no es el parámetro crítico.

---

# Calibración del filtro y validación contra AENA

## Parámetros adoptados (rangos de industria)

| Etapa | Ley | Media | P1 / P99 |
|---|---|---|---|
| Preparación (divest) | Lognormal, σ=0,40 | 52,5 s | 19 s / 123 s |
| Rayos X | Gamma, CV=0,25 | 12,5 s **por bandeja** | — |
| Bandejas por pasajero | Discreta (55/35/10 %) | 1,55 | 1 / 3 |
| Recomposición (repack) | Lognormal, σ=0,40 | 75 s | 27 s / 176 s |

Los mínimos y máximos citados en la literatura (20/120 s en divest, 30 s en
repack) se reproducen como percentiles 1 y 99 de la Lognormal. Eso permite
usar una ley con cola pesada en vez de una triangular, sin perder el anclaje
en los valores publicados.

**Nota de atribución.** Estos rangos son de industria. No deben citarse como
estándar IATA ni ECAC sin tener a mano el documento, la edición y el párrafo:
el Doc 30 de ECAC es normativa de seguridad (qué inspeccionar y con qué
estándares de detección), no una fuente de tiempos de servicio.

## Posiciones por arco

| Etapa | Posiciones | Caudal por arco |
|---|---|---|
| Preparación | 4 | 274 pax/h |
| Rayos X | 1 (el arco) | **186 pax/h** ← limita |
| Recomposición | 4 | 192 pax/h |

Con 3 posiciones de recomposición el caudal cae a 144 pax/h y estrangula el
arco por debajo del rango de industria. Hacen falta 4.

## Tres errores corregidos

**1. Arcos contados como filtros.** El TFM describe 20 unidades de inspección
como 10 filtros dobles. El modelo contaba 10 servidores donde hay 20. Origen
del factor 2,2 inicial.

**2. Registro secundario dentro del arco.** El modelo bloqueaba el arco durante
el cacheo (8 % de pasajeros × 75 s). En la práctica se hace en una mesa lateral.
Corregirlo devolvió un 27 % de caudal.

**3. El caudal se medía mal.** El KPI dividía pasajeros entre horizonte total,
incluyendo la rampa inicial y el vaciado final, en los que el sistema no está
saturado. Hay que medir el **caudal sostenido** en la ventana saturada.

## Resultado de la validación

Sistema inundado (9.000 pax/h) para forzar la saturación, caudal sostenido
medido entre los minutos 12 y 45:

| | pax/h |
|---|---|
| Caudal sostenido del filtro | **3.593** |
| Por arco (20) | **180** (industria: 150–200 ✓) |
| Por filtro doble (10) | **359** |
| Cifra de AENA | 350 |
| **Desviación** | **+2,6 %** |

El modelo reproduce la cifra de gestión de AENA con un 2,6 % de desviación, y
simultáneamente cae dentro del rango de industria por arco. Las dos referencias
son independientes entre sí y el modelo satisface ambas.

**El filtro está validado.** Deja de ser una hipótesis y pasa a ser un modelo
contrastado contra el dato del gestor.

---

# Ampliación a llegadas: frontera, equipajes y aduanas

## Por qué llegadas no es salidas al revés

Salidas es llegada continua (Poisson razonable). **Llegadas es un proceso por
lotes**: un vuelo aterriza y 150-200 personas aparecen a la vez. Además
aparece un recurso sin equivalente en salidas: el equipaje viaja por un
sistema propio (tiempo de primera maleta + tasa de entrega), y el pasajero no
sale hasta que su maleta llega, no cuando él quiere.

## Corrección metodológica: remanente, no espera media

Un primer intento simuló una demanda sostenida durante 120 minutos sobre un
recurso deficitario (2 de 4 puestos de frontera operativos). Resultado:
esperas medias de 105 minutos, P95 de más de 3 horas. **Cifra descartada**:
cuando la capacidad es permanentemente menor que la demanda, la cola crece sin
límite cuanto más tiempo se sostenga la simulación, y el número final depende
de cuánto rato se decida simular, no de la realidad operativa.

La lectura correcta de un recurso deficitario no es "espera media", es
**remanente al final de la hora punta** y **tiempo de vaciado**: se programan
vuelos durante 60 minutos exactos y se deja correr el sistema hasta que se
vacía por completo.

## Dos lecturas de la demanda, nunca mezcladas

El TFM calcula la hora punta de cada tráfico por separado, y esos picos NO son
simultáneos: la suma (3.485 pax/h) supera el PHP total observado de llegadas
(2.146 pax/h). Factor de no-coincidencia: **0,62**.

- **Escenario DISEÑO** (factor 1,0): los picos se solapan por completo. Es el
  supuesto que usa el propio TFM al dimensionar instalaciones compartidas — un
  techo conservador, no una hora observada.
- **Escenario OBSERVADO** (factor 0,62): reproduce el PHP real de 2.146 pax/h.

## Resultado del contraste estático (TFM) — ya había un déficit documentado

| Instalación | Capacidad | Demanda | Ratio | Estado |
|---|---|---|---|---|
| Frontera, 4 puestos instalados | 873 | 848 | 1,03 | Al límite |
| **Frontera, 2 puestos operativos** | **436** | **848** | **0,51** | **Déficit** |
| Aduanas (47 m²) | 732 | 848 | 0,86 | Déficit |
| Las tres salas de recogida | — | — | 2,5–15,9 | Holgadas |
| Ambos vestíbulos | — | — | 1,7–3,0 | Holgados |

**El déficit de frontera y aduanas ya estaba en el TFM.** El modelo dinámico
no lo inventa: lo confirma y le añade una dimensión temporal que la fórmula
estática no puede dar.

## Resultado del modelo dinámico

| | Diseño (picos sumados) | Observado (PHP real) |
|---|---|---|
| Frontera, P95 espera | 55,3 min | 16,8 min |
| Remanente al final de la punta | **20,6 %** de los pasajeros | **14,8 %** |
| Tiempo hasta vaciado total | +117 min | +57 min |

Incluso en el escenario **observado** —el real, no el conservador—, con solo
2 puestos de control de pasaportes operativos, **1 de cada 7 pasajeros de la
hora punta sigue en cola cuando ésta ha terminado**, y el sistema tarda casi
una hora más en vaciarse. Con los 4 puestos instalados (capacidad 873 vs
demanda 848, ratio 1,03) el margen sería mínimo pero positivo.

**Esto es un hallazgo operativo, no una debilidad del modelo**: la brecha
entre puestos instalados y puestos operativos cuesta, en el escenario real,
cerca de una hora de cola remanente por cada hora punta.

## Brecha declarada

`customs_positions = 2` es una **hipótesis del modelo, no un dato de fuente**.
El TFM da los puestos *necesarios* (5) pero no dice, como sí hace con
fronteras, cuántos están operativos hoy. Se ha asumido 2 por similitud
operativa con fronteras; debe verificarse con el gestor antes de un informe.

## Instalaciones (TFM, superficies medidas sobre planos)

| Zona | m² | Unidades |
|---|---|---|
| Sala de recogida Internacional | 1.868 | 3 hipódromos |
| Sala de recogida Europea | 4.985 | 10 hipódromos |
| Sala de recogida Nacional | 3.275 | 5 hipódromos |
| Frontera de llegadas | 1.385 | 4 puestos (2 operativos) |
| Aduanas | 47 | — |
| Vestíbulo Norte (nacional + interinsular) | 1.879 | — |
| Vestíbulo Sur (UE + internacional) | 1.279 | — |

---

# Barrido de sensibilidad: superficie del filtro de seguridad

## Método

Superficie barrida de 200 a 3.000 m², 12 réplicas independientes por punto,
mismas semillas en cada punto (para que la diferencia entre superficies no se
confunda con ruido muestral). Dos regímenes:

1. **Límite físico**: demanda saturante (9.000 pax/h) para medir el caudal
   sostenido en ventana estable, al margen de cuánta gente quiera entrar.
2. **Escenarios reales**: demanda de 2019 observada (2.684 pax/h) y del
   DORA III a 2031 (3.457 pax/h).

## Resultado 1 — El caudal no depende de la superficie, casi en ningún punto

| Superficie | Caudal sostenido | Bloqueados |
|---|---|---|
| 200 m² | 3.437 pax/h | 91,3 % |
| 300 m² | 3.624 pax/h | 87,9 % |
| 400–3.000 m² | **3.635 pax/h (plano)** | 84,5 % → **0 %** |

El caudal se estabiliza ya a partir de 300-400 m²: por encima, el cuello de
botella es el arco de rayos X, no el espacio disponible. **Esto confirma la
intuición inicial.**

## Resultado 2 — Pero el bloqueo SÍ depende de la superficie, en todo el rango

Esto es lo que la prueba informal anterior no vio. Aunque el caudal es plano
desde 300 m², el **porcentaje de pasajeros que sufren bloqueo aguas arriba**
(spill-back hacia facturación) cae de forma continua en todo el rango barrido,
sin estabilizarse hasta los 3.000 m²:

```
200 m²  → 91 % bloqueados
1.000 m² → 64 %
2.000 m² → 31 %
3.000 m² → 0 %
```

**Conclusión matizada:** la superficie no determina cuánta gente puede pasar
por hora, pero sí determina si esa gente lo hace formando un atasco visible
hacia el vestíbulo de facturación o de forma fluida. Un caudal correcto con
un vestíbulo colapsado no es un buen resultado operativo, aunque el número de
"pasajeros por hora" sea idéntico.

## Resultado 3 — Bajo demanda REAL, la superficie deja de importar por completo

| Superficie | 2019 (2.684 pax/h) — P95 | 2031 DORA III (3.457 pax/h) — P95 |
|---|---|---|
| 200 m² | 1,57 min | 3,64 min |
| 300 m² | 1,57 min | 1,78 min |
| **400 m² en adelante** | **1,57 min (plano)** | **1,69 min (plano)** |

Con demanda de 2019, el resultado es idéntico en todo el rango: la superficie
es irrelevante porque el sistema nunca se acerca a su límite físico. Con la
demanda del DORA III a 2031, sólo por debajo de 300-400 m² aparece un efecto
(P95 de 3,6 min y un 32 % de bloqueo a 200 m²); a partir de 400 m² el
resultado también es plano.

## Veredicto

**Bajo cualquier demanda prevista hasta 2031, la superficie del filtro de
seguridad deja de ser un dato crítico a partir de aproximadamente 400 m².**
Como cualquier estimación razonable del recinto de LPA (a partir del área
total del vestíbulo de salidas, 13.000 m² según el TFM) supera con holgura
ese umbral, el estudio puede prescindir de este dato sin perder validez.

La única situación en la que sí sería necesario obtenerlo con precisión es
para modelar densidades de aglomeración por debajo de 400 m², que no
corresponde a ningún escenario de demanda documentado para LPA.

---

# Etapa de puertas QR (verificación de tarjeta de embarque)

## Contexto

Aportado por el usuario, con conocimiento directo del aeropuerto: antes del
serpentín de seguridad hay unas puertas que escanean el QR de la tarjeta de
embarque y dan paso al pasillo. Esta etapa **no estaba en el modelo
validado**, que empezaba directamente en la zona de encolamiento.

No es un problema de geometría peatonal: una puerta que escanea un código y
se abre es un servidor con un tiempo de servicio, igual que un mostrador o un
arco. Encaja en el mismo modelo de colas como una etapa más, sin necesidad de
simular cómo camina la gente. Implementada como extensión no invasiva
(`lpa_puertas.py`, subclase `GatedTerminalModel`) que no modifica el modelo
validado.

Nº de puertas: dato incierto (el usuario estima 4-10). Se barre el rango en
vez de fijar un valor, mismo criterio que con la superficie del filtro.
Tiempo de servicio: 3 s de media — **hipótesis**, rango de industria para
lectores de QR/embarque; no hay dato de LPA.

## Efecto colateral: un fallo real encontrado al construir esta prueba

Al montar el barrido, el P95 de seguridad salió en 9,5 minutos en vez de los
1,69 min validados. La causa no eran las puertas: `config_lpa_2018()` no
fijaba `divest_positions_per_lane`, y el valor por defecto de la clase
(`TerminalConfig.divest_positions_per_lane = 3`) es **distinto** del que se
usó para validar el filtro contra AENA (4). Los análisis que llamaron a
`config_lpa_2018()` sin pasar `divest_positions_per_lane=4` explícitamente
—como el primer intento de este mismo barrido— heredaban una preparación
infra-aprovisionada sin que nada lo avisara.

**Corregido**: `config_lpa_2018()` fija ahora `divest_positions_per_lane=4`
por defecto. Cualquier análisis futuro que use esta función parte de la
configuración validada sin depender de que quien la llama lo recuerde.

## Resultado del barrido

| Nº puertas | Espera media (2019) | Espera media (2031 DORA III) | Capacidad teórica |
|---|---|---|---|
| 3 | 0,021 min | 0,170 min | 3.600 pax/h |
| 4 | 0,004 min | 0,011 min | 4.800 pax/h |
| 6 en adelante | ~0,000 min | ~0,000 min | ≥7.200 pax/h |

Con la estimación del usuario (4 a 10 puertas), la espera en la puerta es
**irrelevante** bajo cualquier demanda prevista hasta 2031: milésimas de
minuto de media, y el P95 de seguridad no se mueve (1,57 y 1,69 min, iguales
que sin la etapa). Con solo 3 puertas empezaría a notarse levemente, pero
sigue siendo un efecto menor comparado con el propio filtro.

**Veredicto**: la etapa de puertas QR es real y ahora está en el modelo, pero
con el rango de puertas que describe el usuario no es el cuello de botella.
El resultado depende del tiempo de servicio asumido (3 s, hipótesis): si en
la práctica el escaneo tardara notablemente más, la conclusión podría
cambiar y merecería remedirse.
