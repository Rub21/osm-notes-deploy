// Close notes on OSM from the browser, like iD: osm-auth logs in with a popup and keeps
// the token in this browser (localStorage). Our server never sees it; it only checks
// on the public OSM API that the note was closed (POST /api/map/close).
import { osmAuth } from "osm-auth";

let auth = null;
let checkedUid = null;   // OSM user id the token belongs to, checked once

export const canClose = (me) => Boolean(me?.notes_client_id && me?.user);

function getAuth(me) {
  if (!auth) {
    auth = osmAuth({
      client_id: me.notes_client_id,
      url: me.osm_url,
      apiUrl: me.osm_api_url,
      scope: "read_prefs write_notes",
      redirect_uri: `${location.origin}/land.html`,
    });
  }
  return auth;
}

/** Forget the OSM token in this browser (on logout). */
export function osmLogout(me) {
  if (me?.notes_client_id) getAuth(me).logout();
  checkedUid = null;
}

/**
 * Close a note on OSM. Must be called right in the click handler: the login popup
 * opens at once, or the browser blocks it. Resolves to "closed" or "already"
 * (someone closed it before), rejects with an Error (err.code = popup | other_user | expired).
 */
export function closeNote(me, noteId, text, switchUser = false) {
  const a = getAuth(me);
  if (switchUser) { a.logout(); checkedUid = null; }
  return new Promise((resolve, reject) => {
    a.authenticate((err) => {
      if (err) {
        const e = new Error(err.message || String(err));
        if (err.status === "popup-blocked") e.code = "popup";
        reject(e);
        return;
      }
      run(a, me, noteId, text).then(resolve, reject);
    }, switchUser ? { switchUser: true } : undefined);
  });
}

async function run(a, me, noteId, text) {
  const api = me.osm_api_url;
  if (checkedUid !== me.user.id) {
    // The token may belong to another OSM account than the one logged in here.
    const r = await a.fetch(`${api}/api/0.6/user/details.json`);
    if (r.status === 401) { a.logout(); throw Object.assign(new Error("expired"), { code: "expired" }); }
    if (!r.ok) throw new Error(`OSM ${r.status}`);
    const u = (await r.json()).user;
    if (u.id !== me.user.id) {
      a.logout();
      throw Object.assign(new Error(u.display_name), { code: "other_user", name: u.display_name });
    }
    checkedUid = u.id;
  }
  const p = new URLSearchParams();
  if (text.trim()) p.set("text", text.trim());
  const r = await a.fetch(`${api}/api/0.6/notes/${noteId}/close.json?${p}`, { method: "POST" });
  if (r.status === 401) {
    a.logout(); checkedUid = null;
    throw Object.assign(new Error("expired"), { code: "expired" });
  }
  if (r.status === 409) return "already";
  if (!r.ok) {
    let msg = `OSM ${r.status}`;
    try { msg = (await r.text()) || msg; } catch { /* no body */ }
    throw new Error(msg);
  }
  return "closed";
}
