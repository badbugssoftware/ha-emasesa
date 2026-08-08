"""Constantes de la integración EMASESA."""

from __future__ import annotations

from datetime import timedelta

DOMAIN = "emasesa"
PLATFORMS = ["binary_sensor", "sensor"]

# --- Endpoints de la API privada de la app "Mi Emasesa" -------------------
API_ROOT = "https://api.emasesa.com"
API_BASE = "https://api.emasesa.com/miemasesa/api/v1.0"
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
SCAN_INTERVAL = timedelta(hours=6)
SCAN_INTERVAL_ESPERA = timedelta(hours=2)

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
