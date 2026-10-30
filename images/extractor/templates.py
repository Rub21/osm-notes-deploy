"""Split app templates from the words the user wrote.

Apps (MAPS.ME, Organic Maps, CoMaps, StreetComplete, OnOSM) add long fixed
text to notes. The model and the language detector should only read what
the person wrote, plus a short tag for the template.

split_note(text) -> Parsed(template, user_text, poi_type, theme, rule_category)
  template       short code, or None when the note has no app template
  user_text      words written by the person ("" when there are none)
  poi_type       OSM type from the template, e.g. "amenity-pharmacy"
  theme          theme code guessed from poi_type, or None
  rule_category  category set by rule when there is no user text, or None
"""

import re
from typing import NamedTuple


class Parsed(NamedTuple):
    template: str | None
    user_text: str
    poi_type: str | None
    theme: str | None
    rule_category: str | None


FLAGS = re.I | re.S

PLACE_GONE = re.compile(r"The place has gone or never existed", re.I)
POI_DATA = re.compile(r"OSM (data version|snapshot date)|POI (types?|name):|POI has no name", re.I)
# Everything from the first template marker to the end.
POI_TAIL = re.compile(
    r"\s*\(?\s*(The place has gone or never existed|This is an auto-generated note|"
    r"A user (of [\w. ]+ application )?has reported|POI name:|POI has no name|"
    r"POI types?:|OSM data version|OSM snapshot date|#mapsme|#organicmaps|#omaps|#comaps).*$",
    FLAGS)
LEADING_QUOTE = re.compile(r'^\s*"(.*?)"\s*(?=The place has gone|POI |OSM |This is|A user|#|$)', FLAGS)
USER_COMMENTS = re.compile(r'User comments:\s*"?(.*?)"?\s*(\(OSM|OSM data version|OSM snapshot date|POI |#|$)', FLAGS)
POI_TYPES = re.compile(r"POI types?:\s*((?:[\w:.-]+\s*)+)", re.I)
APP_HASHTAG = re.compile(r"\s*#(organicmaps|comaps|mapsme|omaps)\b.*$", FLAGS)
# Attachments StreetComplete adds after the text.
SC_ATTACHMENTS = re.compile(r"\s*(Attached photo\(s\)|GPS Trace):.*$", FLAGS)

# "Unable to answer ..." or "In context of overlay ..." + "via StreetComplete X:" + user text.
SC_PREFIXED = re.compile(
    r'^\s*(Unable to answer|In context of)\b.*?via StreetComplete(_ee)?[^:\n]*:\s*(.*)$', FLAGS)
# The signature can be followed by attachments ("Attached photo(s): ...").
SC_SIGNATURE = re.compile(r"\s*via StreetComplete(_ee)?\b.*$", FLAGS)
SC_CONTEXT = re.compile(r'^\s*In context of (overlay )?"[^"]*"\s*[–-][^–\n]*[–-]\s*https?://\S+\s*', re.I)

ONOSM = re.compile(r"onosm\.org submitted note from a business:", re.I)
ONOSM_FIELD = re.compile(
    r"\b(name|phone|website|twitter|hours|category|address|comments?):\s*(.*?)"
    r"(?=\s*\b(?:name|phone|website|twitter|hours|category|address|comments?):|$)", FLAGS)

OSB_MOVED = re.compile(r"^\s*Moved from OSB ID\d+:\s*(.*?)\s*(\[[^\]]*\])?\s*$", FLAGS)

# poi_type prefix (OSM key-value written with "-") -> theme code.
THEME_RULES = [
    ("food", r"^(cuisine-|amenity-(restaurant|cafe|bar|pub|fast_food|food_court|ice_cream|biergarten))"),
    ("health", r"^(amenity-(pharmacy|hospital|clinic|doctors|dentist|veterinary)|healthcare-)"),
    ("education", r"^amenity-(school|university|college|kindergarten|library|driving_school)"),
    ("transport", r"^(public_transport-|railway-|aeroway-|highway-bus_stop|amenity-(bus_station|ferry_terminal|taxi|bicycle_rental|car_rental))"),
    ("shop", r"^(shop-|amenity-marketplace)"),
    ("roads", r"^(highway-|barrier-|amenity-parking)"),
    ("buildings", r"^(building|entrance|addr)"),
    ("leisure", r"^(tourism-|leisure-|historic-|sport-)"),
    ("nature", r"^(natural-|landuse-|waterway-|water-)"),
    ("services", r"^(amenity-|office-|craft-|emergency-|man_made-|power-|telecom|toilets-)"),
]
THEME_RULES = [(code, re.compile(rx)) for code, rx in THEME_RULES]


def theme_for(poi_type: str | None) -> str | None:
    if not poi_type:
        return None
    for code, rx in THEME_RULES:
        if rx.search(poi_type):
            return code
    return None


def pick_poi_type(text: str) -> str | None:
    """POI types can list several values; keep the first one that has a theme."""
    m = POI_TYPES.search(text)
    if not m:
        return None
    types = [t.lower() for t in m.group(1).split() if "-" in t or t.isalpha()]
    for t in types:
        if theme_for(t) and not t.startswith("cuisine-"):
            return t
    return types[0] if types else None


def _clean(text: str) -> str:
    return " ".join((text or "").split()).strip(' "')


def split_note(text: str) -> Parsed:
    text = text or ""

    if PLACE_GONE.search(text) or POI_DATA.search(text):
        gone = bool(PLACE_GONE.search(text))
        m = LEADING_QUOTE.search(text) or USER_COMMENTS.search(text)
        user = m.group(1) if m else POI_TAIL.sub("", text)
        poi_type = pick_poi_type(text)
        user = _clean(user)
        rule = "place_changed" if gone and not user else None
        return Parsed("app_place_gone" if gone else "app_poi_data", user, poi_type,
                      theme_for(poi_type), rule)

    if APP_HASHTAG.search(text):
        return Parsed("app_note", _clean(APP_HASHTAG.sub("", text)), None, None, None)

    m = SC_PREFIXED.search(text)
    if m:
        template = "sc_unable" if m.group(1).lower().startswith("unable") else "sc_note"
        return Parsed(template, _clean(SC_ATTACHMENTS.sub("", m.group(3))), None, None, None)

    if SC_SIGNATURE.search(text):
        user = SC_CONTEXT.sub("", SC_SIGNATURE.sub("", text))
        return Parsed("sc_note", _clean(user), None, None, None)

    if ONOSM.search(text):
        body = ONOSM.sub("", text)
        fields = [(k.lower(), _clean(v)) for k, v in ONOSM_FIELD.findall(body)]
        user = " · ".join(f"{k}: {v}" for k, v in fields if v)
        # A business form always asks to add a place.
        return Parsed("onosm", user, None, None, None)

    m = OSB_MOVED.search(text)
    if m:
        return Parsed("osb_moved", _clean(m.group(1)), None, None, None)

    return Parsed(None, " ".join(text.split()), None, None, None)


if __name__ == "__main__":
    import sys
    for line in sys.stdin:
        print(split_note(line.rstrip("\n")))
