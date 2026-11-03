// DOM helpers.
import { initials } from './lib/text.js';

export const $ = (id) => document.getElementById(id);

/** Create an element with an optional class and text. */
export function el(tag, cls, text) {
  const n = document.createElement(tag);
  if (cls) n.className = cls;
  if (text !== undefined) n.textContent = text;
  return n;
}

/** Initials as a placeholder; the logo is laid over them when it loads (and dropped if it fails). */
export function fillLogo(box, channel) {
  box.replaceChildren(el('span', '', initials(channel.name)));
  if (!channel.logo) return;
  const img = new Image();
  img.alt = '';
  img.loading = 'lazy';
  img.decoding = 'async';
  // RTVE's and 3Cat's SVGs are white on transparent; the rest are pictures, most of them with a white
  // background (the Facebook profiles). The dark theme dresses each kind differently (see style.css).
  const url = channel.logo || '';
  const isSvg = channel.logo_svg || /\.svg(\?|$)/i.test(url);
  const facebook = /^https?:\/\/(?:www\.)?graph\.facebook\.com\//i.test(url);
  if (isSvg) img.classList.add('svg');
  else img.classList.add('raster', facebook ? 'fb' : 'brand');
  img.addEventListener('load', () => img.classList.add('ld'));
  img.addEventListener('error', () => img.remove());
  img.src = '/logo?u=' + encodeURIComponent(channel.logo);
  box.append(img);
}
