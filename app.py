# ==============================
# IMPORTACIÓN DE LIBRERÍAS
# ==============================
# Se importan las librerías necesarias para la aplicación web, manejo de base de datos,
# transformación de coordenadas, manipulación de archivos y fechas.
from flask import Flask, render_template, request, send_file, session, jsonify
import mysql.connector
from mysql.connector import pooling
from pyproj import Transformer
import csv
import io
from collections import defaultdict
import uuid
import os
import glob
from openpyxl import Workbook
import time

# ==============================
# CONFIGURACIÓN GENERAL DE FLASK
# ==============================
# Se configura la aplicación Flask, se define la clave secreta de sesión y el directorio
# donde se guardarán temporalmente los archivos exportados (CSV y Excel).
app = Flask(__name__)
app.secret_key = 'clave_secreta'
app.config['EXPORT_FOLDER'] = 'temp_exports'
os.makedirs(app.config['EXPORT_FOLDER'], exist_ok=True)

# ==========================================================
# FUNCIÓN PARA LIMPIAR ARCHIVOS ANTIGUOS (CSV Y EXCEL > 1h)
# ==========================================================
# Esta función elimina archivos temporales de exportación que tengan más de una hora de antigüedad.
def limpiar_archivos_antiguos():
    una_hora_atras = time.time() - 3600
    for extension in ('*.csv', '*.xlsx'):
        for archivo in glob.glob(os.path.join(app.config['EXPORT_FOLDER'], extension)):
            if os.path.getmtime(archivo) < una_hora_atras:
                try:
                    os.remove(archivo)
                except:
                    pass

# ========================================
# CONFIGURACIÓN DEL POOL DE CONEXIONES DB
# ========================================
# Se crea un pool de conexiones para mejorar el rendimiento y reutilizar conexiones
# a la base de datos MySQL.
dbconfig = {
    "host": os.getenv("DB_HOST"),
    "port": int(os.getenv("DB_PORT")),
    "user": os.getenv("DB_USER"),
    "password": os.getenv("DB_PASSWORD"),
    "database": os.getenv("DB_NAME")
}
cnxpool = pooling.MySQLConnectionPool(pool_name="mypool", pool_size=5, **dbconfig)

# ================================================
# CACHÉ Y FUNCIÓN PARA OBTENER NOMBRES DE COLUMNAS
# ================================================
# Se cachean las columnas de la tabla para evitar múltiples llamadas a la base de datos.
columnas_cache = []

def obtener_columnas():
    global columnas_cache
    if not columnas_cache:
        conn = cnxpool.get_connection()
        cursor = conn.cursor()
        cursor.execute("SHOW COLUMNS FROM biotic_database")
        columnas_cache = [col[0] for col in cursor.fetchall()]
        cursor.close()
        conn.close()
    return columnas_cache

# ===============================
# RUTA PRINCIPAL "/"
# ===============================
# Renderiza el formulario principal con todos los filtros disponibles (Municipio, Proyecto,
# Especie, Grupo Biológico, Tipo de Hidrobiota).
@app.route('/')
def index():
    conn = cnxpool.get_connection()
    cursor = conn.cursor()

    # Consulta de filtros únicos
    cursor.execute("SELECT DISTINCT Municipio FROM biotic_database ORDER BY Municipio")
    municipios = [row[0] for row in cursor.fetchall()]

    cursor.execute("SELECT DISTINCT Proyecto FROM biotic_database ORDER BY Proyecto")
    proyectos = [row[0] for row in cursor.fetchall()]

    cursor.execute("SELECT DISTINCT Nombre_cientifico FROM biotic_database ORDER BY Nombre_cientifico")
    especies = [row[0] for row in cursor.fetchall()]

    cursor.execute("SELECT DISTINCT Grupo_Biologico FROM biotic_database WHERE Grupo_Biologico IS NOT NULL ORDER BY Grupo_Biologico")
    grupos_biologicos = [row[0] for row in cursor.fetchall()]

    cursor.execute("SELECT DISTINCT Tipo_Hidrobiota FROM biotic_database WHERE Tipo_Hidrobiota IS NOT NULL ORDER BY Tipo_Hidrobiota")
    tipos_hidrobiota = [row[0] for row in cursor.fetchall()]

    cursor.close()
    conn.close()

    return render_template(
        'index.html',
        columnas=obtener_columnas(),
        municipios=municipios,
        proyectos=proyectos,
        especies=especies,
        grupos_biologicos=grupos_biologicos,
        tipos_hidrobiota=tipos_hidrobiota
    )

# ===============================
# RUTA "/columnas"
# ===============================
# Devuelve las columnas de la base de datos en formato JSON para el frontend.
@app.route("/columnas")
def columnas():
    return jsonify({"columnas": obtener_columnas()})

# ======================================
# RUTA "/buscar" (BÚSQUEDA DE REGISTROS)
# ======================================
# Esta ruta procesa los filtros enviados desde el formulario y realiza la consulta a la base de datos.
# Posteriormente transforma las coordenadas y exporta los resultados en CSV y Excel.
@app.route('/buscar', methods=['POST'])
def buscar():
    limpiar_archivos_antiguos()

    # ---------- Captura de filtros del formulario ----------
    palabra_clave = request.form.get('palabra', '').strip()
    columna_clave = request.form.get('columna', '')
    municipio = request.form.get('filtro_municipio')
    proyecto = request.form.get('filtro_proyecto')
    nombre_comun = request.form.get('filtro_nombre_comun')
    nombre_cientifico = request.form.get('filtro_especie')
    codigo_de_muestra = request.form.get('codigo_de_muestra')
    grupo_biologico = request.form.get('filtro_grupo_biologico')
    tipo_hidrobiota = request.form.get('filtro_tipo_hidrobiota')
    columnas_mostrar = request.form.getlist('columnas_mostrar')

    # ---------- Validación y preparación de columnas ----------
    columnas_disponibles = obtener_columnas()

    # Columnas esenciales que deben ir siempre
    columnas_clave = [
        'Nombre_cientifico',
        'Nombre_comun',
        'Codigo_de_muestra',
        'Proyecto',
        'Fecha_de_colecta'
    ]

    # Si no se seleccionaron columnas o se seleccionó "__todas__", se muestran todas
    if '__todas__' in columnas_mostrar or not columnas_mostrar:
        columnas_mostrar = columnas_disponibles.copy()
    else:
        columnas_mostrar = list(set(columnas_mostrar + columnas_clave))

    # Validación final
    columnas_mostrar = [col for col in columnas_disponibles if col in columnas_mostrar]

    # ---------- Construcción de consulta SQL ----------
    conn = cnxpool.get_connection()
    cursor = conn.cursor(dictionary=True)

    query = "SELECT * FROM biotic_database WHERE 1=1"
    filtros = []
    valores = []

    # Agregado de filtros dinámicamente
    if municipio:
        filtros.append("Municipio LIKE %s")
        valores.append(f"%{municipio}%")
    if proyecto:
        filtros.append("Proyecto LIKE %s")
        valores.append(f"%{proyecto}%")
    if nombre_comun:
        filtros.append("Nombre_comun LIKE %s")
        valores.append(f"%{nombre_comun}%")
    if nombre_cientifico:
        filtros.append("Nombre_cientifico LIKE %s")
        valores.append(f"%{nombre_cientifico}%")
    if codigo_de_muestra:
        filtros.append("Codigo_de_muestra LIKE %s")
        valores.append(f"%{codigo_de_muestra}%")
    if grupo_biologico:
        filtros.append("Grupo_Biologico LIKE %s")
        valores.append(f"%{grupo_biologico}%")
    if tipo_hidrobiota:
        filtros.append("Tipo_Hidrobiota LIKE %s")
        valores.append(f"%{tipo_hidrobiota}%")
    if palabra_clave:
        if columna_clave and columna_clave != "__todas__" and columna_clave in columnas_disponibles:
            filtros.append(f"{columna_clave} LIKE %s")
            valores.append(f"%{palabra_clave}%")
        elif columna_clave == "__todas__":
            subfiltros = [f"{col} LIKE %s" for col in columnas_disponibles]
            filtros.append("(" + " OR ".join(subfiltros) + ")")
            valores.extend([f"%{palabra_clave}%"] * len(columnas_disponibles))

    if filtros:
        query += " AND " + " AND ".join(filtros)

    cursor.execute(query, valores)
    resultados = cursor.fetchall()

    # ===============================
    # TRANSFORMACIÓN DE COORDENADAS MAGNA A WGS84
    # ===============================
    # Convierte coordenadas de EPSG específico a WGS84 (latitud/longitud estándar).
    transformadores = defaultdict(lambda: None)
    coordenadas = []

    for fila in resultados:
        try:
            lat = str(fila.get('Latitud_decimal', '')).replace(',', '.')
            lon = str(fila.get('Longitud_decimal', '')).replace(',', '.')
            epsg = fila.get('Codigo_EPSG_decimal')
            if lat and lon and epsg:
                lat = float(lat)
                lon = float(lon)
                if not transformadores[epsg]:
                    transformadores[epsg] = Transformer.from_crs(f"EPSG:{epsg}", "EPSG:4326", always_xy=True)
                lon_wgs84, lat_wgs84 = transformadores[epsg].transform(lon, lat)
                fila['Latitud_decimal'] = lat_wgs84
                fila['Longitud_decimal'] = lon_wgs84
                coordenadas.append({
                    'lat': lat_wgs84,
                    'lon': lon_wgs84,
                    'Nombre_cientifico': fila.get('Nombre_cientifico') or 'No disponible',
                    'Nombre_comun': fila.get('Nombre_comun') or 'No disponible',
                    'Codigo_de_muestra': fila.get('Codigo_de_muestra') or 'No disponible',
                    'Proyecto': fila.get('Proyecto') or 'No disponible',
                    'Fecha_de_colecta': fila.get('Fecha_de_colecta') or 'No disponible'
                })
        except:
            fila['Latitud_decimal'] = None
            fila['Longitud_decimal'] = None

    # =======================================
    # EXPORTACIÓN DE RESULTADOS A CSV Y EXCEL
    # =======================================
    export_id = str(uuid.uuid4())
    csv_path = os.path.join(app.config['EXPORT_FOLDER'], f"{export_id}.csv")
    xlsx_path = os.path.join(app.config['EXPORT_FOLDER'], f"{export_id}.xlsx")

    # Exportar a CSV
    with open(csv_path, 'w', encoding='utf-8', newline='') as f:
        writer = csv.DictWriter(f, fieldnames=columnas_mostrar)
        writer.writeheader()
        for fila in resultados:
            writer.writerow({col: fila.get(col, '') for col in columnas_mostrar})

    # Exportar a Excel
    wb = Workbook()
    ws = wb.active
    ws.append(columnas_mostrar)
    for fila in resultados:
        ws.append([fila.get(col, '') for col in columnas_mostrar])
    wb.save(xlsx_path)

    # Guardar rutas en sesión
    session['csv_export_path'] = csv_path
    session['excel_export_path'] = xlsx_path

    cursor.close()
    conn.close()

    return render_template(
        'results.html',
        resultados=resultados,
        columnas_mostrar=columnas_mostrar,
        columnas_csv=columnas_mostrar,
        palabra=palabra_clave,
        columna=columna_clave,
        coordenadas=coordenadas
    )

# ===================================
# RUTA "/exportar_csv" (DESCARGA CSV)
# ===================================
# Permite al usuario descargar el archivo CSV generado con los resultados filtrados.
@app.route('/exportar_csv')
def exportar_csv():
    export_path = session.get('csv_export_path')
    if not export_path or not os.path.exists(export_path):
        return 'No hay resultados para exportar en CSV', 400
    return send_file(export_path, mimetype='text/csv', as_attachment=True, download_name='resultados.csv')

# =======================================
# RUTA "/exportar_excel" (DESCARGA EXCEL)
# =======================================
# Permite al usuario descargar el archivo Excel generado con los resultados filtrados.
@app.route('/exportar_excel')
def exportar_excel():
    export_path = session.get('excel_export_path')
    if not export_path or not os.path.exists(export_path):
        return 'No hay resultados para exportar en Excel', 400
    return send_file(export_path, mimetype='application/vnd.openxmlformats-officedocument.spreadsheetml.sheet', as_attachment=True, download_name='resultados.xlsx')

# ===============================
# EJECUCIÓN DE LA APLICACIÓN
# ===============================
# Se ejecuta la aplicación en modo debug para desarrollo local.
if __name__ == '__main__':
    port = int(os.environ.get('PORT', 5000))
    app.run(host='0.0.0.0', port=port)