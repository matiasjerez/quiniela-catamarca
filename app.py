from flask import Flask, jsonify, send_from_directory
from flask_cors import CORS
import requests
from bs4 import BeautifulSoup
from datetime import datetime, timedelta
import os

app = Flask(__name__, static_folder="static")
CORS(app)

BASE_URL = "https://cat.lotemovil.com.ar/extractos/search"
ORDEN_TURNOS = ["La Primera", "Matutino", "Vespertino", "De la Tarde", "Nocturno"]

HEADERS = {
    "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 Chrome/124.0 Safari/537.36"
}


def fetch_quiniela(fecha: str):
    url = f"{BASE_URL}?imputacion=0&fecha={fecha}"
    print(f"[fetch] GET {url}")
    resp = requests.get(url, headers=HEADERS, timeout=15)
    resp.raise_for_status()
    return parsear(resp.text, fecha)


def parsear(html: str, fecha: str):
    soup = BeautifulSoup(html, "html.parser")
    turnos = []
    items = soup.select("#quiniela-Slider .item")
    print(f"[parsear] fecha={fecha} items encontrados={len(items)}")

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
                "fecha": fecha_sorteo or fecha,
                "numeros": numeros
            })

    if not turnos:
        print(f"[parsear] fallback a parsear_texto")
        turnos = parsear_texto(soup, fecha)

    turnos.sort(key=lambda t: ORDEN_TURNOS.index(t["nombre"]) if t["nombre"] in ORDEN_TURNOS else 99)
    print(f"[parsear] turnos encontrados: {[t['nombre'] for t in turnos]}")
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
        elif "/" in l and len(l) == 10:
            current["fecha"] = l

    if current and current["numeros"]:
        turnos.append(current)

    return turnos


@app.route("/api/quiniela")
def quiniela_hoy():
    fecha = datetime.now().strftime("%d/%m/%Y")
    try:
        turnos = fetch_quiniela(fecha)
        return jsonify({
            "ok": True,
            "fecha": fecha,
            "turnos": turnos,
            "actualizado": datetime.now().strftime("%H:%M:%S")
        })
    except Exception as e:
        print(f"[ERROR quiniela_hoy] {e}")
        return jsonify({"ok": False, "error": str(e)}), 500


@app.route("/api/quiniela/<fecha>")
def quiniela_fecha(fecha):
    fecha_fmt = fecha.replace("-", "/")
    try:
        turnos = fetch_quiniela(fecha_fmt)
        return jsonify({
            "ok": True,
            "fecha": fecha_fmt,
            "turnos": turnos,
            "actualizado": datetime.now().strftime("%H:%M:%S")
        })
    except Exception as e:
        print(f"[ERROR quiniela_fecha] {e}")
        return jsonify({"ok": False, "error": str(e)}), 500


@app.route("/api/cabezas")
def cabezas_ultimos_dias():
    resultado = []
    errores = []
    dia = datetime.now()
    intentos = 0

    while len(resultado) < 5 and intentos < 14:
        if dia.weekday() == 6:  # domingo
            dia -= timedelta(days=1)
            intentos += 1
            continue

        fecha_str = dia.strftime("%d/%m/%Y")
        print(f"[cabezas] intentando fecha={fecha_str}")
        try:
            turnos = fetch_quiniela(fecha_str)
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
                print(f"[cabezas] OK fecha={fecha_str} cabezas={len(cabezas)}")
            else:
                print(f"[cabezas] sin turnos para fecha={fecha_str}")
        except Exception as e:
            msg = f"fecha={fecha_str} error={str(e)}"
            print(f"[cabezas] ERROR {msg}")
            errores.append(msg)

        dia -= timedelta(days=1)
        intentos += 1

    return jsonify({
        "ok": True,
        "dias": resultado,
        "debug_errores": errores,
        "debug_intentos": intentos
    })


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
