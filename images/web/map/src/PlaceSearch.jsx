import { useState } from "react";
import { searchPlace } from "./api.js";

/** Search box over the map: Nominatim on Enter, then fit the map to the place. */
export default function PlaceSearch({ t, lang, onFly }) {
  const [place, setPlace] = useState("");
  const [places, setPlaces] = useState(null);

  const go = (r) => {
    const [s, n, w, e] = r.boundingbox.map(Number);
    onFly({ bbox: [[w, s], [e, n]] });
    setPlaces(null);
    setPlace(r.display_name.split(",")[0]);
  };
  const find = async (e) => {
    e.preventDefault();
    if (!place.trim()) return;
    setPlaces([]);
    try {
      const res = await searchPlace(place.trim(), lang);
      if (res.length === 1) go(res[0]);
      else setPlaces(res);
    } catch { setPlaces([]); }
  };

  return (
    <form className="place" onSubmit={find} role="search">
      <input value={place} onChange={(e) => setPlace(e.target.value)} placeholder={`🔍 ${t.placeHint}`}
             aria-label={t.place} />
      {places ? (
        <ul className="places">
          {places.length === 0 ? <li className="muted">{t.noPlace}</li> : places.map((r) => (
            <li key={r.place_id}><button type="button" onClick={() => go(r)}>{r.display_name}</button></li>
          ))}
        </ul>
      ) : null}
    </form>
  );
}
