"""Frozen desktop entry point, independent of the launch working directory."""
from multiprocessing import freeze_support
from camera_monitor.app import main


def smoke_test(result_path):
    """Exercise bundled Qt, decoder and vault imports without contacting cameras."""
    import sys
    from pathlib import Path
    import av
    from PySide6.QtCore import QTimer
    from PySide6.QtWidgets import QApplication
    from camera_monitor.app import Window
    from camera_monitor.credentials import CredentialStore

    assert av.Codec('h264', 'r').is_decoder
    if sys.platform == 'win32':
        assert CredentialStore().vault().priority > 0
    app = QApplication([])
    from PySide6.QtNetwork import QSslSocket
    from PySide6.QtWebSockets import QWebSocket
    # 下行通道走 wss：打包漏了 TLS 后端插件，现场会永远连不上，且没有任何报错
    assert QSslSocket.supportsSsl(), 'TLS backend missing from the bundle'
    assert QWebSocket() is not None
    from PySide6.QtGui import QImage
    from camera_monitor.snapshots import encode_jpeg
    # 漏了 imageformats/qjpeg 插件，截图会悄悄全部失败、不报任何错，只能在这里拦住
    probe = QImage(64, 36, QImage.Format.Format_RGB32)
    probe.fill(0)
    assert encode_jpeg(probe, 64, 100_000).startswith(b'\xff\xd8'), 'JPEG imageformat plugin missing from the bundle'
    window = Window()
    assert not window.windowIcon().isNull(), "Bundled application icon missing"
    window.show()
    QTimer.singleShot(1000, window.close)
    QTimer.singleShot(3000, app.quit)
    if app.exec() != 0:
        raise RuntimeError('GUI smoke test failed')
    Path(result_path).write_text('ok', encoding='utf-8')

if __name__ == '__main__':
    freeze_support()
    import sys
    if len(sys.argv) == 3 and sys.argv[1] == '--packaging-smoke-test':
        smoke_test(sys.argv[2])
    else:
        main()
