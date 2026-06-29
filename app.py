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
            continue  # ignorar todas las fechas del HTML

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

    # Intentar selector CSS del slider
    items = soup.select("#quiniela-Slider .item")
    for item in items:
        textos = [el.get_text(strip=True) for el in item.find_all(True) if el.get_text(strip=True)]
        nombre = None
        hora = None
        sorteo = None
        numeros = []

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
            elif len(t) == 4 and t.isdigit() and len(numeros) < 20:
                numeros.append(t)

        if nombre and numeros:
            turnos.append({"nombre": nombre, "hora": hora or "", "sorteo": sorteo or "", "fecha": fecha_solicitada, "numeros": numeros})

    if not turnos:
        turnos = parsear_texto(soup, fecha_solicitada)

    # Deduplicar: quedarse con la entrada que tenga MÁS números por turno
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
    Verifica que los turnos encontrados correspondan a la fecha solicitada.
    Estrategia: al menos un turno debe tener 20 números Y la fecha del primer
    número en el HTML debe coincidir con la solicitada.
    Si el sitio no tiene datos para esa fecha, el HTML no va a contener
    ningún turno con 20 números relacionados a esa fecha.
    Usamos una heurística: si hay turnos con hora registrada, son datos reales.
    Si todos los turnos no tienen hora, probablemente son datos de otro día.
    """
    if not turnos:
        return False
    # Si al menos un turno tiene hora, son datos del día solicitado
    con_hora = [t for t in turnos if t["hora"]]
    return len(con_hora) > 0


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
