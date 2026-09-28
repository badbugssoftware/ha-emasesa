"""Constantes de la integración EMASESA."""

from __future__ import annotations

from datetime import timedelta

DOMAIN = "emasesa"
PLATFORMS = ["binary_sensor", "sensor"]

# --- Endpoints de la API privada de la app "Mi Emasesa" -------------------
API_ROOT = "https://api.emasesa.com"
API_BASE = f"{API_ROOT}/miemasesa/api/v1.0"
TOKEN_URL = f"{API_ROOT}/oauth2/token?grant_type=client_credentials"

# Credencial "client_credentials" embebida en el APK (entorno PRO).
# Es Base64 de "<client_id>:<client_secret>" y viaja como cabecera
# "Authorization: Basic ...". Está presente en cualquier copia de la app.
CLIENT_BASIC = (
    "S3VtbGxFOFRtWUs1TV9DWldqR2xMRTVrVFhJYTp0emdYZGl6emZKeEVwRUN6bGZmYzBpN0ZXWkVh"
)

# El backend FILTRA por User-Agent: con el de Python devuelve 401 con el
# mensaje engañoso "usuario y/o contraseña incorrectos". Hay que enviar el
# mismo que la app oficial.
USER_AGENT = "okhttp/2.1.0"

# La app se identifica como "sistema=3" (Android).
SISTEMA = "3"

# --- Claves de configuración ----------------------------------------------
CONF_USERNAME = "usuario"  # NIF/DNI/NIE
CONF_PASSWORD = "contrasena"
CONF_DEVICE_ID = "id_dispositivo"
CONF_CONTRACT_ID = "contrato_id"
CONF_CONTRACT_NUMBER = "contrato_numero"
CONF_SUPPLY_ADDRESS = "direccion_suministro"

# Opciones de versiones anteriores. El intervalo de sondeo dejó de ser
# configurable: lo decide la integración, que sabe cómo publica EMASESA mucho
# mejor de lo que puede saberlo quien la instala. Se listan para poder
# limpiarlas de las entradas existentes y no dejar ajustes que ya no hacen
# nada pero parecen hacerlo.
OPCIONES_OBSOLETAS = ("scan_minutes", "scan_hours")

# --- Ritmo de sondeo -------------------------------------------------------
# La telelectura NB-IoT publica UNA VEZ AL DÍA y a una hora que varía: medido
# en una instalación real, un día el dato llevaba 26 h de retraso y otro 12.
# Sondear a menudo no lo adelanta, sólo multiplica las peticiones contra una
# API privada (cada ciclo son ~10 llamadas).
#
# De ahí los dos ritmos: se espacia cuando ya se tiene el dato del día y se
# vuelve antes mientras se espera la publicación. Como cada instalación
# recibe su dato en un momento distinto, además acaban desfasadas solas y no
# llaman todas a la vez.
#
# 8 h / 3 h salen unos 6 ciclos al día. Con 6 h / 2 h (hasta la 0.7.2) eran
# unos 10: el dato llega una vez al día, así que casi todo el tiempo se está
# esperando y es el intervalo corto el que marca el ritmo. Mirar cada 2 h en
# vez de cada 3 sólo adelantaba el dato media hora de media, cuando EMASESA ya
# lo publica con entre 7 y 26 h de retraso.
SCAN_INTERVAL = timedelta(hours=8)
SCAN_INTERVAL_ESPERA = timedelta(hours=3)

# EMASESA deja de fiarse de un dispositivo unos 55 días después de su "último
# acceso", y ese último acceso SÓLO lo actualiza el registro del dispositivo,
# no los inicios de sesión (comprobado contra la API en septiembre de 2026). La
# app oficial parece registrarlo cada vez que se abre; la integración lo hace
# una vez por semana, con margen de sobra.
RENOVAR_CONFIANZA_CADA = timedelta(days=7)

# Días de histórico horario a importar en el primer arranque (backfill).
INITIAL_BACKFILL_DAYS = 60
# En cada actualización, re-importamos los últimos N días (rellena huecos).
UPDATE_BACKFILL_DAYS = 4
# Tope duro al rellenar un hueco largo (API o HA caídos mucho tiempo).
MAX_BACKFILL_DAYS = 365

ATTRIBUTION = "Datos de EMASESA (Oficina Virtual / Mi Emasesa)"

# El índice del contador y los consumos vienen en LITROS.
LITERS_PER_M3 = 1000.0

# --- Detección de posible fuga --------------------------------------------
# Franja de madrugada analizada y nº de noches consecutivas que deben tener
# TODAS sus horas con consumo para considerar que hay un goteo permanente.
LEAK_HOUR_START = 2
LEAK_HOUR_END = 5
LEAK_NIGHTS = 3
LEAK_MIN_LITERS = 1.0

# Radio (metros) para considerar "cercana" una incidencia de la red.
CONF_INCIDENT_RADIUS = "incident_radius_m"
# Ubicación del suministro. Por defecto la de Home Assistant, pero un segundo
# contrato (otra vivienda, un local) no tiene por qué estar donde está HA.
CONF_LATITUDE = "latitude"
CONF_LONGITUDE = "longitude"
DEFAULT_INCIDENT_RADIUS = 1000
MIN_INCIDENT_RADIUS = 100
MAX_INCIDENT_RADIUS = 20000

# --- Calidad del agua (SINAC) ---------------------------------------------
# El Ministerio de Sanidad publica en SINAC los boletines analíticos que cada
# gestor está obligado a notificar (RD 3/2023). Es mejor fuente que los PDF
# mensuales de la web de EMASESA: va por red de abastecimiento —es decir, por
# municipio—, trae la fecha de cada parámetro y los análisis de control se
# notifican varias veces por semana. Y es HTML, sin dependencias para leer PDF.
SINAC_DETALLE_URL = (
    "https://sinac.sanidad.gob.es/CiudadanoWeb/ciudadano/"
    "informacionAbastecimientoActionDetalleRed.do"
)
# SINAC tarda unos 30 s en generar la ficha de una red, pero la de Sevilla, la
# más grande, tarda cerca de cuatro minutos (223 y 239 s medidos en septiembre
# de 2026). Como la consulta va en segundo plano, esperar de más no molesta a
# nadie; quedarse corto deja sin datos a la red con más usuarios.
SINAC_TIMEOUT = 600
# Una consulta al día. Los análisis de control se toman 2 o 3 veces por semana
# y llegan a SINAC con más de una semana de retraso; los completos, una vez al
# mes. Mirar más a menudo no adelanta nada.
CALIDAD_INTERVAL = timedelta(hours=24)
# Si SINAC falla, se reintenta antes: esperar al día siguiente dejaría los
# sensores de calidad no disponibles 24 h por un fallo puntual.
CALIDAD_INTERVAL_REINTENTO = timedelta(hours=2)

# Redes de abastecimiento de EMASESA en SINAC: código INE del municipio ->
# (nombre, id de la red). Son identificadores del propio SINAC y no cambian;
# sacados recorriendo todos los municipios de la provincia de Sevilla y
# quedándose con las redes cuyo gestor es EMASESA.
# La elige quien instala: no se deduce de nada. Hasta que no hay una elegida
# no se consulta SINAC ni se crean las entidades de calidad.
CONF_RED_SINAC = "red_sinac"
ISSUE_ELEGIR_RED = "elegir_red_sinac"
REDES_SINAC: dict[str, tuple[str, str]] = {
    "41004": ("Alcalá de Guadaíra", "1376"),
    "41005": ("Alcalá del Río", "1366"),
    "41021": ("Camas", "1372"),
    "41034": ("Coria del Río", "1369"),
    "41038": ("Dos Hermanas", "1375"),
    "41043": ("El Garrobo", "1365"),
    "41058": ("Mairena del Alcor", "1373"),
    "41079": ("La Puebla del Río", "1368"),
    "41081": ("La Rinconada", "1367"),
    "41083": ("El Ronquillo", "8349"),
    "41086": ("San Juan de Aznalfarache", "1371"),
    "41091": ("Sevilla", "1374"),
}
