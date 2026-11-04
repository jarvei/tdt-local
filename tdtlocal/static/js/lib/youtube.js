// YouTube links from the lists -> the official embedded player (no proxy, nothing is bypassed).

const CHANNEL_LIVE =
  /^https?:\/\/(?:www\.)?youtube\.com\/(?:channel\/UC[\w-]{22}|@[\w.-]+)\/live\/?(?:[?#].*)?$/i;
const DIRECT_LIVE = /^https?:\/\/(?:www\.)?youtube\.com\/live\/[\w-]{11}\/?(?:[?#].*)?$/i;
const VIDEO = [
  /^https?:\/\/(?:www\.|m\.)?youtube\.com\/(?:live|embed)\/([\w-]{11})/i,
  /^https?:\/\/youtu\.be\/([\w-]{11})/i,
  /^https?:\/\/(?:www\.|m\.)?youtube\.com\/watch\?(?:[^#]*&)?v=([\w-]{11})/i,
];

/** Whether `url` is a live page (channel or fixed id) whose current video the server has to resolve. */
export function ytLive(url) {
  return CHANNEL_LIVE.test(url) || DIRECT_LIVE.test(url);
}

/** Embed URL for a YouTube video id, or `null` if it is not one. */
export function ytVideoEmbed(id) {
  return /^[\w-]{11}$/.test(id) ? 'https://www.youtube-nocookie.com/embed/' + id : null;
}

/** Embed URL for a YouTube video link, or `null` if `url` is not a video (lives are resolved first). */
export function ytEmbed(url) {
  if (ytLive(url)) return null;
  for (const re of VIDEO) {
    const m = re.exec(url);
    if (m) return ytVideoEmbed(m[1]);
  }
  return null;
}
