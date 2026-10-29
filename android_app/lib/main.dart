import 'dart:io';

import 'package:flutter/material.dart';
import 'package:permission_handler/permission_handler.dart';

import 'finder.dart';

void main() => runApp(const FileRescueApp());

class FileRescueApp extends StatelessWidget {
  const FileRescueApp({super.key});

  @override
  Widget build(BuildContext context) {
    return MaterialApp(
      title: 'File Rescue',
      theme: ThemeData(
        colorSchemeSeed: const Color(0xFF2B6CB0),
        useMaterial3: true,
      ),
      darkTheme: ThemeData(
        colorSchemeSeed: const Color(0xFF2B6CB0),
        brightness: Brightness.dark,
        useMaterial3: true,
      ),
      home: const HomePage(),
    );
  }
}

class HomePage extends StatefulWidget {
  const HomePage({super.key});

  @override
  State<HomePage> createState() => _HomePageState();
}

class _HomePageState extends State<HomePage> {
  static const String _root = '/storage/emulated/0';
  static const String _out = '/storage/emulated/0/FileRescue';

  final List<Found> _found = <Found>[];
  final Set<String> _selected = <String>{};
  bool _scanning = false;
  String _status =
      'Tap Scan to look for deleted files that are still on this phone.';

  Future<void> _scan() async {
    var status = await Permission.manageExternalStorage.status;
    if (!status.isGranted) {
      status = await Permission.manageExternalStorage.request();
    }
    if (!status.isGranted) {
      setState(() => _status =
          'Allow "All files access" for File Rescue, then tap Scan again.');
      return;
    }
    setState(() {
      _scanning = true;
      _found.clear();
      _selected.clear();
      _status = 'Scanning...';
    });
    await for (final f in scan(_root)) {
      if (!mounted) {
        return;
      }
      setState(() => _found.add(f));
    }
    if (!mounted) {
      return;
    }
    setState(() {
      _scanning = false;
      _status = _found.isEmpty
          ? 'Nothing found. Trash is emptied after about 30 days and statuses after 24 hours.'
          : 'Found ${_found.length} files. Select the ones you want and tap Recover.';
    });
  }

  Future<void> _recoverSelected() async {
    var done = 0;
    for (final f in _found.where((f) => _selected.contains(f.path))) {
      try {
        await recover(f, _out);
        done++;
      } on FileSystemException {
        // skip files that cannot be copied
      }
    }
    if (!mounted) {
      return;
    }
    setState(() => _status = 'Copied $done files to $_out');
  }

  @override
  Widget build(BuildContext context) {
    return Scaffold(
      appBar: AppBar(title: const Text('File Rescue')),
      body: Column(
        children: <Widget>[
          Padding(
            padding: const EdgeInsets.all(16),
            child: Text(_status),
          ),
          if (_scanning) const LinearProgressIndicator(),
          Expanded(
            child: ListView(
              children: <Widget>[
                for (final kind in Kind.values) ..._section(kind),
              ],
            ),
          ),
        ],
      ),
      floatingActionButton: Row(
        mainAxisAlignment: MainAxisAlignment.end,
        children: <Widget>[
          if (_selected.isNotEmpty)
            FloatingActionButton.extended(
              heroTag: 'recover',
              onPressed: _recoverSelected,
              icon: const Icon(Icons.restore),
              label: Text('Recover ${_selected.length}'),
            ),
          const SizedBox(width: 12),
          FloatingActionButton.extended(
            heroTag: 'scan',
            onPressed: _scanning ? null : _scan,
            icon: const Icon(Icons.search),
            label: const Text('Scan'),
          ),
        ],
      ),
    );
  }

  List<Widget> _section(Kind kind) {
    final items = _found.where((f) => f.kind == kind).toList();
    if (items.isEmpty) {
      return <Widget>[];
    }
    return <Widget>[
      Padding(
        padding: const EdgeInsets.fromLTRB(16, 16, 16, 4),
        child: Text(
          '${kind.title}: ${items.length}',
          style: Theme.of(context).textTheme.titleSmall,
        ),
      ),
      for (final f in items)
        CheckboxListTile(
          value: _selected.contains(f.path),
          onChanged: (v) => setState(() {
            if (v == true) {
              _selected.add(f.path);
            } else {
              _selected.remove(f.path);
            }
          }),
          secondary: SizedBox(
            width: 52,
            height: 52,
            child: f.isImage
                ? Image.file(
                    File(f.path),
                    cacheWidth: 120,
                    fit: BoxFit.cover,
                    errorBuilder: (_, __, ___) => const Icon(Icons.broken_image),
                  )
                : const Icon(Icons.insert_drive_file),
          ),
          title: Text(f.displayName, maxLines: 1, overflow: TextOverflow.ellipsis),
          subtitle: Text('${(f.size / 1024).toStringAsFixed(0)} KB'),
        ),
    ];
  }
}
