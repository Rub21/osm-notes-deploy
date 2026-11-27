import { useEffect, useRef, useState } from "react";
import { ensureScope } from "./osm.js";

// Edit mode: the iD editor (npm @openstreetmap/id, copied to /id by copy-id.mjs) over
// the map, with the note panel still on the side. iD is loaded the first time and kept
// alive after that (its edits, cache and login stay when you go back to the map).
// iD logs in with osm-auth like our close button: same OSM app and the same token in
// this browser (see osm.js). OSM zoom in iD = MapLibre zoom + 1 (256 vs 512 px tiles).

let loading = null;   // Promise of window.iD
let ctx = null;       // iD context, made once
let holder = null;    // iD's root element, moved in and out of the page

function loadId() {
  if (!loading) {
    loading = new Promise((resolve, reject) => {
      const css = document.createElement("link");
      css.rel = "stylesheet";
      css.href = "/id/iD.css";
      document.head.appendChild(css);
      const s = document.createElement("script");
      s.src = "/id/iD.min.js";
      s.onload = () => resolve(window.iD);
      s.onerror = () => { loading = null; reject(new Error("iD did not load")); };
      document.head.appendChild(s);
    });
  }
  return loading;
}

/** Where iD is looking now, as a MapLibre view (null if iD never opened). */
export function editorView() {
  if (!ctx) return null;
  const [lon, lat] = ctx.map().center();
  return { lon, lat, zoom: Math.max(0, ctx.map().zoom() - 1) };
}

export default function Editor({ me, lang, note, t, onChangeset }) {
  const host = useRef(null);
  const [error, setError] = useState("");
  const [ready, setReady] = useState(Boolean(ctx));
  const changeset = useRef(onChangeset);
  changeset.current = onChangeset;

  useEffect(() => {
    let live = true;
    loadId().then((iD) => {
      if (!live) return;
      if (!iD.utilDetect().support) throw new Error(t.idUnsupported);
      if (!ctx) {
        ensureScope(me);
        // iD reads the start position from the URL hash, so put the note there first.
        history.replaceState(null, "", `${location.pathname}${location.search}#map=19.00/${note.lat}/${note.lon}`);
        holder = document.createElement("div");
        holder.className = "id-root";
        host.current.appendChild(holder);
        ctx = iD.coreContext()
          .assetPath("/id/")
          .setsDocumentTitle(false)
          .locale(lang)
          .containerNode(holder);
        ctx.connection().switch({
          url: me.osm_url, apiUrl: me.osm_api_url, client_id: me.notes_client_id,
          redirect_uri: `${location.origin}/land.html`,
        });
        ctx.init();
        ctx.features().disable("boundaries");
      } else {
        host.current.appendChild(holder);
        window.dispatchEvent(new Event("resize"));   // iD measures its panes on resize
        ctx.enter(iD.modeBrowse(ctx));               // nothing selected from last time
      }
      ctx.map().centerZoom([note.lon, note.lat], 19);
      try { ctx.layers().layer("notes").enabled(true); } catch { /* no notes layer */ }
      // After a save in iD: offer to close the note with the changeset link.
      ctx.uploader().on("resultSuccess.notesmap", (cs) => changeset.current?.(cs?.id));
      setReady(true);
    }).catch((e) => { if (live) setError(e.message); });
    return () => {
      live = false;
      ctx?.uploader().on("resultSuccess.notesmap", null);
      if (holder?.parentNode) holder.parentNode.removeChild(holder);
    };
    // Open once per note; moving around is done inside iD.
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [note.note_id]);

  return (
    <div className="editor" ref={host}>
      {!ready && !error ? <div className="map-loading" role="status"><span className="spin" />{t.idLoading}</div> : null}
      {error ? <p className="editor-error">{error}</p> : null}
    </div>
  );
}
