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


def fetch_html(fecha: str) -> str:
    url = f"{BASE_URL}?imputacion=0&fecha={fecha}"
    resp = requests.get(url, headers=HEADERS, timeout=15)
    resp.raise_for_status()
    return resp.text


def extraer_fecha_real(html: str) -> str:
    """Extrae la primera fecha DD/MM/YYYY que aparece en el HTML."""
    match = re.search(r'\b(\d{2}/\d{2}/\d{4})\b', html)
    return match.group(1) if match else ""


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
        fecha_sorteo = None

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
                fecha_sorteo = textos[i + 1]
            elif len(t) == 4 and t.isdigit() and len(numeros) < 20:
                numeros.append(t)

        if nombre and numeros:
            turnos.append({
                "nombre": nombre,
                "hora": hora or "",
                "sorteo": sorteo or "",
                "fecha": fecha_sorteo or fecha_solicitada,
                "numeros": numeros
            })

    if not turnos:
        turnos = parsear_texto(soup, fecha_solicitada)

    turnos.sort(key=lambda t: ORDEN_TURNOS.index(t["nombre"]) if t["nombre"] in ORDEN_TURNOS else 99)
    return turnos


def parsear_texto(soup, fecha):
    texto = soup.get_text(separator="\n")
    lineas = [l.strip() for l in texto.splitlines() if l.strip()]
    turnos = []
    current = None

    for l in lineas:
        if l in ORDEN_TURNOS:
            if current and current["numeros"]:
                turnos.append(current)
            current = {"nombre": l, "hora": "", "sorteo": "", "fecha": fecha, "numeros": []}
        elif current is None:
            continue
        elif len(l) == 8 and l.count(":") == 2:
            current["hora"] = l[:5]
        elif l.isdigit() and len(l) == 5 and not current["sorteo"]:
            current["sorteo"] = l
        elif l.isdigit() and len(l) == 4 and len(current["numeros"]) < 20:
            current["numeros"].append(l)
        elif re.match(r'^\d{2}/\d{2}/\d{4}$', l):
            current["fecha"] = l

    if current and current["numeros"]:
        turnos.append(current)

    return turnos


def deduplicar_turnos(turnos):
    vistos = {}
    for t in turnos:
        nombre = t["nombre"]
        if nombre not in vistos:
            vistos[nombre] = t
        else:
            if not vistos[nombre]["hora"] and t["hora"]:
                vistos[nombre] = t
    return [vistos[n] for n in ORDEN_TURNOS if n in vistos]


def fetch_quiniela(fecha: str):
    html = fetch_html(fecha)

    # Detectar si el sitio devolvió datos de otra fecha
    fecha_real = extraer_fecha_real(html)
    if fecha_real and fecha_real != fecha:
        print(f"[fetch] Sitio devolvió fecha {fecha_real} para solicitud {fecha} — sin datos")
        return [], fecha_real  # sin datos para esa fecha

    turnos = parsear(html, fecha)
    turnos = deduplicar_turnos(turnos)
    return turnos, fecha


@app.route("/api/debug/<fecha>")
def debug_fecha(fecha):
    """Endpoint temporal para diagnosticar el parseo."""
    fecha_fmt = fecha.replace("-", "/")
    try:
        html = fetch_html(fecha_fmt)
        fecha_real = extraer_fecha_real(html)
        soup = BeautifulSoup(html, "html.parser")
        items = soup.select("#quiniela-Slider .item")
        texto_plano = soup.get_text(separator="\n")
        lineas = [l.strip() for l in texto_plano.splitlines() if l.strip()]
        # Buscar líneas relevantes
        relevantes = [l for l in lineas if l in ORDEN_TURNOS or 
                      (l.isdigit() and len(l) == 4) or
                      re.match(r'^\d{2}/\d{2}/\d{4}$', l) or
                      re.match(r'^\d{2}:\d{2}:\d{2}$', l)]
        return jsonify({
            "fecha_solicitada": fecha_fmt,
            "fecha_real_en_html": fecha_real,
            "items_slider": len(items),
            "lineas_relevantes": relevantes[:60]
        })
    except Exception as e:
        return jsonify({"error": str(e)}), 500


@app.route("/api/quiniela")
def quiniela_hoy():
    fecha = datetime.now().strftime("%d/%m/%Y")
    try:
        turnos, _ = fetch_quiniela(fecha)
        return jsonify({
            "ok": True,
            "fecha": fecha,
            "turnos": turnos,
            "sin_datos": len(turnos) == 0,
            "actualizado": datetime.now().strftime("%H:%M:%S")
        })
    except Exception as e:
        return jsonify({"ok": False, "error": str(e)}), 500


@app.route("/api/quiniela/<fecha>")
def quiniela_fecha(fecha):
    fecha_fmt = fecha.replace("-", "/")
    try:
        turnos, _ = fetch_quiniela(fecha_fmt)
        return jsonify({
            "ok": True,
            "fecha": fecha_fmt,
            "turnos": turnos,
            "sin_datos": len(turnos) == 0,
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
            turnos, fecha_real = fetch_quiniela(fecha_str)
            if turnos:
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
                print(f"[cabezas] Sin datos para {fecha_str} (sitio devolvió {fecha_real}), saltando")
        except Exception as e:
            print(f"[cabezas] ERROR {fecha_str}: {e}")

        dia -= timedelta(days=1)
        intentos += 1

    return jsonify({"ok": True, "dias": resultado})


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
