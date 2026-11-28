#!/usr/bin/env node
'use strict';

const crypto = require('crypto');
const { mapAll } = require('./vendor/tinydep');
const { tokenize, countWords, topN } = require('./lib/text');
const { Matrix, stats } = require('./lib/matrix');

function makeText(words) {
  const vocab = ['alpha', 'beta', 'gamma', 'delta', 'epsilon', 'zeta', 'eta', 'theta'];
  const parts = [];
  for (let i = 0; i < words; i++) parts.push(vocab[(i * 7919) % vocab.length]);
  return parts.join(' ');
}

function hashAll(rounds) {
  let digest = 'seed';
  for (let i = 0; i < rounds; i++) {
    digest = crypto.createHash('sha256').update(digest).digest('hex');
  }
  return digest;
}

function squareEach(values) {
  return mapAll(values, function square(v) {
    let acc = 0;
    for (let i = 0; i < 200; i++) acc += Math.sqrt(v * i + 1);
    return acc;
  });
}

function main() {
  const text = makeText(2000000);
  const counts = countWords(tokenize(text));
  const m = new Matrix(220);
  const product = m.multiply(m).multiply(m);
  const values = squareEach(Array.from({ length: 100000 }, (_, i) => i));
  console.log(topN(counts, 3), product.trace(), stats.mean(values), hashAll(200000).slice(0, 8));
}

main();
