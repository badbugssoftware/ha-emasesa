<p align="center">
  <img src="https://raw.githubusercontent.com/badbugssoftware/ha-emasesa/main/brand/icon.png" alt="EMASESA para Home Assistant" width="160">
</p>

<h1 align="center">EMASESA (Aguas de Sevilla) para Home Assistant</h1>

[![HACS: repositorio personalizado](https://img.shields.io/badge/HACS-repositorio%20personalizado-41BDF5?style=for-the-badge&logo=home-assistant&logoColor=white)](https://hacs.xyz/docs/faq/custom_repositories/)
[![Home Assistant](https://img.shields.io/badge/Home%20Assistant-%E2%89%A5%202024.6-41BDF5?style=for-the-badge&logo=home-assistant&logoColor=white)](https://www.home-assistant.io/)
[![Licencia MIT](https://img.shields.io/badge/licencia-MIT-3DA639?style=for-the-badge)](LICENSE)

[![Validate](https://github.com/badbugssoftware/ha-emasesa/actions/workflows/validate.yml/badge.svg)](https://github.com/badbugssoftware/ha-emasesa/actions/workflows/validate.yml)
[![Lint](https://github.com/badbugssoftware/ha-emasesa/actions/workflows/lint.yml/badge.svg)](https://github.com/badbugssoftware/ha-emasesa/actions/workflows/lint.yml)

Integración **no oficial** que trae a Home Assistant tu **consumo de agua de EMASESA**
(Empresa Metropolitana de Abastecimiento y Saneamiento de Aguas de Sevilla), leyendo la
misma API privada que usa la app **Mi Emasesa**.

Está pensada para contadores con **telelectura NB-IoT**, que reportan consumo **diario y
horario**, e importa todo el histórico horario al **panel de Energía** de Home Assistant,
incluido el **coste en euros** calculado con el **simulador oficial de tarifas** de EMASESA.

---

> [!WARNING]
> **Esto usa una API privada, no documentada y no oficial de EMASESA.**
>
> - No está **afiliada, avalada ni soportada por EMASESA**. Es un proyecto personal.
> - EMASESA puede **cambiar o cerrar su backend en cualquier momento y sin previo aviso**,
>   y entonces la integración dejará de funcionar hasta que alguien la adapte.
> - Se conecta **con tu propia cuenta** y solo descarga **tus propios datos de consumo**
>   (interoperabilidad con datos propios). No accede a datos de terceros.
> - Úsala bajo tu responsabilidad. Un uso abusivo (sondeos muy agresivos) puede provocar
>   que EMASESA bloquee tu cuenta: respeta el intervalo por defecto.

---

## Índice

- [Qué hace](#qué-hace)
- [Entidades que crea](#entidades-que-crea)
- [Estadísticas de largo plazo](#estadísticas-de-largo-plazo)
- [Requisitos](#requisitos)
- [Instalación](#instalación)
- [Configuración](#configuración)
- [Opciones](#opciones)
- [Panel de Energía](#panel-de-energía)
- [Servicios](#servicios)
- [Notas importantes](#notas-importantes)
- [Ejemplos de automatización](#ejemplos-de-automatización)
- [FAQ y solución de problemas](#faq-y-solución-de-problemas)
- [Cómo funciona por dentro](#cómo-funciona-por-dentro)
- [Contribuir](#contribuir)
- [Créditos](#créditos)
- [Licencia](#licencia)

---

## Qué hace

- **Lee el contador por telelectura**: índice acumulado, consumo del día, desglose
  diurno / nocturno y consumo medio diario del ciclo.
- **Rellena el histórico horario** en las estadísticas de largo plazo de Home Assistant
  (unos **60 días** en el primer arranque; después mantiene los últimos días y **tapa
  huecos** si HA o la API han estado caídos).
- **Calcula el coste real** del periodo de facturación en curso llamando al
  **simulador oficial de EMASESA**, así que aplica **la tarifa de tu contrato**
  (cuota fija + tramos + saneamiento + depuración + canon autonómico + IVA) sin que la
  integración tenga que mantener tablas de precios.
- **Alimenta el panel de Energía** con dos estadísticas externas: **m³ consumidos** y
  **€ gastados**.
- **Facturación**: importe de la última factura, importe pendiente de pago y días que
  faltan para la siguiente.
- **Avisos**: posible **fuga** por caudal nocturno continuo, **avería del contador** con
  consumo estimado, **incidencia pendiente** en tu suministro e **incidencias de la red**
  de EMASESA cerca de tu casa.
- **Estado de los embalses** que abastecen a Sevilla.
- **Configuración 100 % por interfaz**: sin YAML, con soporte de **doble factor (SMS)**,
  **reautenticación** y **selección de contrato** cuando tienes varios suministros.

```mermaid
flowchart LR
    A["API privada<br/>Mi Emasesa"] --> B["Coordinator<br/>(cada 45 min)"]
    B --> C["9 sensores +<br/>4 binary_sensors"]
    B --> D["Estadísticas externas<br/>emasesa:…_water<br/>emasesa:…_water_cost"]
    D --> E["Panel de Energía<br/>(agua + coste)"]
```

## Entidades que crea

La integración crea **un dispositivo por contrato**, llamado `EMASESA <nº de contrato>`,
con el fabricante, modelo y número de serie reales del contador:

```
┌─ Dispositivo: EMASESA 12345678 ──────────────────────────────────┐
│  Fabricante: …     Modelo: …     Nº de serie: …                  │
│                                                                  │
│  Índice del contador ................... 1 284,317 m³            │
│  Consumo del día ....................... 312 L                   │
│  Consumo medio diario .................. 287 L                   │
│  Coste del periodo ..................... 41,86 €                 │
│  Precio del agua ....................... 2,1934 €/m³             │
│  Última factura ........................ 68,42 €                 │
│  Importe pendiente ..................... 0,00 €                  │
│  Días para la próxima factura .......... 34 d                    │
│  Embalses .............................. 61,4 %                  │
│                                                                  │
│  Posible fuga .......................... Correcto                │
│  Incidencia de red cercana ............. Correcto                │
│  ⚙ Avería del contador ................. Correcto                │
│  ⚙ Incidencia pendiente ................ Correcto                │
└──────────────────────────────────────────────────────────────────┘
```

### Sensores

| Entidad | `entity_id` de ejemplo | Unidad | `device_class` | `state_class` |
| --- | --- | --- | --- | --- |
| **Índice del contador** | `sensor.emasesa_12345678_indice_del_contador` | `m³` | `water` | `total_increasing` |
| **Consumo del día** | `sensor.emasesa_12345678_consumo_del_dia` | `L` | – | – (valor diario) |
| **Consumo medio diario** | `sensor.emasesa_12345678_consumo_medio_diario` | `L` | – | `measurement` |
| **Coste del periodo** | `sensor.emasesa_12345678_coste_del_periodo` | `EUR` | `monetary` | – |
| **Precio del agua** | `sensor.emasesa_12345678_precio_del_agua` | `EUR/m³` | – | `measurement` |
| **Última factura** | `sensor.emasesa_12345678_ultima_factura` | `EUR` | `monetary` | – |
| **Importe pendiente** | `sensor.emasesa_12345678_importe_pendiente` | `EUR` | `monetary` | – |
| **Días para la próxima factura** | `sensor.emasesa_12345678_dias_para_la_proxima_factura` | `d` | – | – |
| **Embalses** | `sensor.emasesa_12345678_embalses` | `%` | – | `measurement` |
| **Cada embalse** ⚪ | `sensor.emasesa_12345678_aracena` | `%` | – | `measurement` |

⚪ Se crea un sensor por cada embalse (Aracena, Zufre, La Minilla…), pero vienen
**desactivados**: el desglose ya está en los atributos del sensor conjunto, y seis
entidades más recargan la lista sin aportar nada a la mayoría. Si quieres graficar la
evolución de uno concreto, actívalo desde el dispositivo y a partir de ahí tendrá
histórico.

### Sensores binarios

| Entidad | `entity_id` de ejemplo | `device_class` | Categoría | Se activa cuando… |
| --- | --- | --- | --- | --- |
| **Posible fuga** | `binary_sensor.emasesa_12345678_posible_fuga` | `problem` | – | Durante **3 noches seguidas** ninguna hora entre las **02:00 y las 05:00** baja de 1 L |
| **Incidencia de red cercana** | `binary_sensor.emasesa_12345678_incidencia_de_red_cercana` | `problem` | – | Hay una actuación o avería de la red de EMASESA dentro del radio configurado |
| **Avería del contador** | `binary_sensor.emasesa_12345678_averia_del_contador` | `problem` | Diagnóstico | EMASESA marca el contador en avería y **estima** el consumo |
| **Incidencia pendiente** | `binary_sensor.emasesa_12345678_incidencia_pendiente` | `problem` | Diagnóstico | Hay una orden de trabajo o incidencia abierta en tu suministro |

> El `entity_id` real se construye con el **número de contrato** que aparece en tu
> factura. Compruébalo en *Ajustes → Dispositivos y servicios → EMASESA*.

<details>
<summary><b>Atributos de cada entidad</b></summary>

**Índice del contador**

| Atributo | Descripción |
| --- | --- |
| `indice_litros` | Lectura acumulada del contador, en litros (valor crudo de la API) |
| `fecha_dato` | Fecha del último dato de consumo disponible |
| `fecha_lectura_contador` | Fecha de la última lectura reportada por el contador |
| `numero_serie`, `modelo` | Identificación del contador |
| `telelectura_nbiot` | Si el contador es NB-IoT |
| `estadistica_historica` | Id exacto de la estadística externa de consumo (útil para el panel de Energía) |

**Consumo del día**

| Atributo | Descripción |
| --- | --- |
| `fecha` | Día al que corresponde el consumo |
| `consumo_diurno_litros` | Litros consumidos en franja diurna |
| `consumo_nocturno_litros` | Litros consumidos en franja nocturna |

**Consumo medio diario**

| Atributo | Descripción |
| --- | --- |
| `valoracion`, `valoracion_texto` | Valoración del consumo que hace la propia EMASESA |
| `ultima_telelectura` | Fecha de la última telelectura recibida |

**Coste del periodo**

| Atributo | Descripción |
| --- | --- |
| `consumo_periodo_m3` | m³ consumidos en el ciclo de facturación en curso |
| `precio_efectivo_eur_m3` | € por m³ resultante (coste ÷ consumo) |
| `periodo_desde` | Fecha de fin de la última factura, es decir, inicio del ciclo actual |
| `proxima_factura` | Fecha prevista de la próxima facturación |

**Última factura**

| Atributo | Descripción |
| --- | --- |
| `numero` | Número de factura |
| `fecha_emision` | Fecha de emisión |
| `estado_cobro` | Estado del cobro |
| `consumo_m3`, `dias_facturados` | Consumo y días facturados |
| `periodo_desde`, `periodo_hasta` | Periodo facturado |

**Días para la próxima factura**: `periodo_desde` y `proxima_factura`.

**Embalses**: `fecha`, `volumen_hm3`, `capacidad_hm3` y `por_embalse`, una lista con el
nombre, el porcentaje de llenado y el volumen de cada embalse.

**Posible fuga**

| Atributo | Descripción |
| --- | --- |
| `noches_analizadas` | Noches completas usadas en el análisis |
| `consumo_minimo_nocturno_l` | Litros de la hora de madrugada con menos consumo |
| `desde` | Primera noche de la racha detectada |

**Incidencia de red cercana**

| Atributo | Descripción |
| --- | --- |
| `numero` | Incidencias dentro del radio |
| `radio_m` | Radio configurado, en metros |
| `total_ciudad` | Incidencias activas en toda la ciudad |
| `mas_cercana`, `distancia_m`, `direccion` | Datos de la más próxima |
| `incidencias` | Hasta 10 incidencias con categoría, dirección, inicio, tipo y distancia |

</details>

**¿Por qué “Consumo del día” no tiene `state_class`?** Porque es un valor que sube y baja
de un día a otro; si el `recorder` intentara acumularlo como suma, el histórico saldría
mal. El histórico correcto lo aportan las estadísticas externas y el índice del contador.

**¿Por qué “Precio del agua” baja según consumes?** Porque reparte la **cuota fija** del
ciclo entre más m³. Es el precio *efectivo* del periodo, no una tarifa marginal.

## Estadísticas de largo plazo

Además de los sensores, la integración escribe dos **estadísticas externas** con detalle
**horario**, que son las que conviene usar en el panel de Energía:

| `statistic_id` | Unidad | Contenido |
| --- | --- | --- |
| `emasesa:<contrato>_water` | `m³` | Consumo acumulado del contador, hora a hora |
| `emasesa:<contrato>_water_cost` | `EUR` | Coste acumulado, hora a hora, al precio efectivo del ciclo |

> [!NOTE]
> `<contrato>` es el **identificador interno del contrato** que devuelve la API, que
> **no tiene por qué coincidir** con el número de contrato de tu factura. Si tienes dudas
> sobre el valor exacto, míralo en el atributo `estadistica_historica` del sensor
> *Índice del contador*, o búscalo en
> *Herramientas para desarrolladores → Estadísticas* escribiendo `EMASESA`.

La importación del consumo es **idempotente**: se basa en el índice absoluto del contador,
así que reimportar los mismos días no duplica consumo. El coste, en cambio, se acumula
**por incrementos** y nunca reescribe el pasado, porque el precio efectivo va cambiando
dentro del ciclo.

## Requisitos

| Requisito | Detalle |
| --- | --- |
| **Contador con telelectura NB-IoT** | Es lo que da el detalle **diario y horario**. EMASESA tiene ya telegestionado en torno al **80 % del parque de contadores**; si el tuyo aún es mecánico, la integración arrancará pero apenas tendrás datos. |
| **Cuenta de la Oficina Virtual / app Mi Emasesa** | Con acceso al contrato que quieras monitorizar. |
| **Home Assistant ≥ 2024.6** | Requerido por el config flow y la API de estadísticas que se usa. |
| **Integración `recorder` activa** | Es una dependencia declarada: sin ella no hay estadísticas de largo plazo ni panel de Energía. Viene activada de serie salvo que la hayas desactivado a mano. |
| **Ubicación de tu casa configurada** | Solo para el sensor de *incidencia de red cercana*, que compara las coordenadas de las actuaciones de EMASESA con las de tu instalación. |

## Instalación

### Opción A — HACS (recomendada)

Este repositorio todavía no está en el índice por defecto de HACS, así que se añade como
**repositorio personalizado**:

1. Abre **HACS** en Home Assistant.
2. Menú **⋮** (arriba a la derecha) → **Repositorios personalizados**.
3. Pega la URL `https://github.com/badbugssoftware/ha-emasesa` y elige la categoría
   **Integration**. Pulsa **Añadir**.
4. Busca **EMASESA (Aguas de Sevilla)** en HACS y pulsa **Descargar**.
5. **Reinicia** Home Assistant.

[![Añadir repositorio a HACS](https://img.shields.io/badge/HACS-a%C3%B1adir%20repositorio-41BDF5?style=flat-square&logo=home-assistant&logoColor=white)](https://my.home-assistant.io/redirect/hacs_repository/?owner=badbugssoftware&repository=ha-emasesa&category=integration)

### Opción B — Instalación manual

1. Descarga la última versión del repositorio.
2. Copia la carpeta `custom_components/emasesa/` dentro de la carpeta
   `config/custom_components/` de tu instalación, de forma que quede
   `config/custom_components/emasesa/manifest.json`.
3. **Reinicia** Home Assistant.

## Configuración

Todo se hace desde la interfaz; no hay nada que poner en `configuration.yaml`.

1. Ve a **Ajustes → Dispositivos y servicios → Añadir integración** y busca **EMASESA**.

   [![Añadir integración](https://img.shields.io/badge/Mis%20enlaces-a%C3%B1adir%20integraci%C3%B3n-41BDF5?style=flat-square&logo=home-assistant&logoColor=white)](https://my.home-assistant.io/redirect/config_flow_start/?domain=emasesa)

2. **Usuario**: tu **NIF / DNI / NIE**, que es el identificador con el que entras en la
   Oficina Virtual (se normaliza a mayúsculas automáticamente).
   **Contraseña**: la misma de la Oficina Virtual / app Mi Emasesa.

3. **Doble factor (si tu cuenta lo tiene activado)**: EMASESA te enviará un **código por
   SMS** (o correo) y la integración te pedirá el **Código de verificación**.
   Al terminar, Home Assistant queda registrado como **dispositivo de confianza**, así que
   **no se te volverá a pedir** en cada actualización ni en cada reinicio.

4. **Selección de contrato**: si en tu cuenta hay varios puntos de suministro, elige cuál
   quieres monitorizar; se muestran como `nº de contrato — dirección de suministro`.
   Si solo hay uno, este paso se salta.

Puedes **repetir el proceso** para añadir más contratos: cada uno se crea como una entrada
independiente, con su propio dispositivo y sus propias estadísticas.

### Reautenticación

Si cambias la contraseña en la Oficina Virtual, o EMASESA invalida la sesión, Home
Assistant mostrará el aviso de **“Volver a autenticar”**. Introduce la contraseña nueva y,
si hace falta, el código SMS: el dispositivo se vuelve a registrar como de confianza
automáticamente.

## Opciones

En la tarjeta de la integración, **Configurar**:

| Opción | Clave | Por defecto | Rango |
| --- | --- | --- | --- |
| Ubicación del suministro | `latitude` / `longitude` | La de Home Assistant | – |
| Radio de incidencias cercanas (metros) | `incident_radius_m` | `1000` | `100` – `20000` |

### El intervalo de sondeo no se configura

Y es a propósito. **Se adapta solo:**

| Situación | Vuelve a mirar en |
| --- | --- |
| Ya tiene el dato del día | **6 horas** |
| Esperando la publicación | **2 horas** |

EMASESA publica la telelectura **una vez al día y a una hora que varía**: medido en una
instalación real, un día el dato llevaba 26 h de retraso y otro 12. Con un intervalo fijo
o machacas la API o llegas tarde, y un número puesto a mano no puede distinguir las dos
situaciones.

Bajarlo tampoco serviría de nada: el dato no llega antes por mirar más veces, y cada
ciclo son unas diez llamadas a una API privada. Como cada instalación recibe su dato en
un momento distinto, además acaban desfasadas solas en lugar de llamar todas a la vez.

> Si vienes de una versión anterior, el ajuste `scan_minutes` que tuvieras guardado se
> retira solo al arrancar.

## Panel de Energía

Ve a **Ajustes → Paneles → Energía**.

### 1. Consumo de agua

En la sección **Agua**, pulsa **Añadir consumo de agua** y selecciona la estadística:

```
emasesa:<contrato>_water        →  aparece como "EMASESA consumo <contrato>"
```

### 2. Coste

En el mismo diálogo, elige **“Usar una entidad con el coste total”** (*Use an entity
tracking the total costs*) y selecciona:

```
emasesa:<contrato>_water_cost   →  aparece como "EMASESA coste <contrato>"
```

Así el panel muestra los euros calculados con la tarifa real de tu contrato, en vez de
multiplicar por un precio fijo.

> [!CAUTION]
> **No añadas a la vez el sensor `Índice del contador` y la estadística
> `emasesa:<contrato>_water`.**
>
> Los dos representan **el mismo contador**, así que el panel de Energía **contaría el
> consumo dos veces**. Elige uno:
>
> - **Recomendado: la estadística externa** `emasesa:<contrato>_water`, porque trae el
>   detalle **horario** correcto y el histórico completo de los últimos ~60 días.
> - El sensor `Índice del contador` solo tiene sentido si prefieres que el panel se
>   alimente del estado en vivo; en ese caso, deja la estadística para tarjetas e
>   informes.
>
> Si ya lo habías añadido mal, quita la fuente duplicada en el panel de Energía y, si
> hiciera falta, borra las estadísticas sobrantes desde
> *Herramientas para desarrolladores → Estadísticas*.

## Servicios

### `emasesa.simular_factura`

Pregunta al **simulador oficial de EMASESA** cuánto costaría un consumo determinado en un
periodo. Devuelve respuesta, así que se usa con `response_variable`.

| Campo | Obligatorio | Descripción |
| --- | --- | --- |
| `config_entry_id` | Sí | Contrato de EMASESA sobre el que simular |
| `consumo` | Sí | Metros cúbicos a simular (`0` – `1000`) |
| `fecha_desde` | No | Inicio del periodo. Por defecto, el del ciclo en curso |
| `fecha_hasta` | No | Fin del periodo. Por defecto, hoy |

Devuelve `importe`, `consumo`, `dias` y la lista de `conceptos` con su desglose
(`concepto`, `unidades`, `precio_unitario`, `total_con_iva`).

```yaml
action:
  - service: emasesa.simular_factura
    data:
      config_entry_id: "{{ config_entry_id('sensor.emasesa_12345678_coste_del_periodo') }}"
      consumo: 25
    response_variable: simulacion
  - service: notify.persistent_notification
    data:
      title: "Simulación EMASESA"
      message: "25 m³ costarían {{ simulacion.importe }} € ({{ simulacion.dias }} días)."
```

### `emasesa.recargar_historico`

Vuelve a importar el consumo horario de los últimos N días a las estadísticas. Útil si
detectas huecos en el panel de Energía.

| Campo | Obligatorio | Descripción |
| --- | --- | --- |
| `config_entry_id` | Sí | Contrato de EMASESA a recargar |
| `dias` | No | Días hacia atrás a recargar (`1` – `365`, por defecto `60`) |

### Diagnósticos

En la tarjeta del dispositivo, **Descargar diagnóstico** genera un JSON con el estado de
la integración. El NIF, la contraseña, el identificador de dispositivo y la dirección de
suministro se **redactan automáticamente**, pero **revísalo igualmente** antes de pegarlo
en una incidencia.

## Notas importantes

- ⏳ **Los datos llegan con 1–2 días de retraso.** Así funciona la telelectura de EMASESA:
  el contador NB-IoT envía sus lecturas por lotes y el backend las consolida después. No
  esperes ver en Home Assistant la ducha que te acabas de dar. El sensor
  *Consumo del día* muestra el **último día disponible**, que no tiene por qué ser hoy.
  Lo mismo vale para el sensor de *posible fuga*: avisa con ese mismo retraso.
- 💶 **El coste es una estimación**, no una factura. Lo calcula el **simulador oficial de
  EMASESA** con el consumo real de tu ciclo, así que aplica tu tarifa de verdad, pero
  puede diferir de la factura final por redondeos, regularizaciones, prorrateos,
  bonificaciones o cambios de tarifa a mitad de periodo. El simulador trabaja con m³
  enteros.
- 📅 En el **primer arranque** se importan unos **60 días** de histórico horario; el
  proceso tarda un poco y las estadísticas pueden no verse hasta pasados unos minutos.
- 🧭 Las horas se interpretan en **Europe/Madrid**, incluidos los cambios de hora
  (el día de 25 horas de octubre está contemplado).
- 🧩 Los datos “extra” (facturas, embalses e incidencias) son **opcionales**: si esas
  llamadas fallan, la integración sigue funcionando y solo se quedan sin valor esos
  sensores.
- 🔐 Tus credenciales se guardan en el almacén de configuración de Home Assistant, como en
  cualquier otra integración, y solo se envían a EMASESA.

### Sin telelectura NB-IoT

EMASESA lleva telegestionado en torno al **80 % del parque de contadores**, pero si el
tuyo todavía no lo está, la API no publica consumo hora a hora para tu contrato.

Con un contador sin telelectura, esto es lo que tienes:

| | |
| --- | --- |
| ✅ **Sigue funcionando** | Lectura del contador, facturas, importe pendiente, coste del periodo, consumo medio, embalses e incidencias de red |
| ❌ **No funciona** | El histórico horario del panel de Energía y la detección de fugas nocturnas, que necesitan datos hora a hora |

No hay nada que arreglar del lado de Home Assistant: depende del contador que tengas
instalado. El día que EMASESA empiece a publicar datos horarios de tu suministro,
empezarán a aparecer solos.

Puedes comprobar tu caso en el atributo `telelectura_nbiot` del sensor
*Índice del contador*.

## Ejemplos de automatización

> Sustituye `12345678` por el número de tu contrato en los `entity_id`.

### Aviso de consumo diario alto

```yaml
automation:
  - alias: "Agua · Consumo diario alto"
    description: >-
      Avisa cuando el último día reportado por EMASESA supera los 500 litros.
    mode: single
    trigger:
      - platform: numeric_state
        entity_id: sensor.emasesa_12345678_consumo_del_dia
        above: 500
    action:
      - service: notify.persistent_notification
        data:
          title: "Consumo de agua alto"
          message: >-
            El {{ state_attr('sensor.emasesa_12345678_consumo_del_dia', 'fecha') }}
            se consumieron
            {{ states('sensor.emasesa_12345678_consumo_del_dia') }} L
            (media del ciclo:
            {{ states('sensor.emasesa_12345678_consumo_medio_diario') }} L/día).
```

### Aviso de posible fuga

El sensor binario ya hace el trabajo: se enciende cuando durante **tres noches seguidas**
no hay ni una sola hora de madrugada con el consumo a cero, que es el síntoma clásico de
una cisterna que pierde o de un goteo.

```yaml
automation:
  - alias: "Agua · Posible fuga"
    mode: single
    trigger:
      - platform: state
        entity_id: binary_sensor.emasesa_12345678_posible_fuga
        to: "on"
        for: "00:10:00"
    action:
      - service: notify.persistent_notification
        data:
          title: "⚠️ Posible fuga de agua"
          message: >-
            Llevas
            {{ state_attr('binary_sensor.emasesa_12345678_posible_fuga',
                          'noches_analizadas') }}
            noches con consumo continuo de madrugada (mínimo
            {{ state_attr('binary_sensor.emasesa_12345678_posible_fuga',
                          'consumo_minimo_nocturno_l') }} L/h,
            desde el
            {{ state_attr('binary_sensor.emasesa_12345678_posible_fuga', 'desde') }}).
            Revisa cisternas, grifos y riego.
```

<details>
<summary>Variante sin el sensor binario, con tu propio umbral nocturno</summary>

```yaml
automation:
  - alias: "Agua · Caudal nocturno por encima de lo normal"
    mode: single
    trigger:
      - platform: state
        entity_id: sensor.emasesa_12345678_consumo_del_dia
        attribute: fecha
    condition:
      - condition: template
        value_template: >-
          {{ state_attr('sensor.emasesa_12345678_consumo_del_dia',
                        'consumo_nocturno_litros') | float(0) > 50 }}
    action:
      - service: notify.persistent_notification
        data:
          title: "Caudal nocturno alto"
          message: >-
            La noche del
            {{ state_attr('sensor.emasesa_12345678_consumo_del_dia', 'fecha') }}
            se registraron
            {{ state_attr('sensor.emasesa_12345678_consumo_del_dia',
                          'consumo_nocturno_litros') }} L con la casa en reposo.
```

</details>

### Aviso cuando el gasto del ciclo pasa de un umbral

```yaml
automation:
  - alias: "Agua · Coste del periodo por encima de lo previsto"
    mode: single
    trigger:
      - platform: numeric_state
        entity_id: sensor.emasesa_12345678_coste_del_periodo
        above: 60
    action:
      - service: notify.persistent_notification
        data:
          title: "Factura de agua en camino"
          message: >-
            Llevas {{ states('sensor.emasesa_12345678_coste_del_periodo') }} € este ciclo
            ({{ state_attr('sensor.emasesa_12345678_coste_del_periodo',
                           'consumo_periodo_m3') }} m³).
            Quedan {{ states('sensor.emasesa_12345678_dias_para_la_proxima_factura') }}
            días para la próxima factura.
```

### Cortar el riego si hay una avería de red cerca

```yaml
automation:
  - alias: "Agua · Incidencia de red cercana"
    mode: single
    trigger:
      - platform: state
        entity_id: binary_sensor.emasesa_12345678_incidencia_de_red_cercana
        to: "on"
    action:
      - service: switch.turn_off
        target:
          entity_id: switch.riego_jardin
      - service: notify.persistent_notification
        data:
          title: "Incidencia de EMASESA cerca"
          message: >-
            {{ state_attr('binary_sensor.emasesa_12345678_incidencia_de_red_cercana',
                          'mas_cercana') }}
            a {{ state_attr('binary_sensor.emasesa_12345678_incidencia_de_red_cercana',
                            'distancia_m') }} m
            ({{ state_attr('binary_sensor.emasesa_12345678_incidencia_de_red_cercana',
                           'direccion') }}). Riego apagado por si hay corte.
```

## FAQ y solución de problemas

<details>
<summary><b>“Usuario o contraseña incorrectos”, pero mis credenciales son correctas</b></summary>

Este es el clásico. El servidor de EMASESA **filtra por `User-Agent`**: si la petición no
se identifica exactamente como la app oficial (`okhttp/2.1.0`), responde un **401 con un
mensaje falso de contraseña incorrecta**, aunque las credenciales sean perfectas.

**Ya está resuelto en la integración**: todas las llamadas usan las mismas cabeceras que la
app. Si aun así te sale el error:

1. Comprueba que puedes entrar en la [Oficina Virtual](https://www.emasesaonline.com/)
   con ese NIF/NIE y esa contraseña.
2. Usa el **NIF/DNI/NIE**, no el correo electrónico ni el número de contrato.
3. Escribe el NIF **sin espacios ni guiones**; la letra da igual, se pasa a mayúsculas.
4. Si has fallado varias veces seguidas, EMASESA puede haber bloqueado temporalmente la
   cuenta: entra en la web y desbloquéala.

</details>

<details>
<summary><b>Me vuelve a pedir el código SMS una y otra vez</b></summary>

Tras el primer login, la integración registra Home Assistant como **dispositivo de
confianza** (igual que hace la app), y eso es lo que evita el 2FA en cada sondeo. Si te lo
vuelve a pedir:

- Puede que EMASESA haya **retirado la confianza** al dispositivo (cambio de contraseña,
  limpieza de dispositivos en la app, mucho tiempo sin usarlo). Completa la
  **reautenticación** que te propone Home Assistant y volverá a quedar registrado.
- Si has **borrado y vuelto a añadir** la integración, se genera un `id_dispositivo` nuevo
  y toca un SMS más. Es normal, y solo una vez.
- Revisa en la app Mi Emasesa que no hayas alcanzado el límite de dispositivos de
  confianza; borra los que ya no uses.
- Con el log en `debug` verás el canal por el que EMASESA envía el código
  (`S` = SMS, `C` = correo).

</details>

<details>
<summary><b>No aparece consumo horario o los sensores están vacíos</b></summary>

Lo más probable es que **tu contador no tenga telelectura NB-IoT**; tienes los detalles
en [Sin telelectura NB-IoT](#sin-telelectura-nb-iot). Compruébalo en el atributo
`telelectura_nbiot` del sensor *Índice del contador*: si no es afirmativo, EMASESA no
publica detalle horario para ese contrato y solo tendrás lo que dé la lectura periódica.

También puede ser simplemente el **retraso de 1–2 días** de la telelectura, sobre todo si
acabas de instalar la integración o si el contador lleva poco tiempo telegestionado.

Y si el binario *Avería del contador* está encendido, EMASESA está **estimando** tu
consumo: los datos no son lecturas reales.

</details>

<details>
<summary><b>El panel de Energía me duplica el consumo</b></summary>

Casi seguro que tienes añadidos **a la vez** el sensor *Índice del contador* y la
estadística `emasesa:<contrato>_water`. Quita uno de los dos; ver
[Panel de Energía](#panel-de-energía).

</details>

<details>
<summary><b>Tengo huecos en el histórico del panel de Energía</b></summary>

Llama al servicio [`emasesa.recargar_historico`](#emasesarecargar_historico) con los días
que quieras reimportar. La importación es idempotente: no duplica consumo.

</details>

<details>
<summary><b>El coste no cuadra con mi factura</b></summary>

Es una **estimación del simulador oficial** para el consumo acumulado del ciclo en curso.
Diferencias habituales: el simulador redondea a m³ enteros y la factura real puede incluir
regularizaciones, prorrateos por cambio de tarifa, bonificaciones o conceptos ajenos al
consumo. Sirve para saber por dónde vas, no para cuadrar céntimos.

</details>

<details>
<summary><b>El sensor de incidencias cercanas nunca se enciende</b></summary>

Comprueba que tienes la **ubicación de tu casa** configurada en *Ajustes → Sistema →
General*: sin coordenadas no hay forma de calcular la distancia. Y sube el
**radio de incidencias** en las opciones si vives en una zona con pocas actuaciones.

</details>

<details>
<summary><b>Ha dejado de funcionar de golpe</b></summary>

Recuerda que esto depende de una **API privada que EMASESA puede cambiar sin avisar**.
Antes de abrir una incidencia:

1. Comprueba que la **app Mi Emasesa** funciona en tu móvil. Si tampoco va, es una caída
   de EMASESA: espera.
2. Reinicia Home Assistant y mira los logs.
3. Activa el log detallado y reproduce el fallo:

   ```yaml
   # configuration.yaml
   logger:
     default: warning
     logs:
       custom_components.emasesa: debug
   ```

4. Abre una incidencia en
   [GitHub](https://github.com/badbugssoftware/ha-emasesa/issues) con la versión de Home
   Assistant, la versión de la integración y el log o el diagnóstico, **quitando antes tu
   NIF, tu contraseña, los tokens y el número de contrato**.

</details>

<details>
<summary><b>¿Puedo monitorizar varios contratos?</b></summary>

Sí: añade la integración una vez por contrato. Cada uno crea su propio dispositivo, sus
entidades y su par de estadísticas.

</details>

<details>
<summary><b>¿Cómo borro las estadísticas si quiero empezar de cero?</b></summary>

En *Herramientas para desarrolladores → Estadísticas*, busca `EMASESA` y elimina las
entradas. En el siguiente sondeo, la integración volverá a importar el histórico
(unos 60 días).

</details>

## Cómo funciona por dentro

1. **Token de aplicación**: `POST /oauth2/token?grant_type=client_credentials` con la
   credencial `Basic` embebida en el APK (entorno de producción).
2. **Login de usuario**: `POST /login/autenticarUsuario` con NIF, contraseña e
   `id_dispositivo`, que devuelve el token `Bearer` de sesión. Si la cuenta tiene doble
   factor y el dispositivo no es de confianza, EMASESA manda un código y hay que repetir
   el login añadiendo el `pin`.
3. **Dispositivo de confianza**: `POST /dispositivos` con `confianza: "S"`, para que los
   siguientes logins no disparen otro SMS.
4. **Datos**: consumo horario por rango de fechas, último día disponible, información del
   contador, valoración del consumo del ciclo, simulación de factura, facturas, embalses y
   actuaciones en la red.
5. **Estadísticas**: el índice acumulado del contador (en litros) se convierte a m³ y se
   escribe como suma monotónica en `emasesa:<contrato>_water`; el coste se acumula por
   incrementos en `emasesa:<contrato>_water_cost`.

Dos detalles que cuestan horas si no se saben, y que ya están resueltos en el código:

- El servidor **exige `User-Agent: okhttp/2.1.0`**; con el de Python devuelve un 401 con
  un mensaje engañoso de credenciales incorrectas.
- `/oauth2/token` **exige `Content-Type: application/x-www-form-urlencoded`**; sin él
  responde 415.

## Contribuir

Las incidencias y los *pull requests* son bienvenidos. Lee
[CONTRIBUTING.md](CONTRIBUTING.md) antes de empezar: explica cómo montar el entorno, cómo
pasar los tests y el linter y, muy importante, **cómo compartir logs sin filtrar tus datos
personales**.

## Créditos

Este proyecto no existiría sin el trabajo previo de otras integraciones de agua españolas,
que sirvieron de referencia e inspiración:

- [**Canal de Isabel II**](https://github.com/miguelangel-nubla/homeassistant_canal_isabel_II)
  de [@miguelangel-nubla](https://github.com/miguelangel-nubla) — contadores de agua de
  Madrid.
- [**Aigües de Barcelona**](https://github.com/duhow/hass-aigues-barcelona)
  de [@duhow](https://github.com/duhow) — consumo de agua de Barcelona.

Y, cómo no, de la comunidad de Home Assistant y de HACS.

## Licencia

[MIT](LICENSE) © 2026 badbugssoftware.

*EMASESA y Mi Emasesa son marcas de la Empresa Metropolitana de Abastecimiento y
Saneamiento de Aguas de Sevilla, S.A. Este proyecto no está afiliado, patrocinado ni
respaldado por EMASESA.*
