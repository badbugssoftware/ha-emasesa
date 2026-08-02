# EMASESA para Home Assistant

Integración **no oficial** para consultar tu consumo de agua de **EMASESA** (Empresa
Metropolitana de Abastecimiento y Saneamiento de Aguas de Sevilla) en Home Assistant,
usando la misma API privada que la app **Mi Emasesa**.

Pensada para contadores con **telelectura (NB-IoT)**, que reportan consumo **diario y
horario**. Importa el histórico horario al **panel de Energía** de Home Assistant.

> ⚠️ Proyecto personal, sin relación con EMASESA. Usa la API interna de su app, que
> puede cambiar sin previo aviso. Úsalo con tu propia cuenta y bajo tu responsabilidad.

## Qué te da

- **Sensor `Índice del contador`** (m³): lectura acumulada del contador. `device_class:
  water`, `state_class: total_increasing` → apto para el panel de Energía.
- **Sensor `Consumo del día`** (litros): consumo del último día disponible, con desglose
  diurno/nocturno.
- **Estadística horaria** `emasesa:<contrato>_water`: histórico por horas para el panel
  de Energía (backfill de los últimos ~60 días en el primer arranque).
- Atributos del contador: fabricante, modelo, número de serie, si es NB-IoT, fechas.

## Instalación

### Opción A — HACS (repositorio personalizado)
1. HACS → Integraciones → menú ⋮ → **Repositorios personalizados**.
2. Añade la URL de este repo y categoría **Integration**.
3. Instala **EMASESA (Aguas de Sevilla)** y **reinicia** Home Assistant.

### Opción B — Manual
Copia `custom_components/emasesa/` dentro de la carpeta `config/custom_components/` de tu
Home Assistant y reinicia.

## Configuración

1. Ajustes → Dispositivos y servicios → **Añadir integración** → **EMASESA**.
2. Introduce tu **NIF/DNI/NIE** y **contraseña** de la Oficina Virtual / app Mi Emasesa.
3. Si tu cuenta tiene **doble factor**, introduce el código que te llegue por SMS/correo.
   Tras el primer acceso, el dispositivo queda como *de confianza* y no se te vuelve a
   pedir.
4. Si tienes varios contratos, elige el suministro.

El intervalo de actualización (por defecto 3 h) se ajusta en las **Opciones** de la
integración. El dato de telelectura se consolida a diario, así que no merece la pena
sondear más a menudo.

## Panel de Energía

Ajustes → Paneles → **Energía** → *Consumo de agua* → añade la estadística
**`EMASESA consumo <contrato>`** (`emasesa:<contrato>_water`), que trae el histórico
horario completo.

## Cómo funciona (técnico)

- Autenticación OAuth de la app: `client_credentials` → token de app → login de usuario
  (`/login/autenticarUsuario`) → token Bearer de usuario (+ doble factor si aplica).
- Datos: `GET /consumos/contrato/{id}` (histórico horario) y `/…/ultimo` (último día),
  más `/lecturas/informacion/{id}` para los datos del contador.
- El `indice` (lectura acumulada, en litros) se importa como suma en m³, de forma
  idempotente, a las estadísticas de largo plazo.

## Limitaciones

- API no documentada: puede romperse si EMASESA cambia su backend.
- El histórico depende de lo que exponga tu contrato (contadores sin telelectura no dan
  detalle horario).

## Licencia

MIT.
