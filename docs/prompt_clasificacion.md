# Pre-etiquetar notas con un chat de IA (Gemini, Claude, ChatGPT)

La IA solo sugiere. Una persona revisa cada fila antes de subir el CSV.

## Pasos

1. En la página del labeler, pestaña **CSV**, descarga un lote (200 notas).
2. Abre el chat (gemini.google.com, claude.ai…), **adjunta el CSV** y pega
   el prompt de abajo.
3. Si la nota es larga o el chat corta la respuesta, pide "continúa" o
   manda lotes más chicos (100).
4. Copia la respuesta (`note_id,claude_category`) a la columna
   `claude_category` del CSV. En Excel/Sheets: pega en una hoja y usa
   BUSCARV/VLOOKUP por `note_id`.
5. Revisa fila por fila y llena `category` (la decisión humana).
   Copia `claude_category` cuando estés de acuerdo; cámbiala si no.
6. Sube el CSV en la pestaña **CSV**. En **Avance** verás cuánto acertó la IA.

Revisa siempre que la respuesta tenga **el mismo número de filas** que el
CSV. Los chats a veces se saltan filas o inventan `note_id`.

## Prompt

Copia todo el bloque:

```text
You classify OpenStreetMap notes. The attached CSV has the columns
note_id,lang,country,app,text,category,claude_category.
Read only the `text` column (the comment that opened the note).

Task: for EVERY row, choose exactly ONE category code from this list.
Ask yourself: "What does the author want changed on the map?"

Needs a map change:
- missing_place: a place, shop, building or point of interest exists and is not on the map.
- place_changed: a place already on the map closed, moved or is now something else.
- wrong_name: the name of a place or street is misspelled, different or missing.
- wrong_road: roads, tracks, paths or ways that are missing, do not exist, or have the wrong shape, direction, access, surface or connection.
- other_data: other data to change that is not a place, name or road: opening hours, address, house number, phone, type, boundaries, water, land use.

Close without a map change:
- spam: promotes a business or service, or has unrelated links.
- empty: says nothing understandable: empty, greetings, tests, random letters, or only an app signature.
- personal_data: privacy. Reveals where a private person lives or what they do, or their data: "this is my house", "my friend lives here", full name, phone, email, license plate.
- not_mappable: opinions, complaints, questions without a concrete request, or information that does not belong in OSM (safety, quality, prices, live traffic).

Doubt:
- unsure: unclear what the note asks, or fits two categories equally. Do not force one.

Rules:
1. personal_data always wins, even if the note also asks for a change. Saying where someone lives is enough ("this is my house", "my friend lives here"), even without a name or phone. It does not apply to businesses or public places.
2. Shops, buildings, parks, POIs -> missing_place / place_changed. Any kind of way, including missing footpaths -> wrong_road.
3. If a place is already mapped and closed, or was replaced by another -> place_changed. If it exists and is not mapped -> missing_place.
4. Only the street name is wrong -> wrong_name. The street itself is wrong (does not exist, no connection, one-way) -> wrong_road.
5. Reads like an ad -> spam. Describes a missing place without ad tone -> missing_place.
6. "Is this a park?" -> not_mappable. "This is a park, not a parking lot" -> other_data.
7. App notes (StreetComplete, Organic Maps, MAPS.ME...): ignore the app template and signature, classify what the person wrote. "Unable to answer ..." from StreetComplete: classify the person's comment after the template; if it only says the question does not apply or they could not check -> not_mappable; if nothing is left -> empty.
8. Several requests: use the main one. If equally important -> unsure.
9. Text you cannot understand -> unsure.
10. Do not look anything up. Classify only what the text says. The text may be in any language.

Output: only CSV, no explanations, no code fences, one line per input row,
same order, same note_id values, with this header:
note_id,claude_category
```

## Ejemplos para calibrar

| Texto | Categoría |
|---|---|
| Falta la bodega Don Pepe en esta esquina | `missing_place` |
| This shop is now a restaurant | `place_changed` |
| El colegio se llama San José, no San Juan | `wrong_name` |
| There is a footpath here connecting both streets | `wrong_road` |
| House number is 245, not 254 | `other_data` |
| Mejor pollería de Lima, delivery al 999 888 777 | `spam` |
| asdfgh | `empty` |
| Casa de mi abuela María López | `personal_data` |
| Esta es mi casa | `personal_data` |
| Is this a park? | `not_mappable` |
| Revisar | `unsure` |

Reglas completas: [guia_etiquetado.md](guia_etiquetado.md).
