from flask import Flask, jsonify, send_from_directory
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
