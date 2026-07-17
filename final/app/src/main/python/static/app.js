// app.js - shared across every PyBox page.
//
// Used to be copy-pasted into 6 separate inline <script> blocks
// (authHeaders) and 4 separate ones (escapeHtml), with small drifts
// between copies (e.g. contacts.py's authHeaders took a `json` param
// the other 5 pages didn't have; search.py's escapeHtml didn't escape
// quotes). Now there's exactly one implementation of each, loaded once
// via theme.render() and cached by the WebView across page navigations
// instead of being re-parsed on every single nav.

// Builds the headers every mutating /api/* fetch() call needs.
// json defaults to true (matches how 5 of the 6 original copies always
// behaved); pass authHeaders(false) for a GET/DELETE with no JSON body
// if you want to skip the Content-Type header, though sending it on a
// bodyless request is harmless and Flask ignores it either way.
function authHeaders(json = true) {
  const token = (window.PyBoxAuth && window.PyBoxAuth.getToken) ? window.PyBoxAuth.getToken() : "";
  const h = { "X-PyBox-Token": token };
  if (json) h["Content-Type"] = "application/json";
  return h;
}

// Escapes the 5 characters that matter for safe HTML text/attribute
// interpolation. This is the more complete of the 4 original versions
// (one page's copy didn't escape quotes) - same behavior everywhere now.
function escapeHtml(str) {
  return String(str == null ? "" : str).replace(/[&<>"']/g, c => ({
    "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;",
  }[c]));
}
