"""
build.py
---------
Reconstruye los archivos de datos que usa el mapa (carpeta data/) a partir
de los archivos que se colocan en source/.

Este script lo corre automáticamente GitHub Actions cada vez que se sube
un archivo nuevo a source/. No hace falta ejecutarlo a mano ni entender
el código para usarlo.

Archivos esperados en source/ (ver LEEME.md para el detalle de cada uno):
  - barrios.geojson
  - zonificacion.geojson, zonificacion.kml o zonificacion.kmz (cualquiera de los tres formatos sirve)
  - parcelario.geojson  → un solo archivo con todas las parcelas juntas (formato nuevo, sin dato de dominio fiscal/privado)
    o, si no existe ese archivo:
  - inmuebles_fiscales.geojson + inmuebles_privados.geojson → el formato viejo, separado en dos capas con dominio Fiscal/Privado
"""
import json, re, os, zipfile, io
from pyproj import Transformer
from lxml import etree

SOURCE = "source"
OUT = "data"
NSHARDS = 100

# Campos que NUNCA se publican porque identifican a personas
# (adjudicatario/ocupante/etc). Se comparan en minúscula, así que da
# igual cómo vengan capitalizados en el archivo de origen.
CAMPOS_SENSIBLES = {
    "apellido_nombre", "posesion", "fecha_expte",
    "categoria", "propietario_tierras_fiscales", "codigo_pos",
}

KML_NS = {"kml": "http://www.opengis.net/kml/2.2"}


def lower_keys(d):
    """Copia un diccionario de propiedades con todas las claves en minúscula,
    para no depender de cómo venga capitalizado cada archivo de origen."""
    return {(k or "").lower(): v for k, v in d.items()}


def get_transformer(geojson):
    """Detecta el CRS del archivo y arma un transformador a WGS84 (lon/lat).
    Si el archivo ya está en WGS84 / CRS84, no transforma nada."""
    crs = geojson.get("crs", {}).get("properties", {}).get("name", "")
    m = re.search(r"EPSG::?(\d+)", crs)
    if not m:
        return None  # ya está en WGS84 / CRS84, o no declara CRS
    epsg = int(m.group(1))
    if epsg == 4326:
        return None
    return Transformer.from_crs(f"EPSG:{epsg}", "EPSG:4326", always_xy=True)


def reproject_coords(coords, transformer):
    if not coords:
        return coords
    if isinstance(coords[0], (int, float)):
        x, y = coords[0], coords[1]
        if transformer:
            x, y = transformer.transform(x, y)
        return [round(x, 6), round(y, 6)]
    return [reproject_coords(c, transformer) for c in coords]


def parse_zonificacion_desc(html):
    """Extrae los pares clave-valor de la tabla HTML que QGIS/KML embebe
    en el campo 'description' de la capa de zonificación."""
    if not html:
        return {}
    cells = re.findall(r"<td[^>]*>(.*?)</td>", html, re.S)
    cells = [re.sub(r"<[^>]+>", "", c).strip() for c in cells]
    pairs = cells[1:]
    result = {}
    for i in range(0, len(pairs) - 1, 2):
        key, val = pairs[i], pairs[i + 1]
        if key:
            result[key] = val
    return result


def _kml_coords_to_ring(coords_text):
    ring = []
    for tok in coords_text.split():
        parts = tok.strip().split(",")
        if len(parts) >= 2:
            ring.append([float(parts[0]), float(parts[1])])
    return ring


def _kml_polygon_to_geojson(polygon_el):
    outer = polygon_el.find("./kml:outerBoundaryIs/kml:LinearRing/kml:coordinates", KML_NS)
    if outer is None or not outer.text:
        return None
    rings = [_kml_coords_to_ring(outer.text)]
    for inner in polygon_el.findall("./kml:innerBoundaryIs/kml:LinearRing/kml:coordinates", KML_NS):
        if inner.text:
            rings.append(_kml_coords_to_ring(inner.text))
    return rings


def load_kml_as_geojson(raw_bytes):
    """Convierte un KML (ya sea el contenido de un .kml o el doc.kml
    adentro de un .kmz) en un FeatureCollection GeoJSON, conservando el
    campo 'description' con la tabla HTML tal cual la escribió QGIS/Google
    Earth, para que parse_zonificacion_desc la siga entendiendo igual."""
    parser = etree.XMLParser(recover=True)
    root = etree.fromstring(raw_bytes, parser=parser)
    features = []
    for pm in root.findall(".//kml:Placemark", KML_NS):
        name_el = pm.find("kml:name", KML_NS)
        desc_el = pm.find("kml:description", KML_NS)
        name = name_el.text if name_el is not None else None
        description = desc_el.text if desc_el is not None else None

        polygons = pm.findall(".//kml:Polygon", KML_NS)
        rings_list = [r for r in (_kml_polygon_to_geojson(p) for p in polygons) if r]
        if not rings_list:
            continue
        if len(rings_list) == 1:
            geometry = {"type": "Polygon", "coordinates": rings_list[0]}
        else:
            geometry = {"type": "MultiPolygon", "coordinates": [r for r in rings_list]}

        features.append({
            "type": "Feature",
            "properties": {"Name": name, "description": description},
            "geometry": geometry,
        })
    return {"type": "FeatureCollection", "features": features}


def load_zonificacion_source():
    """Busca la capa de zonificación en source/ sin importar en qué
    formato la hayan exportado: GeoJSON, KML o KMZ."""
    for fname in ("zonificacion.geojson",):
        path = os.path.join(SOURCE, fname)
        if os.path.exists(path):
            data = json.load(open(path, encoding="utf-8"))
            return data, get_transformer(data)

    for fname in ("zonificacion.kmz",):
        path = os.path.join(SOURCE, fname)
        if os.path.exists(path):
            with zipfile.ZipFile(path) as z:
                kml_name = next(n for n in z.namelist() if n.lower().endswith(".kml"))
                raw = z.read(kml_name)
            return load_kml_as_geojson(raw), None

    for fname in ("zonificacion.kml",):
        path = os.path.join(SOURCE, fname)
        if os.path.exists(path):
            raw = open(path, "rb").read()
            return load_kml_as_geojson(raw), None

    raise FileNotFoundError(
        "No encontré la capa de zonificación en source/. Subí zonificacion.geojson, "
        "zonificacion.kml o zonificacion.kmz."
    )


def build_barrios():
    path = os.path.join(SOURCE, "barrios.geojson")
    data = json.load(open(path, encoding="utf-8"))
    transformer = get_transformer(data)
    out = {"type": "FeatureCollection", "features": []}
    for f in data["features"]:
        p = lower_keys(f["properties"])
        geom = f["geometry"]
        if not geom or not geom.get("coordinates"):
            continue
        out["features"].append({
            "type": "Feature",
            "properties": {
                "barrio": p.get("barrio"),
                "circ": p.get("circ"),
                "sec": p.get("sec"),
            },
            "geometry": {
                "type": geom["type"],
                "coordinates": reproject_coords(geom["coordinates"], transformer),
            },
        })
    json.dump(out, open(os.path.join(OUT, "barrios_final.geojson"), "w", encoding="utf-8"),
               ensure_ascii=False)
    print(f"barrios: {len(out['features'])} features")


def build_zonificacion():
    data, transformer = load_zonificacion_source()
    out = {"type": "FeatureCollection", "features": []}
    parsed = []
    for f in data["features"]:
        p = f["properties"]
        geom = f["geometry"]
        if not geom or not geom.get("coordinates"):
            continue
        d = parse_zonificacion_desc(p.get("description"))
        props = {
            "nombre": d.get("Nombre") or p.get("Name") or p.get("nombre"),
            "codigo": d.get("Codigo") or p.get("codigo"),
            "reglamento": d.get("REGLAMENT") or p.get("reglamento"),
            "fuente_url": d.get("Fuente") or p.get("fuente_url"),
            "uso_predominante": d.get("USO_AD-PRE") or p.get("uso_predominante"),
            "uso_complementario": d.get("USO_COMPLE") or p.get("uso_complementario"),
            "sup_lote_min": d.get("SUB_SUP_MI") or p.get("sup_lote_min"),
            "sup_lote_max": d.get("SUB_SUP_MA") or p.get("sup_lote_max"),
            "fos": d.get("IN-URB_FOS") or p.get("fos"),
            "fot": d.get("IN-URB_FOT") or p.get("fot"),
            "altura_max": d.get("IN-URB_HM") or p.get("altura_max"),
            "retiro": d.get("IN-URB_RET") or p.get("retiro"),
            "densidad_hab": d.get("DENS_HAB") or p.get("densidad_hab"),
            # el nombre de este campo cambió entre relevamientos ("HA" -> "SUP(HA)");
            # se prueban ambos para no depender de cuál venga en el archivo nuevo.
            "superficie_ha": d.get("SUP(HA)") or d.get("HA") or p.get("superficie_ha"),
        }
        parsed.append((props, geom))

    # Completar links faltantes: si otra zona con exactamente la misma
    # normativa ("reglamento") sí tiene un link http al Digesto cargado,
    # se usa ese mismo link. Esto no inventa fuentes: solo replica un
    # link que ya existe en el propio archivo para la misma ordenanza.
    from collections import Counter
    url_by_reglamento = {}
    for props, _ in parsed:
        reg = props.get("reglamento")
        url = props.get("fuente_url") or ""
        if reg and url.startswith("http"):
            url_by_reglamento.setdefault(reg, Counter())[url] += 1
    reglamento_best_url = {
        reg: counter.most_common(1)[0][0] for reg, counter in url_by_reglamento.items()
    }

    for props, geom in parsed:
        if not (props.get("fuente_url") or "").startswith("http"):
            reg = props.get("reglamento")
            if reg in reglamento_best_url:
                props["fuente_url"] = reglamento_best_url[reg]
        out["features"].append({
            "type": "Feature",
            "properties": props,
            "geometry": {
                "type": geom["type"],
                "coordinates": reproject_coords(geom["coordinates"], transformer),
            },
        })

    json.dump(out, open(os.path.join(OUT, "zonificacion_final.geojson"), "w", encoding="utf-8"),
               ensure_ascii=False)
    print(f"zonificacion: {len(out['features'])} features")


def build_nomenclatura(p):
    """Devuelve los 4 componentes catastrales relevantes por separado
    (se descartan departamento y ejido, que no se muestran en la web).
    p ya debe tener las claves en minúscula (ver lower_keys)."""
    return {
        "circunscripcion": p.get("circun"),
        "sector": p.get("sector"),
        "manzana": p.get("numero_div"),
        "parcela_cat": p.get("numero_par"),
    }


def _procesar_features(features, transformer, dominio, shards):
    n = 0
    for f in features:
        p = lower_keys(f["properties"])
        p = {k: v for k, v in p.items() if k not in CAMPOS_SENSIBLES}
        geom = f["geometry"]
        if not geom or not geom.get("coordinates"):
            continue
        partida = p.get("partida")
        if partida is None:
            continue
        direccion = None
        if p.get("calles"):
            direccion = f"{p.get('calles')} {p.get('numero') or ''}".strip()
        props = {
            "partida": partida,
            **build_nomenclatura(p),
            "barrio": p.get("barrios"),
            "direccion": direccion,
        }
        if dominio is not None:
            props["dominio"] = dominio
        new_geom = {
            "type": geom["type"],
            "coordinates": reproject_coords(geom["coordinates"], transformer),
        }
        shard_id = int(partida) % NSHARDS
        shards[shard_id]["features"].append({
            "type": "Feature", "properties": props, "geometry": new_geom
        })
        n += 1
    return n


def build_parcelas():
    shards = {i: {"type": "FeatureCollection", "features": []} for i in range(NSHARDS)}

    combinado_path = os.path.join(SOURCE, "parcelario.geojson")
    fiscal_path = os.path.join(SOURCE, "inmuebles_fiscales.geojson")
    privado_path = os.path.join(SOURCE, "inmuebles_privados.geojson")

    if os.path.exists(combinado_path):
        # Formato nuevo: una sola capa con todas las parcelas, sin dato
        # de dominio fiscal/privado (no viene en el archivo de origen).
        data = json.load(open(combinado_path, encoding="utf-8"))
        transformer = get_transformer(data)
        n = _procesar_features(data["features"], transformer, dominio=None, shards=shards)
        print(f"parcelario.geojson: {n} features (sin dato de dominio fiscal/privado)")

    elif os.path.exists(fiscal_path) and os.path.exists(privado_path):
        # Formato viejo: dos capas separadas, cada una aporta su dominio.
        for fname, dominio in [
            ("inmuebles_fiscales.geojson", "Fiscal"),
            ("inmuebles_privados.geojson", "Privado"),
        ]:
            path = os.path.join(SOURCE, fname)
            data = json.load(open(path, encoding="utf-8"))
            transformer = get_transformer(data)
            n = _procesar_features(data["features"], transformer, dominio=dominio, shards=shards)
            print(f"{fname}: {n} features")

    else:
        raise FileNotFoundError(
            "No encontré datos de parcelario en source/. Subí parcelario.geojson "
            "(formato combinado) o inmuebles_fiscales.geojson + inmuebles_privados.geojson "
            "(formato separado)."
        )

    for i in range(NSHARDS):
        out_path = os.path.join(OUT, f"parcelas_{i:02d}.geojson")
        json.dump(shards[i], open(out_path, "w", encoding="utf-8"),
                   ensure_ascii=False, separators=(",", ":"))
    total = sum(len(s["features"]) for s in shards.values())
    print(f"parcelas: {total} features en {NSHARDS} archivos")


if __name__ == "__main__":
    os.makedirs(OUT, exist_ok=True)
    build_barrios()
    build_zonificacion()
    build_parcelas()
    print("Listo.")
