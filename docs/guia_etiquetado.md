# Guía de etiquetado de notas OSM

Esta guía la usan las personas que etiquetan y también Claude cuando
pre-etiqueta el CSV. Las categorías y los temas viven en `sql/ml_schema.sql`
(tablas `ml.categories` y `ml.themes`). Si cambias uno, cambia los dos archivos.

Cada nota lleva **dos etiquetas**:

- **Categoría**: qué hay que hacer (lugar faltante, spam, privacidad…).
- **Tema**: de qué trata (salud, transporte, comida…). Muchas veces ya viene
  sugerido desde el tipo de POI de la app; cámbialo solo si está mal.

## Cómo etiquetar

1. Lee **solo el texto con que se abrió la nota**. No uses los comentarios
   de después ni si la nota está cerrada. Si la nota trae una plantilla de
   app, lee lo que escribió la persona (texto grande en la página, columna
   `user_text` en el CSV). La plantilla aparece aparte, en gris.
2. Pregúntate: **¿qué pide cambiar en el mapa quien abrió la nota?**
3. Elige **una sola** categoría. Si dudas entre dos, usa `unsure`.
4. No busques en el mapa ni en internet. Se etiqueta lo que dice el texto.

## Categorías

### Hay que tocar el mapa

| Código | Categoría | Cuándo usarla |
|---|---|---|
| `missing_place` | Lugar faltante | Un lugar, negocio, edificio o punto de interés existe y no está en el mapa. |
| `place_changed` | Lugar cerrado o cambiado | Un lugar que ya está en el mapa cerró, se mudó o ahora es otra cosa. |
| `wrong_name` | Nombre incorrecto | El nombre de un lugar o calle está mal escrito, es otro o falta. |
| `wrong_road` | Calle o camino faltante o incorrecto | Calles, caminos, senderos o vías que faltan, no existen o tienen mal la forma, el sentido, el acceso o la conexión. |
| `other_data` | Otro dato incorrecto | Hay que cambiar algo que no es lugar, nombre ni calle: horario, dirección, número, teléfono, tipo, límites, agua, uso del suelo. |

### Se cierra sin tocar el mapa

| Código | Categoría | Cuándo usarla |
|---|---|---|
| `spam` | Spam / publicidad | Promociona un negocio o servicio, o tiene enlaces sin relación con el mapa. |
| `empty` | Vacía o sin sentido | No dice nada que se pueda entender ni verificar: vacía, saludos, pruebas, letras al azar. |
| `personal_data` | Privacidad / datos personales | Revela dónde vive o qué hace una persona privada, o sus datos: "esta es mi casa", "aquí vive mi amigo", nombre completo, teléfono, correo, placa. La nota se oculta. |
| `not_mappable` | Opinión o no mapeable | Opiniones, quejas, preguntas o información que no va en OSM: seguridad, calidad, precios, tráfico del momento. |

### Dudas

| Código | Categoría | Cuándo usarla |
|---|---|---|
| `unsure` | No sé / ambigua | No queda claro qué pide la nota o encaja igual en dos categorías. No fuerces una. |

## Reglas para casos dudosos

1. **`personal_data` gana siempre.** "Mi casa es la azul, llamar al
   987654321" → `personal_data`, aunque también pida agregar algo.
   Basta con que diga dónde vive alguien ("esta es mi casa", "aquí vive
   mi amigo", "la casa de la señora Rosa"), aunque no tenga teléfono ni
   apellido. No aplica a negocios ni a lugares públicos ("mi colegio",
   "la tienda de mi tío" con su nombre comercial → la categoría normal).
2. **Lugar vs. calle.** Negocios, edificios, parques y puntos de interés →
   `missing_place` o `place_changed`. Vías de cualquier tipo, incluidos los
   senderos que faltan → `wrong_road`.
3. **Cerró vs. falta.** Si el lugar ya está en el mapa y cerró →
   `place_changed`. Si no está y existe → `missing_place`. Si un lugar
   reemplazó a otro ("donde estaba la farmacia ahora hay una panadería") →
   `place_changed`.
4. **Nombre de calle.** Si el problema es solo el nombre → `wrong_name`.
   Si la calle en sí está mal (no existe, no conecta, es de un sentido) →
   `wrong_road`.
5. **Publicidad con dato útil.** "Pollería Don Pepe, Av. Lima 123, abierto
   24h, delivery 999…": si el texto se lee como anuncio → `spam`. Si
   describe un lugar que falta, sin tono de anuncio → `missing_place`.
6. **Pregunta sin pedido concreto.** "¿Esto es un parque?" →
   `not_mappable`. "Esto es un parque, no un estacionamiento" →
   `other_data`.
7. **Notas de apps** (StreetComplete, Organic Maps…). Ignora la firma de la
   app y clasifica el pedido. Si solo queda la firma → `empty`.
   Las notas "The place has gone or never existed" de MAPS.ME, Organic Maps
   y CoMaps sin texto de la persona se etiquetan solas como
   `place_changed` y no aparecen para etiquetar. Si la persona escribió
   algo, clasifica eso: "ya cerró" → `place_changed`, "esta es mi casa" →
   `personal_data`.
   En los "Unable to answer…" de StreetComplete clasifica el comentario
   de la persona: si solo dice que la pregunta no aplica o no pudo
   verificarlo → `not_mappable`.
8. **Varios pedidos en una nota.** Usa el primero o el principal. Si son
   igual de importantes → `unsure`.
9. **Otro idioma o texto que no entiendes** → `unsure`.

## Temas

| Código | Tema | Tags de OSM |
|---|---|---|
| `food` | Comida y bebida | `amenity=restaurant/cafe/bar/fast_food` |
| `shop` | Tiendas | `shop=*` |
| `health` | Salud | `amenity=pharmacy/hospital/clinic/doctors` |
| `education` | Educación | `amenity=school/university/kindergarten` |
| `transport` | Transporte público | `highway=bus_stop`, `railway=*`, `public_transport=*` |
| `roads` | Calles y caminos | `highway=*` (vías), `barrier=*`, estacionamientos |
| `buildings` | Edificios y direcciones | `building=*`, `addr:*`, entradas |
| `leisure` | Turismo y ocio | `tourism=*`, `leisure=*`, `historic=*` |
| `nature` | Naturaleza y agua | `natural=*`, `landuse=*`, `waterway=*` |
| `services` | Servicios y otros | otros `amenity=*` (bancos, correo, baños…), `office=*` |
| `none` | No aplica | spam, vacías, privacidad, opiniones generales |

Si la nota no habla de nada concreto, deja el tema vacío o usa `none`.

## Ejemplos

| Texto | Categoría |
|---|---|
| Falta la bodega Don Pepe en esta esquina | `missing_place` |
| New bakery opened here last month | `missing_place` |
| La farmacia Inkafarma cerró hace meses | `place_changed` |
| This shop is now a restaurant | `place_changed` |
| El colegio se llama San José, no San Juan | `wrong_name` |
| Esta calle no tiene salida | `wrong_road` |
| There is a footpath here connecting both streets | `wrong_road` |
| This road is one-way northbound | `wrong_road` |
| El horario es de 8 a 6 | `other_data` |
| House number is 245, not 254 | `other_data` |
| Mejor pollería de Lima, delivery al 999 888 777 | `spam` |
| ok | `empty` |
| asdfgh | `empty` |
| Casa de mi abuela María López | `personal_data` |
| Esta es mi casa | `personal_data` |
| My friend lives here, blue door | `personal_data` |
| Zona peligrosa de noche | `not_mappable` |
| Muy buen servicio, recomendado | `not_mappable` |
| Revisar | `unsure` |

## Formato del CSV

```
note_id,lang,country,app,template,user_text,text,category,theme,claude_category,claude_theme
```

- `user_text` es lo que escribió la persona; `template` dice qué plantilla
  de app tenía la nota; `text` es el texto completo.
- Llena `category` y `theme` con el **código** (por ejemplo `missing_place`,
  `health`) o con el nombre en español o inglés.
- `claude_theme` viene sugerido desde el tipo de POI cuando la app lo trae.
- Si llenas solo `claude_category` (y `claude_theme`), la fila va a la
  pestaña **Revisar** de la página.
- `claude_category` es la sugerencia de Claude. No la borres: sirve para
  medir en cuántas notas acertó.
- Si dejas `category` vacía, esa fila no se guarda.
