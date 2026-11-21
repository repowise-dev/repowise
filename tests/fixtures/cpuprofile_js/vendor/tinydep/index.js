'use strict';

function mapAll(items, fn) {
  const out = new Array(items.length);
  for (let i = 0; i < items.length; i++) out[i] = fn(items[i], i);
  return out;
}

module.exports = { mapAll };
