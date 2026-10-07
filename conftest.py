import os
import pytest

if not os.environ.get('DISPLAY'):
    os.environ.setdefault('QT_QPA_PLATFORM', 'offscreen')


@pytest.fixture(scope='session')
def qapp():
    from PyQt6.QtWidgets import QApplication
    app = QApplication.instance() or QApplication([])
    yield app


@pytest.fixture
def dialogs(monkeypatch):
    from PyQt6.QtWidgets import QMessageBox
    messages = []
    for kind in ('information','warning','critical'):
        monkeypatch.setattr(QMessageBox,kind,lambda parent,title,text,*args: messages.append((title,text)))
    return messages
