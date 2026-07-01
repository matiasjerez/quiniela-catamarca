from flask import Flask, jsonify, send_from_directory, request
from flask_cors import CORS
import requests
from bs4 import BeautifulSoup
from datetime import datetime, timedelta
import os
import re

app = Flask(__name__, static_folder="static")
CORS(app)

BASE_URL = "https://cat.lotemovil.com.ar/extractos/search"
ORDEN_TURNOS = ["La Primera", "Matutino", "Vespertino", "De la Tarde", "Nocturno"]

HEADERS = {
    "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 Chrome/124.0 Safari/537.36"
}

FECHA_RE = re.compile(r'^\d{2}/\d{2}/\d{4}$')
HORA_RE  = re.compile(r'^\d{2}:\d{2}:\d{2}$')


def fetch_html(fecha: str) -> str:
    url = f"{BASE_URL}?imputacion=0&fecha={fecha}"
    resp = requests.get(url, headers=HEADERS, timeout=15)
    resp.raise_for_status()
    return resp.text


def parsear_texto(soup, fecha_solicitada: str):
    texto = soup.get_text(separator="\n")
    lineas = [l.strip() for l in texto.splitlines() if l.strip()]

    turnos = []
    current = None

    for l in lineas:
        if l in ORDEN_TURNOS:
            if current and current["numeros"]:
                turnos.append(current)
            current = {"nombre": l, "hora": "", "sorteo": "", "fecha": fecha_solicitada, "numeros": []}
            continue

        if current is None:
            continue

        if HORA_RE.match(l):
            current["hora"] = l[:5]
            continue

        if FECHA_RE.match(l):
            # Guardar la fecha del sorteo (primera fecha que aparece por turno)
            if not current.get("fecha_real"):
                current["fecha_real"] = l
            continue

        if l.isdigit() and len(l) == 5 and not current["sorteo"] and not current["numeros"]:
            current["sorteo"] = l
            continue

        if l.isdigit() and len(l) == 4 and len(current["numeros"]) < 20:
            current["numeros"].append(l)
            continue

    if current and current["numeros"]:
        turnos.append(current)

    return turnos


def parsear(html: str, fecha_solicitada: str):
    soup = BeautifulSoup(html, "html.parser")
    turnos = []

    items = soup.select("#quiniela-Slider .item")
    for item in items:
        textos = [el.get_text(strip=True) for el in item.find_all(True) if el.get_text(strip=True)]
        nombre = None
        hora = None
        sorteo = None
        numeros = []
        fecha_real = None

        for i, t in enumerate(textos):
            if t in ORDEN_TURNOS:
                nombre = t
            elif t == "HORA:" and i + 1 < len(textos):
                hora = textos[i + 1][:5]
            elif "SORTEO" in t and i + 1 < len(textos):
                try:
                    sorteo = textos[i + 1]
                except:
                    pass
            elif t == "FECHA DE SORTEO:" and i + 1 < len(textos):
                fecha_real = textos[i + 1]
            elif len(t) == 4 and t.isdigit() and len(numeros) < 20:
                numeros.append(t)

        if nombre and numeros:
            turnos.append({
                "nombre": nombre, "hora": hora or "", "sorteo": sorteo or "",
                "fecha": fecha_solicitada, "fecha_real": fecha_real or fecha_solicitada,
                "numeros": numeros
            })

    if not turnos:
        turnos = parsear_texto(soup, fecha_solicitada)

    # Deduplicar: quedarse con la entrada con más números
    vistos = {}
    for t in turnos:
        nombre = t["nombre"]
        if nombre not in vistos or len(t["numeros"]) > len(vistos[nombre]["numeros"]):
            vistos[nombre] = t
        elif len(t["numeros"]) == len(vistos[nombre]["numeros"]) and t["hora"] and not vistos[nombre]["hora"]:
            vistos[nombre] = t

    return [vistos[n] for n in ORDEN_TURNOS if n in vistos]


def tiene_datos_reales(turnos, fecha_solicitada: str):
    """
    Verifica que los datos correspondan a la fecha solicitada.
    Estrategia: compara la fecha_real de los turnos con la fecha solicitada.
    Si el sitio devuelve datos de otro día, fecha_real será diferente.
    """
    if not turnos:
        return False

    con_fecha_real = [t for t in turnos if t.get("fecha_real")]
    if not con_fecha_real:
        # Sin fecha_real disponible, verificar al menos que tengan hora
        return any(t["hora"] for t in turnos)

    # Verificar que al menos un turno tenga fecha_real == fecha_solicitada
    coinciden = [t for t in con_fecha_real if t.get("fecha_real") == fecha_solicitada]
    return len(coinciden) > 0


@app.route("/api/quiniela")
def quiniela_hoy():
    fecha = datetime.now().strftime("%d/%m/%Y")
    try:
        html = fetch_html(fecha)
        turnos = parsear(html, fecha)
        valido = tiene_datos_reales(turnos, fecha)
        return jsonify({
            "ok": True,
            "fecha": fecha,
            "turnos": turnos if valido else [],
            "sin_datos": not valido,
            "actualizado": datetime.now().strftime("%H:%M:%S")
        })
    except Exception as e:
        return jsonify({"ok": False, "error": str(e)}), 500


@app.route("/api/quiniela/<fecha>")
def quiniela_fecha(fecha):
    fecha_fmt = fecha.replace("-", "/")
    try:
        html = fetch_html(fecha_fmt)
        turnos = parsear(html, fecha_fmt)
        valido = tiene_datos_reales(turnos, fecha_fmt)
        return jsonify({
            "ok": True,
            "fecha": fecha_fmt,
            "turnos": turnos if valido else [],
            "sin_datos": not valido,
            "actualizado": datetime.now().strftime("%H:%M:%S")
        })
    except Exception as e:
        return jsonify({"ok": False, "error": str(e)}), 500


@app.route("/api/cabezas")
def cabezas_ultimos_dias():
    resultado = []
    dia = datetime.now()
    intentos = 0

    while len(resultado) < 5 and intentos < 14:
        if dia.weekday() == 6:  # domingo
            dia -= timedelta(days=1)
            intentos += 1
            continue

        fecha_str = dia.strftime("%d/%m/%Y")
        try:
            html = fetch_html(fecha_str)
            turnos = parsear(html, fecha_str)
            valido = tiene_datos_reales(turnos, fecha_str)

            if valido and turnos:
                cabezas = [
                    {"turno": t["nombre"], "numero": t["numeros"][0], "hora": t["hora"]}
                    for t in turnos if t["numeros"]
                ]
                resultado.append({
                    "fecha": fecha_str,
                    "dia_semana": ["Lun","Mar","Mié","Jue","Vie","Sáb","Dom"][dia.weekday()],
                    "cabezas": cabezas
                })
                print(f"[cabezas] OK {fecha_str} — {len(cabezas)} turnos")
            else:
                print(f"[cabezas] Sin datos válidos para {fecha_str}, saltando")
        except Exception as e:
            print(f"[cabezas] ERROR {fecha_str}: {e}")

        dia -= timedelta(days=1)
        intentos += 1

    return jsonify({"ok": True, "dias": resultado})


@app.route("/api/debug/<fecha>")
def debug_fecha(fecha):
    fecha_fmt = fecha.replace("-", "/")
    try:
        html = fetch_html(fecha_fmt)
        soup = BeautifulSoup(html, "html.parser")
        items = soup.select("#quiniela-Slider .item")
        turnos = parsear(html, fecha_fmt)
        valido = tiene_datos_reales(turnos, fecha_fmt)
        return jsonify({
            "fecha_solicitada": fecha_fmt,
            "items_slider": len(items),
            "datos_validos": valido,
            "turnos_parseados": len(turnos),
            "turnos": [{
                "nombre": t["nombre"],
                "hora": t["hora"],
                "fecha_real": t.get("fecha_real", ""),
                "numeros_count": len(t["numeros"]),
                "primer_numero": t["numeros"][0] if t["numeros"] else None
            } for t in turnos]
        })
    except Exception as e:
        return jsonify({"error": str(e)}), 500


from collections import Counter
import math

def fetch_dia(fecha_str, dow):
    """Descarga y parsea un día. Retorna lista de registros o []."""
    try:
        html = fetch_html(fecha_str)
        turnos = parsear(html, fecha_str)
        if not tiene_datos_reales(turnos, fecha_str):
            return []
        registros = []
        for t in turnos:
            if t["numeros"]:
                registros.append({
                    "fecha": fecha_str,
                    "dia_semana": dow,
                    "turno": t["nombre"],
                    "cabeza": t["numeros"][0],
                    "todos": t["numeros"]
                })
        return registros
    except Exception as e:
        print(f"[historial] ERROR {fecha_str}: {e}")
        return []


def fetch_historial(dias_max=45):
    """Descarga el historial en paralelo usando threads."""
    from concurrent.futures import ThreadPoolExecutor, as_completed

    # Armar lista de fechas a consultar (sin domingos)
    fechas = []
    dia = datetime.now() - timedelta(days=1)
    for _ in range(dias_max + 10):
        if dia.weekday() != 6:  # saltar domingos
            fechas.append((dia.strftime("%d/%m/%Y"), dia.weekday()))
        dia -= timedelta(days=1)
        if len(fechas) >= dias_max:
            break

    historial = []
    # 10 workers en paralelo — reduce tiempo de ~90s a ~10s
    with ThreadPoolExecutor(max_workers=10) as executor:
        futuros = {executor.submit(fetch_dia, f, dow): f for f, dow in fechas}
        for futuro in as_completed(futuros):
            registros = futuro.result()
            historial.extend(registros)

    # Ordenar por fecha descendente
    historial.sort(key=lambda x: x["fecha"], reverse=True)
    print(f"[historial] Total recolectado: {len(historial)} registros de {len(fechas)} días")
    return historial


def calcular_estadisticas(historial, turno_filtro=None):
    """
    Calcula estadísticas sobre las cabezas del historial.
    Métricas:
    - Frecuencia absoluta y relativa de cada número
    - Frecuencia por día de la semana
    - Días desde la última aparición (números "fríos" y "calientes")
    - Terminación (último dígito) más frecuente
    - Score combinado para predicción del día siguiente
    """
    if turno_filtro:
        datos = [d for d in historial if d["turno"] == turno_filtro]
    else:
        datos = historial

    if not datos:
        return {}

    total = len(datos)
    mañana_dow = (datetime.now().weekday() + 1) % 7

    # Frecuencia global de cabezas
    freq_global = Counter(d["cabeza"] for d in datos)

    # Frecuencia por día de semana
    freq_dow = {}
    for d in datos:
        dow = d["dia_semana"]
        num = d["cabeza"]
        if dow not in freq_dow:
            freq_dow[dow] = Counter()
        freq_dow[dow][num] += 1

    # Total de sorteos para el día de la semana de mañana
    total_manana_dow = sum(freq_dow.get(mañana_dow, Counter()).values())

    # Última aparición de cada número
    ultima_aparicion = {}
    fechas_ordenadas = sorted(datos, key=lambda x: x["fecha"])
    for i, d in enumerate(fechas_ordenadas):
        ultima_aparicion[d["cabeza"]] = i  # índice del último sorteo donde salió

    total_sorteos = len(fechas_ordenadas)

    # Terminaciones más frecuentes
    freq_terminacion = Counter(d["cabeza"][-1] for d in datos)

    # Calcular score para cada número (0000-9999)
    numeros_vistos = set(d["cabeza"] for d in datos)
    scores = {}

    for num in numeros_vistos:
        # 1. Frecuencia global normalizada (0-1)
        f_global = freq_global[num] / total

        # 2. Frecuencia para el día de semana de mañana (0-1)
        f_dow = freq_dow.get(mañana_dow, Counter())[num] / max(total_manana_dow, 1)

        # 3. "Temperatura": qué tan reciente fue su última aparición
        # Números que salieron hace poco tienen menos chances (ley de rareza)
        # Números que no salen hace mucho tienen más chances
        ultimo_idx = ultima_aparicion.get(num, -1)
        if ultimo_idx >= 0:
            sorteos_desde_ultimo = total_sorteos - 1 - ultimo_idx
            # Normalizar: más sorteos sin salir = mayor score de temperatura
            temp_score = min(sorteos_desde_ultimo / 30, 1.0)
        else:
            temp_score = 1.0

        # 4. Frecuencia de la terminación del número
        terminacion = num[-1]
        f_term = freq_terminacion[terminacion] / total

        # Score combinado (pesos ajustables)
        score = (
            f_global * 0.30 +      # 30% frecuencia histórica global
            f_dow    * 0.35 +      # 35% frecuencia para ese día de semana
            temp_score * 0.20 +    # 20% temperatura (días sin salir)
            f_term   * 0.15        # 15% terminación frecuente
        )

        scores[num] = {
            "numero": num,
            "frecuencia": freq_global[num],
            "porcentaje_global": round(f_global * 100, 2),
            "veces_manana_dow": freq_dow.get(mañana_dow, Counter())[num],
            "porcentaje_dow": round(f_dow * 100, 2),
            "sorteos_sin_salir": total_sorteos - 1 - ultima_aparicion.get(num, total_sorteos - 1),
            "score": round(score * 100, 4)
        }

    # Top 20 por score
    top = sorted(scores.values(), key=lambda x: x["score"], reverse=True)[:20]

    dias_semana = ["Lunes","Martes","Miércoles","Jueves","Viernes","Sábado","Domingo"]

    return {
        "total_sorteos_analizados": total,
        "dias_con_datos": len(set(d["fecha"] for d in datos)),
        "fecha_inicio": fechas_ordenadas[0]["fecha"] if fechas_ordenadas else "",
        "fecha_fin": fechas_ordenadas[-1]["fecha"] if fechas_ordenadas else "",
        "dia_prediccion": dias_semana[mañana_dow],
        "terminaciones_frecuentes": [{"terminacion": k, "veces": v} for k, v in freq_terminacion.most_common(5)],
        "top_candidatos": top
    }

@app.route("/estadisticas")
def estadisticas_page():
    return send_from_directory(app.static_folder, "estadisticas.html")

@app.route("/api/estadisticas")
def estadisticas():
    turno = request.args.get("turno", None)
    try:
        print(f"[estadisticas] Descargando historial...")
        historial = fetch_historial(dias_max=45)
        print(f"[estadisticas] {len(historial)} registros descargados")
        stats = calcular_estadisticas(historial, turno_filtro=turno)
        stats["turno_filtro"] = turno or "Todos"
        return jsonify({"ok": True, **stats})
    except Exception as e:
        return jsonify({"ok": False, "error": str(e)}), 500

@app.route("/", defaults={"path": ""})
@app.route("/<path:path>")
def serve(path):
    if path and os.path.exists(os.path.join(app.static_folder, path)):
        return send_from_directory(app.static_folder, path)
    return send_from_directory(app.static_folder, "index.html")


if __name__ == "__main__":
    port = int(os.environ.get("PORT", 5000))
    print(f"🎲 Quiniela Catamarca corriendo en http://localhost:{port}")
    app.run(debug=False, host="0.0.0.0", port=port)
