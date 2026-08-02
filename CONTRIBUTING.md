# Cómo contribuir

Gracias por pasarte por aquí. Este es un proyecto pequeño y personal, así que cualquier
ayuda —un informe de error bien escrito, una corrección de la documentación o un PR— se
agradece de verdad.

## Índice

- [Antes de nada: privacidad](#antes-de-nada-privacidad)
- [Informar de un error](#informar-de-un-error)
- [Proponer una función](#proponer-una-función)
- [Entorno de desarrollo](#entorno-de-desarrollo)
- [Tests](#tests)
- [Estilo y convenciones](#estilo-y-convenciones)
- [Estructura del proyecto](#estructura-del-proyecto)
- [Trabajar con la API privada](#trabajar-con-la-api-privada)
- [Traducciones](#traducciones)
- [Enviar un pull request](#enviar-un-pull-request)
- [Publicar una versión](#publicar-una-versión)
- [Código de conducta](#código-de-conducta)

## Antes de nada: privacidad

Esta integración maneja **datos personales**: tu NIF, tu contraseña, tokens de sesión, tu
número de contrato y la dirección de tu vivienda. Antes de pegar un log, una captura o una
respuesta de la API en una incidencia o en un PR, **quita o sustituye**:

| Dato | Qué hacer |
| --- | --- |
| NIF / DNI / NIE (`usuario`) | Sustitúyelo por `********X` |
| Contraseña (`contrasena`) | Nunca la pegues, en ningún caso |
| `access_token`, `refresh_token`, cabeceras `Authorization` | Sustitúyelos por `<REDACTADO>` |
| `contratos_id`, `numero_contrato` | Sustitúyelos por `12345678` |
| `direccion_suministro`, población, coordenadas | Sustitúyelas por `<dirección>` |
| `id_dispositivo` | Sustitúyelo por un UUID falso |

Los `entity_id` y los `statistic_id` llevan el contrato dentro, así que anonimízalos
también. Si no estás seguro de si algo es sensible, no lo pegues: pregunta primero.

**Nunca subas al repositorio** credenciales reales, volcados de tráfico sin depurar,
`.storage` de Home Assistant ni el APK de la app.

## Informar de un error

Abre una incidencia en
[GitHub Issues](https://github.com/badbugssoftware/ha-emasesa/issues) e incluye:

1. **Versión de Home Assistant** y **versión de la integración** (la de `manifest.json`).
2. **Cómo lo has instalado**: HACS o manual.
3. **Qué esperabas** y **qué ha pasado**.
4. **Pasos para reproducirlo**.
5. **Log relevante, anonimizado**. Actívalo así y reinicia:

   ```yaml
   # configuration.yaml
   logger:
     default: warning
     logs:
       custom_components.emasesa: debug
   ```

6. Si el problema tiene que ver con el consumo o con el panel de Energía, di también si tu
   contador es **NB-IoT** (atributo `telelectura_nbiot` del sensor *Índice del contador*).

Antes de abrir la incidencia, comprueba que **la app oficial Mi Emasesa funciona** en tu
móvil: si tampoco va, es una caída de EMASESA y no hay nada que arreglar aquí.

## Proponer una función

Abre una incidencia describiendo **el caso de uso**, no solo la solución técnica. Si la
función necesita un endpoint de la API que aún no se usa, indica cuál y qué devuelve
(con la respuesta **anonimizada**).

Ten en cuenta el alcance del proyecto: monitorizar consumo, coste e información del
suministro. Todo lo que implique **operaciones de escritura** en la cuenta de EMASESA
(pagos, cambios de datos, altas o bajas) queda fuera.

## Entorno de desarrollo

La integración no tiene dependencias propias (`requirements` está vacío en el
`manifest.json`): todo lo que usa viene con Home Assistant.

```bash
git clone https://github.com/badbugssoftware/ha-emasesa.git
cd ha-emasesa

python3 -m venv .venv
source .venv/bin/activate
pip install ruff
```

Para probar en tu instalación, lo más cómodo es enlazar la carpeta del componente dentro
de la configuración de Home Assistant:

```bash
ln -s "$(pwd)/custom_components/emasesa" /ruta/a/config/custom_components/emasesa
```

Después reinicia Home Assistant. Si cambias `manifest.json`, `strings.json` o las
traducciones, hace falta reiniciar (y a veces limpiar la caché del navegador) para ver los
cambios en el config flow.

## Tests

Hay una batería de tests unitarios con **pytest** en `tests/`, que usa
`pytest-homeassistant-custom-component` (esa dependencia es la que fija de facto la
versión de Home Assistant contra la que se prueba) y `aioresponses` para simular la API.

```bash
python3 -m venv .venv-test
.venv-test/bin/pip install -r requirements-test.txt
.venv-test/bin/pytest -q
```

Qué se espera de un PR:

- Si **arreglas un bug**, añade un test que falle sin el arreglo.
- Si **añades un endpoint** a `api.py`, añade un test con una respuesta de ejemplo
  **anonimizada** (mira `tests/test_api.py`).
- Si tocas la **importación de estadísticas** o la **detección de fugas**, hay que cubrir
  el caso raro: huecos, índices que retroceden y el día de 25 horas de octubre
  (ver `tests/test_coordinator.py`).
- **Nunca** pongas datos reales en los tests: ni NIF, ni contratos, ni direcciones.

## Estilo y convenciones

- Se sigue el estilo de las integraciones del núcleo de Home Assistant.
- `from __future__ import annotations` al principio de cada módulo, y tipado en las
  firmas públicas.
- Línea de 88 columnas. Formato y linting con **ruff**, configurado en `ruff.toml` con los
  mismos grupos de reglas que usa Home Assistant Core:

  ```bash
  ruff check custom_components/
  ruff format --check --diff custom_components/
  ```

  Es exactamente lo que ejecuta la CI, así que si pasa en local, pasa en el PR.

- **Todo el I/O es asíncrono.** Nada de `requests` ni de llamadas bloqueantes dentro del
  bucle de eventos; se usa el `aiohttp` compartido de Home Assistant
  (`async_get_clientsession`).
- **Comentarios y docstrings en español**, igual que el resto del código. Explica *por qué*
  se hace algo raro, no *qué* hace la línea: gran parte del valor de este repositorio está
  en los comentarios que documentan las manías de la API de EMASESA.
- Los nombres de las entidades salen de `strings.json` mediante `_attr_translation_key`,
  nunca escritos a mano en el sensor.
- Cada entidad necesita un `_attr_unique_id` estable, con el id de contrato como prefijo.
- **No rompas los `unique_id` ni los `statistic_id` existentes** sin una migración: si
  cambian, la gente pierde su histórico.

### Integración continua

Con cada push y cada PR se ejecutan dos flujos de trabajo:

| Flujo | Qué comprueba |
| --- | --- |
| `.github/workflows/lint.yml` | `ruff check` y `ruff format --check` |
| `.github/workflows/validate.yml` | **hassfest** (`manifest.json`, `strings.json` y traducciones) y **HACS Action** (estructura del repositorio y `hacs.json`) |

`validate.yml` se lanza además todos los lunes, para detectar roturas causadas por cambios
en hassfest o en HACS aunque nadie haya tocado el repositorio.

## Estructura del proyecto

```
custom_components/emasesa/
├── __init__.py        # setup/unload de la entrada, coordinator y servicios
├── api.py             # cliente HTTP de la API privada (auth, 2FA, endpoints)
├── binary_sensor.py   # fuga, avería del contador e incidencias
├── config_flow.py     # alta, doble factor, selección de contrato, reauth y opciones
├── const.py           # dominio, endpoints, claves de config y valores por defecto
├── coordinator.py     # sondeo, coste, detección de fugas e importación de estadísticas
├── diagnostics.py     # volcado de diagnóstico con datos personales redactados
├── entity.py          # device_info compartido por todas las plataformas
├── sensor.py          # entidades sensor
├── manifest.json
├── services.yaml      # definición de los servicios del dominio
├── strings.json       # textos del config flow, opciones y nombres de entidad
└── translations/
    ├── es.json
    └── en.json

tests/                 # pytest (api, coordinator)
ruff.toml              # configuración del linter/formateador
.github/workflows/     # lint.yml y validate.yml
```

Reglas rápidas:

- Todo lo que hable HTTP vive en `api.py`; ni el coordinator ni las entidades construyen
  URLs.
- Toda la lógica de negocio vive en `coordinator.py`; las entidades solo **leen** de
  `coordinator.data`.
- El `DeviceInfo` se construye en `entity.py` y lo comparten todas las plataformas, para
  que sensores y binarios cuelguen del mismo dispositivo.
- Las constantes nuevas van a `const.py`, no dispersas por los módulos.
- Los datos “extra” (facturas, embalses, incidencias) se piden con el envoltorio `_safe`
  del coordinator: **si fallan, la actualización del consumo no debe caerse**.
- Si añades un servicio, defínelo en `services.yaml` **y** añade sus textos a
  `strings.json` y a las traducciones.

## Trabajar con la API privada

Cosas que hay que respetar sí o sí, porque están puestas por una razón:

- **`User-Agent: okhttp/2.1.0` en todas las peticiones.** El servidor filtra por él: con
  el `User-Agent` de Python el login devuelve un **401 con un mensaje falso de contraseña
  incorrecta**. Es el error número uno que se reporta.
- **`Content-Type: application/x-www-form-urlencoded` en `/oauth2/token`**, o el servidor
  responde **415**.
- **El parámetro `sistema=3`** (Android) viaja en todas las llamadas.
- **Registrar el dispositivo de confianza** (`confianza: "S"`) después de cada login
  correcto en el config flow. Sin eso, cada ciclo del coordinator dispararía un SMS nuevo
  al usuario.
- **No aumentes la frecuencia de sondeo.** El dato tarda 1–2 días en llegar; sondear más a
  menudo no aporta nada y puede provocar bloqueos de cuenta.
- Al añadir un endpoint nuevo, documenta en el docstring **la forma de la respuesta** con
  un ejemplo anonimizado.

Sobre las estadísticas de largo plazo:

- El consumo se importa como **suma absoluta** derivada del índice del contador, lo que
  hace la reimportación **idempotente**.
- El coste se acumula **por incrementos** y **nunca reescribe el pasado**: si la `sum` de
  una hora ya escrita bajase, el panel de Energía lo interpretaría como un reinicio del
  contador y los números saldrían mal.
- Cualquier cambio en esa lógica debe probarse con un histórico real, incluyendo el día
  del cambio de hora de octubre (25 horas).

## Traducciones

Los textos viven en dos sitios que hay que mantener alineados:

- `strings.json`: la fuente de verdad.
- `translations/es.json` y `translations/en.json`: las traducciones que ve el usuario.

Si añades una clave (paso del flujo, error, opción, servicio o entidad nueva),
**actualiza los tres ficheros** en el mismo commit. hassfest falla si falta alguna, y en la
interfaz aparecería el nombre técnico de la clave en vez de un texto legible.

¿Quieres aportar otro idioma? Copia `translations/en.json` a `translations/<código>.json` y
traduce solo los valores, nunca las claves.

## Enviar un pull request

1. Haz un fork y crea una rama descriptiva: `feat/sensor-embalses`, `fix/reauth-2fa`.
2. **Un cambio por PR.** Es mucho más fácil de revisar y de revertir.
3. Mensajes de commit en formato
   [Conventional Commits](https://www.conventionalcommits.org/es/):
   `feat:`, `fix:`, `docs:`, `refactor:`, `chore:`.
4. Actualiza el `README.md` si el cambio afecta a entidades, opciones o instalación.
5. Sube la versión de `manifest.json` solo si el mantenedor te lo pide: normalmente se
   hace al publicar la release.
6. En la descripción del PR, cuenta **cómo lo has probado** (versión de HA, si tu contador
   es NB-IoT, qué has visto en el panel de Energía).

Aviso importante: si tu cambio añade entidades, procura que **no queden habilitadas por
defecto si son ruidosas**, y comprueba que no rompen el panel de Energía por doble conteo
con las estadísticas ya existentes.

## Publicar una versión

Reservado al mantenedor:

1. Sube el campo `version` en `custom_components/emasesa/manifest.json`
   (versionado semántico).
2. Commit `chore: release vX.Y.Z` y etiqueta `vX.Y.Z`.
3. Crea la release en GitHub con las notas de los cambios. HACS toma las actualizaciones
   de las releases etiquetadas.

## Código de conducta

Sé buena gente. Se rechazará cualquier contribución o comentario que falte al respeto a
otras personas.

Al contribuir, aceptas que tu aportación se publique bajo la
[licencia MIT](LICENSE) del proyecto.
