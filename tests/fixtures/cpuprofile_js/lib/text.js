'use strict';

function tokenize(text) {
  const words = [];
  let start = -1;
  for (let i = 0; i <= text.length; i++) {
    const c = i < text.length ? text.charCodeAt(i) : 32;
    const letter = (c >= 97 && c <= 122) || (c >= 65 && c <= 90);
    if (letter && start < 0) start = i;
    if (!letter && start >= 0) {
      words.push(text.slice(start, i).toLowerCase());
      start = -1;
    }
  }
  return words;
}

const countWords = (words) => {
  const counts = new Map();
  words.forEach((w) => counts.set(w, (counts.get(w) || 0) + 1));
  return counts;
};

function topN(counts, n) {
  return [...counts.entries()].sort((a, b) => b[1] - a[1] || (a[0] < b[0] ? -1 : 1)).slice(0, n);
}

module.exports = { tokenize, countWords, topN };
