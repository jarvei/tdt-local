// Atresplayer's embedded player: a recording (U7D) or a live channel with no id in the catalogue. It is shown in
// an <iframe> and nothing goes through the proxy; its sound and its ads are its own, the page cannot change them.

// the live player of a channel (`/embed/live/<channel>/`) or a recording or episode (`/embed/video/<24 hex digits>/`)
const EMBED = /^https:\/\/www\.atresplayer\.com\/embed\/(?:live\/([a-z0-9-]{2,40})|video\/([0-9a-f]{24}))\/?(?:[?#].*)?$/i;

const atresLive = (slug) => 'https://www.atresplayer.com/embed/live/' + slug + '/';

/** Address of the embeddable player of a recording (`id` is the 24 hex digits at the end of its address). */
export const atresVideoLink = (id) => 'https://www.atresplayer.com/embed/video/' + id.toLowerCase() + '/';

/** The embed URL for an Atresplayer player link (live or recording), or `null` if `url` is not one. */
export function atresEmbed(url) {
  const m = EMBED.exec(String(url || ''));
  if (!m) return null;
  return m[1] ? atresLive(m[1].toLowerCase()) : atresVideoLink(m[2]);
}

/** The channel's own page on atresplayer.com, for the "Abrir la web de la cadena" button, or `''`. */
export function atresWeb(url) {
  const m = EMBED.exec(String(url || ''));
  return m && m[1] ? 'https://www.atresplayer.com/directos/' + m[1].toLowerCase() + '/' : '';
}
