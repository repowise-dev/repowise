// Perf-dialect fixtures: expected hits are asserted in
// tests/unit/health/test_perf_dialects.py (Dart section).

import 'dart:io';

import 'package:http/http.dart' as http;

Future<void> serialFetch(List<Uri> urls) async {
  for (final u in urls) {
    final r = await http.get(u); // io_in_loop network + serial_await_in_loop
  }
}

Future<void> readFiles(List<File> files) async {
  for (final f in files) {
    final t = await f.readAsString(); // io_in_loop filesystem + serial_await
  }
}

Future<void> queryRows(dynamic db, List<int> ids) async {
  for (final id in ids) {
    final rows = await db.rawQuery('SELECT * FROM t WHERE id = ?'); // db
  }
}

String concat(List<String> parts) {
  var s = '';
  for (final p in parts) {
    s += 'chunk'; // string_concat_in_loop
  }
  return s;
}

void makeClients(List<int> xs) {
  for (final x in xs) {
    final c = HttpClient(); // resource_construction_in_loop
  }
}

void constantBound() {
  for (var i = 0; i < 3; i++) {
    http.get(Uri.parse('u')); // constant loop -> no hit
  }
}
