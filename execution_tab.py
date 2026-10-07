import os
import signal
import subprocess
import threading
from pathlib import Path

from PyQt6.QtCore import QThread, pyqtSignal
from PyQt6.QtWidgets import (QWidget, QVBoxLayout, QHBoxLayout, QPushButton,
    QLabel, QRadioButton, QSpinBox, QComboBox, QTextEdit, QGroupBox,
    QFormLayout)

from case_qaqc import SOLVERS, check_case, mesh_passed
from foam_io import write_case_files


class OpenFOAMWorker(QThread):
    """Execute an argument list without a shell and reap it before finishing."""
    output_signal = pyqtSignal(str)
    finished_signal = pyqtSignal(int)

    def __init__(self, command, cwd, parent=None):
        super().__init__(parent)
        if isinstance(command, str):
            raise TypeError('Commands must be argument lists.')
        self.command = list(command)
        self.cwd = str(cwd)
        self.process = None
        self._cancelled = threading.Event()
        self._lock = threading.Lock()

    @property
    def cancelled(self):
        return self._cancelled.is_set()

    def _signal_process(self, force=False):
        with self._lock:
            process = self.process
            # The group can outlive its parent while a child holds stdout open.
            if process is None or (process.poll() is not None and not force):
                return
            try:
                if os.name == 'posix':
                    os.killpg(process.pid, signal.SIGKILL if force else signal.SIGTERM)
                elif force:
                    subprocess.run(['taskkill', '/PID', str(process.pid), '/T', '/F'],
                                   stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
                else:
                    process.terminate()
            except ProcessLookupError:
                pass

    def run(self):
        code = -1
        self.output_signal.emit('--- Executing: ' + ' '.join(self.command) + ' ---')
        self.output_signal.emit('Case: ' + self.cwd)
        try:
            if not self.cancelled:
                with self._lock:
                    environment = os.environ.copy()
                    environment['PWD'] = self.cwd
                    self.process = subprocess.Popen(self.command, cwd=self.cwd,
                        stdout=subprocess.PIPE, stderr=subprocess.STDOUT,
                        text=True, errors='replace', bufsize=1, shell=False,
                        start_new_session=(os.name == 'posix'), env=environment)
                if self.cancelled:
                    self._signal_process()
                for line in self.process.stdout:
                    self.output_signal.emit(line.rstrip('\r\n'))
                self.process.stdout.close()
                code = self.process.wait()
        except Exception as exc:
            self.output_signal.emit(f'ERROR: Failed to execute command: {exc}')
            self._signal_process(force=True)
            if self.process is not None:
                self.process.wait()
        finally:
            self.finished_signal.emit(code)

    def stop(self):
        self._cancelled.set()
        self._signal_process()
        timer = threading.Timer(2, self._signal_process, kwargs={'force': True})
        timer.daemon = True
        timer.start()


class ExecutionTab(QWidget):
    busy_changed = pyqtSignal(bool)
    def __init__(self, parent=None):
        super().__init__(parent)
        self.case_dir = ''
        self.worker = None
        self._operation = None
        self._output = []
        self.setup_ui()

    def setup_ui(self):
        layout = QVBoxLayout(self)
        self.case_label = QLabel('Select a case folder at the top of the window.')
        self.case_label.setWordWrap(True)
        layout.addWidget(self.case_label)
        env = QGroupBox('Execution environment')
        env_layout = QHBoxLayout(env)
        self.rb_local = QRadioButton('Run on this Linux/OpenFOAM machine')
        self.rb_local.setChecked(True)
        self.rb_hpc = QRadioButton('HPC submission (not implemented)')
        self.rb_hpc.setEnabled(False)
        self.rb_hpc.setToolTip('Use your approved scheduler/SSH workflow outside this GUI.')
        env_layout.addWidget(self.rb_local)
        env_layout.addWidget(self.rb_hpc)
        layout.addWidget(env)
        parallel = QGroupBox('Solver and parallel setup')
        form = QFormLayout(parallel)
        self.combo_solver = QComboBox()
        self.combo_solver.addItems(SOLVERS)
        self.combo_solver.currentTextChanged.connect(lambda: self._clear_validation())
        form.addRow('Target solver:', self.combo_solver)
        self.spin_cores = QSpinBox()
        self.spin_cores.setRange(1, 256)
        self.spin_cores.setValue(1)
        self.spin_cores.valueChanged.connect(lambda: self._clear_validation())
        form.addRow('MPI ranks (1 = serial):', self.spin_cores)
        self.decompose_axis = QComboBox()
        self.decompose_axis.addItems(['X','Y','Z'])
        form.addRow('Uniform decomposition axis:', self.decompose_axis)
        layout.addWidget(parallel)
        self.btn_qaqc = QPushButton('Check case and mesh')
        self.btn_qaqc.clicked.connect(self.run_qaqc)
        self.btn_mesh = QPushButton('Generate initial mesh (blockMesh)')
        self.btn_mesh.clicked.connect(self.run_block_mesh)
        self.btn_decompose = QPushButton('Write decomposition and run decomposePar')
        self.btn_decompose.setToolTip('Backs up the old dictionary; writes simple decomposition along the selected axis.')
        self.btn_decompose.clicked.connect(self.run_decompose)
        self.btn_run = QPushButton('Run solver')
        self.btn_run.clicked.connect(self.run_solver)
        self.btn_reconstruct = QPushButton('Reconstruct latest results')
        self.btn_reconstruct.clicked.connect(self.run_reconstruct)
        for button in (self.btn_mesh, self.btn_qaqc, self.btn_decompose, self.btn_run, self.btn_reconstruct):
            layout.addWidget(button)
        self.status_label = QLabel('Not checked')
        layout.addWidget(self.status_label)
        self.console = QTextEdit()
        self.console.setReadOnly(True)
        self.console.document().setMaximumBlockCount(10000)
        layout.addWidget(self.console, 1)
        self.btn_stop = QPushButton('Stop running command')
        self.btn_stop.setEnabled(False)
        self.btn_stop.clicked.connect(self.stop_simulation)
        layout.addWidget(self.btn_stop)

    def set_case_directory(self, path):
        self.case_dir = path
        self.case_label.setText('Case: ' + path if path else 'Select a case folder.')
        self._clear_validation()

    def set_solver(self, name):
        if self.combo_solver.findText(name) < 0:
            self.combo_solver.addItem(name)
        self.combo_solver.setCurrentText(name)

    def _clear_validation(self):
        self.status_label.setText('Not checked; run case and mesh checks before solving.')

    def append_log(self, text):
        # Solver output is plain text, even if it happens to contain HTML tags.
        self.console.moveCursor(self.console.textCursor().MoveOperation.End)
        self.console.insertPlainText(str(text) + '\n')
        self.console.ensureCursorVisible()

    def toggle_ui_state(self, running):
        for control in (self.btn_mesh, self.btn_qaqc, self.btn_decompose, self.btn_run,
                        self.btn_reconstruct, self.combo_solver, self.spin_cores, self.decompose_axis):
            control.setEnabled(not running)
        self.btn_stop.setEnabled(running)
        self.busy_changed.emit(running)

    def _preflight(self, parallel=False):
        report = check_case(self.case_dir, self.combo_solver.currentText(),
                            self.spin_cores.value() if parallel else 1)
        for message in report.warnings:
            self.append_log('WARNING: ' + message)
        for message in report.errors:
            self.append_log('ERROR: ' + message)
        if not report.ok:
            self.status_label.setText('Case check failed; see the errors below.')
        return report.ok

    def execute_command(self, command, operation=None):
        if self.worker is not None:
            self.append_log('A command is already running; stop it or wait for completion.')
            return False
        if not self.case_dir or not Path(self.case_dir).is_dir():
            self.append_log('Select an existing case folder first.')
            return False
        self._operation = operation
        self._output = []
        self.toggle_ui_state(True)
        self.worker = OpenFOAMWorker(command, self.case_dir, self)
        self.worker.output_signal.connect(self._receive_output)
        self.worker.finished_signal.connect(self.on_process_finished)
        # Release the worker only after QThread itself has stopped.
        self.worker.finished.connect(self._worker_finished)
        self.worker.start()
        return True

    def _receive_output(self, line):
        self.append_log(line)
        if self._operation in ('qaqc', 'before_solver'):
            self._output.append(line)

    def on_process_finished(self, code):
        cancelled = self.worker.cancelled
        self.append_log(f'--- {"Cancelled" if cancelled else "Finished"} (exit code {code}) ---')
        self._success = not cancelled and code == 0
        if self._operation in ('qaqc', 'before_solver'):
            self._success = not cancelled and mesh_passed('\n'.join(self._output), code)
            self.status_label.setText('Case checks and checkMesh passed.' if self._success
                                      else 'Mesh check failed or was cancelled; solver was not started.')
        elif self._operation == 'solver':
            self.status_label.setText('Solver completed.' if self._success else 'Solver failed or was cancelled.')

    def _worker_finished(self):
        worker = self.worker
        self.worker = None
        operation = self._operation
        self._operation = None
        self.toggle_ui_state(False)
        worker.deleteLater()
        if operation == 'before_solver' and self._success:
            solver = self.combo_solver.currentText()
            ranks = self.spin_cores.value()
            command = ['mpirun', '-np', str(ranks), solver, '-parallel'] if ranks > 1 else [solver]
            self.execute_command(command, 'solver')

    def stop_simulation(self):
        if self.worker is not None:
            self.btn_stop.setEnabled(False)
            self.append_log('Stopping the running process and its process group...')
            self.worker.stop()

    def run_qaqc(self):
        if self._preflight(parallel=True):
            self.status_label.setText('Case file checks passed; checking the mesh...')
            self.execute_command(['checkMesh'], 'qaqc')

    def run_block_mesh(self):
        if not self.case_dir or not (Path(self.case_dir)/'system/blockMeshDict').exists():
            self.append_log('Select a case with system/blockMeshDict first.')
            return
        if (Path(self.case_dir)/'constant/polyMesh/faces').exists():
            self.append_log('A mesh already exists. Regenerate deliberately outside the GUI after preserving results.')
            return
        self.execute_command(['blockMesh'], 'mesh')

    def run_solver(self):
        # Always recheck files and mesh; a previous PASS can become stale.
        if self._preflight(parallel=True):
            self.status_label.setText('Checking the mesh before starting the solver...')
            self.execute_command(['checkMesh'], 'before_solver')

    def run_decompose(self):
        if self.worker is not None or not self._preflight():
            return
        ranks = self.spin_cores.value()
        if ranks <= 1:
            self.append_log('Select at least two MPI ranks before decomposing.')
            return
        if any(Path(self.case_dir).glob('processor[0-9]*')):
            self.append_log('Processor folders already exist. Select a fresh case or manage existing decomposition outside the GUI.')
            return
        divisions = [1,1,1]
        divisions[self.decompose_axis.currentIndex()] = ranks
        text = ('FoamFile { version 2.0; format ascii; class dictionary; object decomposeParDict; }\n'
                f'numberOfSubdomains {ranks};\nmethod simple;\n'
                f'simpleCoeffs {{ n ({" ".join(map(str,divisions))}); delta 0.001; }}\n')
        try:
            backup = write_case_files(self.case_dir, {'system/decomposeParDict': text})
            if backup:
                self.append_log('Previous dictionary backed up to: ' + str(backup))
        except (OSError, ValueError) as exc:
            self.append_log('Cannot write decomposition: ' + str(exc))
            return
        self.execute_command(['decomposePar'], 'decompose')

    def run_reconstruct(self):
        if not self.case_dir or not any(Path(self.case_dir).glob('processor[0-9]*')):
            self.append_log('No processor directories exist in the selected case.')
            return
        self.execute_command(['reconstructPar', '-latestTime'], 'reconstruct')

    def run_on_hpc(self, command):
        self.append_log('HPC submission is not implemented. Use an approved remote Linux/scheduler workflow.')
