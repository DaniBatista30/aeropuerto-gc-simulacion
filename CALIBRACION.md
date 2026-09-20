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
