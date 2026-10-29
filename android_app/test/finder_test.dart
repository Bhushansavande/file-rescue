import 'package:file_rescue_app/finder.dart';
import 'package:flutter_test/flutter_test.dart';

void main() {
  test('classify matches the Python tool', () {
    expect(classify('/sdcard/DCIM/Camera/.trashed-1893456000-a.jpg'), Kind.trash);
    expect(classify('/sdcard/Android/data/x/files/.Trash/a.jpg'), Kind.trash);
    expect(classify('/sdcard/WhatsApp/Media/.Statuses/v.mp4'), Kind.status);
    expect(classify('/sdcard/WhatsApp/Media/.Statuses/.nomedia'), isNull);
    expect(classify('/sdcard/DCIM/.thumbnails/123'), Kind.thumbnails);
    expect(classify('/sdcard/DCIM/Camera/IMG_1.jpg'), isNull);
  });

  test('originalName strips the trash prefix', () {
    expect(originalName('.trashed-1893456000-IMG_1.jpg'), 'IMG_1.jpg');
    expect(originalName('.hidden.jpg'), 'hidden.jpg');
    expect(originalName('plain.jpg'), 'plain.jpg');
  });
}
