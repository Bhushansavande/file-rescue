import 'dart:io';

/// The kinds of deleted-but-still-on-the-phone files this app can find.
enum Kind { trash, status, thumbnails }

extension KindInfo on Kind {
  String get title {
    switch (this) {
      case Kind.trash:
        return 'Trash (kept about 30 days)';
      case Kind.status:
        return 'WhatsApp statuses (kept about 24 hours)';
      case Kind.thumbnails:
        return 'Thumbnails (small previews)';
    }
  }

  String get folder {
    switch (this) {
      case Kind.trash:
        return 'trash';
      case Kind.status:
        return 'whatsapp-status';
      case Kind.thumbnails:
        return 'thumbnails';
    }
  }
}

class Found {
  Found(this.path, this.kind, this.size, this.displayName);

  final String path;
  final Kind kind;
  final int size;
  final String displayName;

  bool get isImage {
    final p = displayName.toLowerCase();
    return p.endsWith('.jpg') ||
        p.endsWith('.jpeg') ||
        p.endsWith('.png') ||
        p.endsWith('.webp') ||
        p.endsWith('.gif');
  }
}

final RegExp _trashed = RegExp(r'^\.trashed-(\d+)-(.+)$');

/// Which kind is this path? Same rules as the Python tool (`filerescue android`).
Kind? classify(String path) {
  final p = path.replaceAll('\\', '/');
  final low = p.toLowerCase();
  final base = p.substring(p.lastIndexOf('/') + 1);
  if (_trashed.hasMatch(base) ||
      low.contains('/.trash/') ||
      low.contains('/.recycle') ||
      low.contains('/.deleted/')) {
    return Kind.trash;
  }
  if (low.contains('/.statuses/')) {
    return base.startsWith('.nomedia') ? null : Kind.status;
  }
  if (low.contains('/.thumbnails/')) {
    return Kind.thumbnails;
  }
  return null;
}

/// '.trashed-1735689600-IMG_1.jpg' -> 'IMG_1.jpg'
String originalName(String fileName) {
  final m = _trashed.firstMatch(fileName);
  if (m != null) {
    return m.group(2)!;
  }
  return fileName.startsWith('.') ? fileName.substring(1) : fileName;
}

const List<String> _skip = <String>[
  '/Android/data',
  '/Android/obb',
];

/// Walks [root] and reports every recoverable file. Folders that cannot be read are skipped.
Stream<Found> scan(String root) async* {
  final pending = <Directory>[Directory(root)];
  while (pending.isNotEmpty) {
    final dir = pending.removeLast();
    List<FileSystemEntity> items;
    try {
      items = await dir.list(followLinks: false).toList();
    } on FileSystemException {
      continue;
    }
    for (final e in items) {
      if (e is Directory) {
        final p = e.path;
        if (_skip.any((s) => p.endsWith(s))) {
          continue;
        }
        pending.add(e);
      } else if (e is File) {
        final kind = classify(e.path);
        if (kind == null) {
          continue;
        }
        try {
          final size = await e.length();
          final name = e.path.substring(e.path.lastIndexOf('/') + 1);
          yield Found(e.path, kind, size, originalName(name));
        } on FileSystemException {
          continue;
        }
      }
    }
  }
}

/// Copies [f] into [outRoot]/<kind folder>/ and returns the new path.
Future<String> recover(Found f, String outRoot) async {
  final folder = Directory('$outRoot/${f.kind.folder}');
  await folder.create(recursive: true);
  var target = '${folder.path}/${f.displayName}';
  var i = 1;
  while (await File(target).exists()) {
    final dot = f.displayName.lastIndexOf('.');
    final stem = dot > 0 ? f.displayName.substring(0, dot) : f.displayName;
    final ext = dot > 0 ? f.displayName.substring(dot) : '';
    target = '${folder.path}/${stem}_$i$ext';
    i++;
  }
  await File(f.path).copy(target);
  return target;
}
