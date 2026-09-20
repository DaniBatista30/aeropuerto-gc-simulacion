# Anexo matemático — LPA-DES

Formulación completa del modelo, con la evaluación de si cada bloque cumple el
estándar exigible en un estudio de capacidad aeroportuaria.

> **Cómo leer este documento.** Cada sección indica dónde está implementada la
> ecuación en el código y termina con un juicio de conformidad. Las brechas
> están declaradas explícitamente en la sección 9; no están escondidas.

---

## 1. Notación

| Símbolo | Significado | Unidad |
|---|---|---|
| $\lambda$ | Tasa de llegada de pasajeros | pax/s |
| $S$ | Tiempo de servicio (variable aleatoria) | s |
| $c$ | Número de servidores en paralelo | — |
| $\rho$ | Utilización del recurso | — |
| $W_q$ | Tiempo de espera en cola | s |
| $L(t)$ | Número de pasajeros en el sistema en el instante $t$ | pax |
| $n_z(t)$ | Ocupación de la zona $z$ | pax |
| $A_z$ | Superficie útil de la zona $z$ | m² |
| $\phi_z$ | Fracción de $A_z$ clausurada por obras | — |
| $\sigma_z(t)$ | Espacio libre por pasajero en la zona $z$ | m²/pax |
| $C_a, C_s$ | Coeficientes de variación de llegadas y servicio | — |
| $n$ | Número de réplicas independientes | — |

---

## 2. Proceso de llegadas

Proceso de Poisson homogéneo de intensidad $\lambda$. El número de llegadas en
una ventana de duración $t$ es

$$N(t) \sim \text{Poisson}(\lambda t), \qquad P[N(t)=k]=\frac{(\lambda t)^k e^{-\lambda t}}{k!}$$

y los intervalos entre llegadas son exponenciales e independientes:

$$X \sim \text{Exp}(\lambda), \qquad f_X(x)=\lambda e^{-\lambda x}, \quad \mathbb{E}[X]=\frac{1}{\lambda}, \quad C_a = 1$$

Conversión desde la tasa horaria de diseño:

$$\lambda = \frac{\text{pax/h}}{3600}$$

**Implementación:** `AirportTerminalModel.passenger_source()`, vía
`Generator.exponential(1/λ)`.

**Conformidad: PARCIAL.** El proceso de Poisson es la hipótesis correcta para
llegadas independientes y sin memoria, y es adecuado para una ventana corta de
intensidad constante. Pero el ADRM dimensiona sobre **perfiles de presentación
(show-up) no homogéneos**: la demanda real es la superposición de las curvas de
antelación de cada vuelo respecto a su hora programada de salida, y tiene picos
que un proceso homogéneo no reproduce. Ver brecha **G1**.

---

## 3. Leyes de servicio

### 3.1 Gamma

$$f_S(x)=\frac{x^{k-1}e^{-x/\theta}}{\Gamma(k)\,\theta^{k}}, \qquad x>0$$

Parametrización por media $\mu$ y coeficiente de variación $C_s$:

$$k=\frac{1}{C_s^{2}}, \qquad \theta=\mu\,C_s^{2}$$

de donde

$$\mathbb{E}[S]=k\theta=\mu, \qquad \operatorname{Var}[S]=k\theta^{2}=(\mu C_s)^{2}, \qquad \gamma_1=\frac{2}{\sqrt{k}}=2C_s$$

**Justificación estructural.** Si un servicio consta de $k$ micro-tareas
secuenciales independientes, cada una $\text{Exp}(1/\theta)$, entonces

$$S=\sum_{i=1}^{k} E_i \sim \text{Gamma}(k,\theta)$$

Es decir, la Gamma no es una elección de conveniencia: es la distribución exacta
de un servicio en cadena. Por eso se aplica al arco/RX (aproximación, bandeja,
tránsito, recogida) y a las e-Gates.

**Implementación:** `GammaService`. Parámetros: arco/RX $\mu=15\,$s, $C_s=0{,}30$;
preparación $\mu=40\,$s, $C_s=0{,}45$; recomposición $\mu=45\,$s, $C_s=0{,}50$;
e-Gate ABC $\mu=18\,$s, $C_s=0{,}30$.

### 3.2 Lognormal

Si $\ln S \sim \mathcal{N}(m, s^{2})$:

$$\mathbb{E}[S]=e^{m+s^{2}/2}, \qquad \operatorname{Var}[S]=\left(e^{s^{2}}-1\right)e^{2m+s^{2}}, \qquad C_s=\sqrt{e^{s^{2}}-1}$$

Para fijar la media aritmética objetivo $\mu$ se despeja el parámetro de
localización:

$$m=\ln\mu-\frac{s^{2}}{2}$$

**Justificación estructural.** La cola derecha decae más lentamente que en la
Gamma. Reproduce los casos patológicos del mostrador (exceso de equipaje,
documentación de menores, mascotas, reubicación de asiento) que son los que
gobiernan $P_{95}$ y $P_{99}$, no la media.

**Implementación:** `LognormalService`. Facturación $\mu=150\,$s, $s=0{,}45$;
cabina policial $\mu=28\,$s, $s=0{,}40$.

### 3.3 Truncamiento inferior

$$S' = \max(S, s_{\min})$$

**Sesgo introducido.** El truncamiento eleva la media:

$$\mathbb{E}[S']=\mathbb{E}[S]+\int_{0}^{s_{\min}}(s_{\min}-x)f_S(x)\,dx$$

Con los parámetros usados, $P[S<s_{\min}]<10^{-3}$ y el sesgo es inferior al
0,1 % de $\mu$. Es despreciable, pero **debe declararse**, no ignorarse.

**Conformidad: CORRECTA.** Distribuciones asimétricas de soporte positivo con
justificación estructural, no ajustadas a ojo. El ADRM no prescribe una familia
concreta; exige que la variabilidad esté representada, y lo está.

---

## 4. Red de colas y contención

El sistema es una **red abierta de colas M/G/c en tándem con bloqueo**.

### 4.1 Utilización

$$\rho=\frac{\lambda\,\mathbb{E}[S]}{c}$$

Condición de estabilidad: $\rho<1$. Con $\rho\ge 1$ la cola crece sin cota y
cualquier estadístico de espera depende del horizonte, no del sistema.

### 4.2 Disciplina y medida de la espera

FIFO. El tiempo de espera de cada pasajero se mide como

$$W_q = t_{\text{concesión}} - t_{\text{solicitud}}$$

que en SimPy es exactamente el intervalo entre `resource.request()` y el
disparo del evento.

### 4.3 Bloqueo por aforo (spill-back)

Cada zona $z$ tiene un aforo entero $N_z^{\max}$. Un pasajero sólo entra si

$$n_z(t) < N_z^{\max}$$

En caso contrario permanece en la zona anterior. El tiempo de bloqueo acumulado
por pasajero es

$$B=\sum_{z}\left(t_{z}^{\text{entrada}}-t_{z}^{\text{solicitud}}\right)$$

Esta es la diferencia esencial frente a un modelo de colas puro: sin aforo, la
saturación se manifiesta como una cola infinitamente densa, lo cual es
físicamente imposible y subestima el impacto de una obra.

### 4.4 Caudal de la línea de filtro

La línea es un pipeline de tres etapas con $c_d$ posiciones de preparación,
$c_a$ arcos y $c_r$ posiciones de recomposición. El caudal está fijado por la
etapa más restrictiva:

$$\Theta=\min\left(\frac{c_d}{\mathbb{E}[S_d]},\ \frac{c_a}{\mathbb{E}[S_a]},\ \frac{c_r}{\mathbb{E}[S_r]}\right)\times 3600 \quad [\text{pax/h}]$$

Con la parametrización actual el arco es el cuello de botella:
$\Theta = 3600/15 = 240$ pax/h por línea.

**Conformidad: CORRECTA.** Modelar la línea como un servidor único de ~18 s da
caudales irreales y oculta el bloqueo por recomposición, que es el modo de fallo
observado en la práctica.

---

## 5. Aforo y geometría

Superficie útil tras clausura parcial:

$$A_z=A_z^{\text{nom}}\,(1-\phi_z), \qquad \phi_z\in[0,1)$$

Aforo físico a densidad de aglomeración $a_{\text{crush}}$:

$$N_z^{\max}=\left\lfloor \frac{A_z}{a_{\text{crush}}} \right\rfloor, \qquad a_{\text{crush}}=0{,}5\ \text{m}^2/\text{pax}$$

**Conformidad: CORRECTA en forma, PENDIENTE en datos.** La formulación es
trivial y sólida; el problema es que $A_z^{\text{nom}}$ son valores de trabajo
inventados. Ver brecha **G4** — es la brecha crítica.

---

## 6. Métricas de Nivel de Servicio

### 6.1 Densidad instantánea

$$\sigma_z(t)=\frac{A_z}{\max\left(1,\ n_z(t)\right)} \qquad [\text{m}^2/\text{pax}]$$

### 6.2 Clasificación IATA ADRM (espacio)

$$\text{LoS}_{\text{espacio}}=\begin{cases}\text{Over-design} & \sigma \ge 1{,}5\\ \text{Optimum} & 1{,}2 \le \sigma < 1{,}5\\ \text{Sub-optimum} & \sigma < 1{,}2\end{cases}$$

La alarma **Over-crowded** se dispara cuando $\sigma_z(t) < 1{,}2$ con
$n_z(t)>0$.

### 6.3 Clasificación de Fruin (densidad peatonal en cola)

$$\text{LoS}_{\text{Fruin}}=\begin{cases}A & \sigma \ge 1{,}2\\ B & 0{,}9 \le \sigma < 1{,}2\\ C & 0{,}7 \le \sigma < 0{,}9\\ D & 0{,}3 \le \sigma < 0{,}7\\ E & 0{,}2 \le \sigma < 0{,}3\\ F & \sigma < 0{,}2\end{cases}$$

El límite A/B coincide con el óptimo del ADRM, lo que proporciona una
verificación cruzada entre ambos marcos.

### 6.4 Clasificación por tiempo de cola (MQT)

$$\text{LoS}_{\text{tiempo}}=\begin{cases}\text{Over-design} & W_q < \tau_1\\ \text{Optimum} & \tau_1 \le W_q \le \tau_2\\ \text{Sub-optimum} & W_q > \tau_2\end{cases}$$

con $(\tau_1,\tau_2)$ = (10, 20) min en facturación y (5, 10) min en seguridad y
fronteras.

### 6.5 Percentiles

Estimador empírico con interpolación lineal entre órdenes:

$$\hat{P}_q = x_{(\lfloor h \rfloor)} + (h-\lfloor h \rfloor)\left(x_{(\lceil h \rceil)}-x_{(\lfloor h \rfloor)}\right), \qquad h=(N-1)\frac{q}{100}+1$$

**Advertencia estadística.** $\hat{P}_{95}$ calculado sobre $N$ pasajeros de una
sola corrida tiene varianza considerable, y $\hat{P}_{99}$ mucha más: su varianza
asintótica es

$$\operatorname{Var}\left[\hat{P}_q\right]\approx\frac{q(1-q)}{N\left[f(P_q)\right]^{2}}$$

que crece cuando $f(P_q)$ es pequeña, es decir, justo en la cola. Esto es
precisamente lo que motiva la sección 7: reportar percentiles sin intervalo de
confianza es indefendible.

**Conformidad: CORRECTA en forma, umbrales POR VERIFICAR.** Ver brecha **G5**.

---

## 7. Inferencia estadística

### 7.1 Réplicas independientes

La hora punta es una **simulación terminante** (el sistema empieza y acaba
vacío). El estimador de cada KPI $Y$ es la media sobre $n$ réplicas:

$$\bar{Y}=\frac{1}{n}\sum_{i=1}^{n}Y_i, \qquad s^{2}=\frac{1}{n-1}\sum_{i=1}^{n}\left(Y_i-\bar{Y}\right)^{2}$$

Las $Y_i$ son i.i.d. por construcción (semillas independientes), lo que legitima
la inferencia clásica.

> **Por qué NO se trunca un periodo de calentamiento.** El warm-up y las medias
> por lotes sirven para estimar el régimen estacionario de un sistema que nunca
> se vacía. Aplicarlos aquí eliminaría precisamente los transitorios de pico que
> constituyen el objeto del estudio.

### 7.2 Intervalo de confianza

$$\bar{Y}\ \pm\ \underbrace{t_{n-1,\,1-\alpha/2}\ \frac{s}{\sqrt{n}}}_{h}$$

Se usa la $t$ de Student y no la normal porque $n$ es pequeño y $\sigma$
desconocida. Precisión relativa:

$$\gamma=\frac{h}{|\bar{Y}|}$$

### 7.3 Parada secuencial

Se añaden réplicas hasta $\gamma \le \gamma^{*}$ (por defecto $0{,}05$). El
tamaño muestral requerido se estima por

$$n^{*}\ \ge\ \left(\frac{t_{n-1,\,1-\alpha/2}\ s}{\gamma^{*}\,|\bar{Y}|}\right)^{2}$$

Esto responde de forma auditable a la pregunta "¿por qué 30 réplicas y no 5?".

### 7.4 Comparación pareada con Números Aleatorios Comunes

Diferencias por réplica entre escenarios $A$ y $B$:

$$D_i = Y_i^{B}-Y_i^{A}, \qquad \bar{D}\ \pm\ t_{n-1,\,1-\alpha/2}\frac{s_D}{\sqrt{n}}$$

La varianza de la diferencia es

$$\operatorname{Var}\left[D\right]=\operatorname{Var}\left[Y^{A}\right]+\operatorname{Var}\left[Y^{B}\right]-2\operatorname{Cov}\left[Y^{A},Y^{B}\right]$$

Si CRN induce $\operatorname{Cov}>0$, la varianza cae y el intervalo se estrecha
sin coste computacional adicional. El código reporta la correlación empírica
$\hat{r}$ y la reducción de varianza

$$\Delta_{\text{var}}=1-\frac{\operatorname{Var}[D]}{\operatorname{Var}[Y^{A}]+\operatorname{Var}[Y^{B}]}$$

para poder **demostrar** que la técnica funciona en lugar de asumirlo. En la
validación se obtuvieron $\hat{r}\in[0{,}58,\ 0{,}95]$ y reducciones de hasta el
90 %.

El efecto se declara significativo cuando el intervalo de $\bar{D}$ excluye el
cero.

**Conformidad: CORRECTA.** Es el procedimiento estándar (Law & Kelton) para
estudios de simulación defendibles.

---

## 8. Identidades de validación

No están implementadas todavía, pero son los contrastes que hay que ejecutar
antes de presentar nada. Son la diferencia entre un modelo que corre y un modelo
que se sabe correcto.

### 8.1 Ley de Little

$$L=\lambda W$$

Debe cumplirse por subsistema con error relativo inferior al 1 %. Si no se
cumple, hay un error de contabilidad en la instrumentación.

### 8.2 Conservación de flujo

$$\text{pax generados}=\text{pax procesados}+\text{pax en sistema al cierre}$$

### 8.3 Contraste contra aproximación analítica

Para cada recurso, la aproximación de Kingman–Sakasegawa para G/G/c:

$$W_q\ \approx\ \left(\frac{C_a^{2}+C_s^{2}}{2}\right)\left(\frac{\rho^{\sqrt{2(c+1)}-1}}{c\,(1-\rho)}\right)\mathbb{E}[S]$$

En un escenario **sin bloqueo ni aforo**, la simulación debe reproducir esta
predicción dentro del intervalo de confianza. Si diverge, el error está en el
modelo, no en la realidad. Es la prueba de verificación más barata y más
convincente ante un revisor.

### 8.4 Contraste de las leyes de servicio

Kolmogórov–Smirnov entre la muestra generada y la distribución teórica, usando
los cuantiles de SciPy (`theoretical_quantile`):

$$D_N=\sup_x\left|F_N(x)-F(x)\right|$$

---

## 9. Brechas declaradas

| ID | Brecha | Impacto | Prioridad |
|---|---|---|---|
| **G1** | Llegadas Poisson homogéneas en lugar de perfil de show-up por vuelo | Alto: subestima los picos, que son el objeto del estudio | Alta |
| **G2** | Sin validación contra datos históricos de LPA | **Crítico**: sin esto el modelo es una hipótesis, no una predicción | Máxima |
| **G3** | Monitor por muestreo a paso fijo en lugar de integración temporal ponderada | Medio: sesga $\bar{L}$ si el paso es grueso | Media |
| **G4** | Superficies zonales inventadas | **Crítico**: toda la analítica espacial depende de ellas | Máxima |
| **G5** | Umbrales ADRM no verificados contra la edición vigente | Alto: la clasificación LoS podría ser incorrecta | Alta |
| **G6** | Sin Lado Aire | Alto para un estudio integral | Media |
| **G7** | Sin equipaje, embarque, ni llegadas/recogida | Medio: el estudio cubre sólo el flujo de salida hasta fronteras | Media |
| **G8** | Identidades de validación (§8) no implementadas | Alto: verificación pendiente | Alta |

---

## 10. Nota sobre el encuadre normativo

**IATA ADRM — Lado Tierra: encuadre correcto.** El ADRM es efectivamente el
marco de referencia para dimensionar procesos de terminal, y las métricas
implementadas (tiempos máximos de cola, espacio por pasajero, clases de Nivel de
Servicio) son las suyas. La simulación de eventos discretos es además el método
que el propio ADRM recomienda cuando las fórmulas estáticas dejan de ser
suficientes.

**OACI Anexo 14 — Lado Aire: encuadre incorrecto.** Conviene corregirlo antes de
presentar nada, porque un revisor técnico lo detectará de inmediato. El Anexo 14
es una norma de **características físicas y de diseño** del aeródromo:
dimensiones de pista, franjas, superficies limitadoras de obstáculos, RESA,
señalización, iluminación. **No contiene métodos de capacidad ni de
throughput.** Es el documento que dice si una pista está bien construida, no
cuántos movimientos por hora admite.

Para el Lado Aire, la referencia adecuada según lo que se quiera modelar:

| Objetivo | Documento |
|---|---|
| Geometría y diseño del aeródromo | OACI Anexo 14 Vol. I + Doc 9157 (Manual de Diseño de Aeródromos) |
| Planificación y capacidad | OACI Doc 9184 (Manual de Planificación de Aeropuertos) |
| Separaciones y estela turbulenta | OACI Doc 4444 (PANS-ATM), **no** el Anexo 14 |
| Capacidad declarada y slots | IATA WSG (Worldwide Airport Slot Guidelines) |
| Métodos de cálculo de capacidad de pista | FAA AC 150/5060-5; metodología de capacidad de EUROCONTROL |

Recomendación: mantener el Anexo 14 como fuente de la **geometría** del modelo
de Lado Aire, y tomar las reglas de **separación y capacidad** del Doc 4444 y de
la metodología de capacidad declarada. Presentarlo así demuestra dominio del
marco normativo; presentar "capacidad según Anexo 14" demuestra lo contrario.
