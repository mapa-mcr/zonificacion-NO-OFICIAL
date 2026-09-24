# Actualización: parcelario 2025 y zonificación nueva

## Qué cambió en los datos

- **Parcelario:** ahora viene en un solo archivo combinado (`PARCELARIO_2025`), sin distinción de dominio fiscal/privado y sin ningún dato personal (ni nombres, ni dirección, ni estado de tenencia). Como no existe esa información en el archivo de origen, la etiqueta "Fiscal/Privado" directamente **no aparece** en el buscador para estas parcelas — no hacía falta que yo la sacara a mano, `build.py` ahora la omite sola cuando no viene en los datos.
- **Zonificación:** pasó de 151 a 198 zonas. La cargué directo desde el archivo `.kmz` que me pasaste, sin necesidad de que la reexportes desde QGIS — el script ahora entiende KML/KMZ directamente.

## Un dato que investigué y descarté

El parcelario nuevo tiene un campo `ZONA` (un número del 0 al 41) que parecía prometedor — pensé que podía ser un ID directo a la zona urbanística correspondiente, lo cual habría sido más preciso que el cálculo espacial que hace la página. Lo verifiqué cruzando una parcela real contra el mapa de zonificación, y **no coincide** con nada del archivo de zonificación (ni con su `id` ni con ningún otro campo). No sé qué representa ese número, así que lo dejé afuera del archivo publicado — mejor no mostrar un dato cuyo significado no puedo confirmar. La página sigue calculando la zona de cada parcela de la forma en que ya lo hacía (comparando la ubicación real contra el mapa de zonificación vigente), que es un método verificado.

## Nuevo formato de `source/` (más flexible que antes)

`build.py` ahora acepta más de un formato para cada capa, así no dependés de que el archivo te llegue siempre igual:

- **Parcelario:** subí `source/parcelario.geojson` si es un solo archivo combinado (como esta vez). Si en el futuro te llega de nuevo separado en fiscal/privado, subí `source/inmuebles_fiscales.geojson` + `source/inmuebles_privados.geojson` como antes — el script detecta solo cuál de los dos casos tiene y arma el dato de dominio únicamente cuando corresponde.
- **Zonificación:** subí `source/zonificacion.geojson`, `source/zonificacion.kml` o `source/zonificacion.kmz` — cualquiera de los tres. Ya no hace falta pasar por QGIS para exportarla si te la dan directo en KML/KMZ.
- **Barrios:** sin cambios, `source/barrios.geojson`.

## Reemplazar en tu repositorio

Este paquete trae `index.html`, `build.py` y la carpeta `data/` ya regenerada con los datos nuevos. Reemplazá los tres en tu repositorio (GitHub Desktop, arrastrando y confirmando "Reemplazar" como ya veníamos haciendo). No hace falta tocar `source/` esta vez porque ya generé `data/` yo mismo con tus archivos — pero si en algún momento querés que la actualización quede corriendo sola vía la Action, también podés subir los mismos `PARCELARIO_2025.geojson` (renombrado a `parcelario.geojson`) y `Zonificacion_por_Ordenanza.kmz` (renombrado a `zonificacion.kmz`) a `source/`, y `build.py` los va a reconocer igual.
