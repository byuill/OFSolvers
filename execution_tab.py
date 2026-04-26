import os
import subprocess
from PyQt6.QtWidgets import (
    QWidget,
    QVBoxLayout,
    QHBoxLayout,
    QPushButton,
    QLabel,
    QRadioButton,
    QSpinBox,
    QComboBox,
    QTextEdit,
    QGroupBox,
    QFormLayout,
    QMessageBox,
)
from PyQt6.QtCore import Qt, QThread, pyqtSignal


class OpenFOAMWorker(QThread):
    """
    Worker thread to execute OpenFOAM terminal commands without blocking the main GUI.
    Captures stdout/stderr in real-time and emits it back to the main thread.
    """

    output_signal = pyqtSignal(str)
    finished_signal = pyqtSignal(int)

    def __init__(self, command, cwd=None, parent=None):
        super().__init__(parent)
        self.command = command
        self.cwd = cwd if cwd else os.getcwd()
        self.process = None
        self._is_running = True

    def run(self):
        self.output_signal.emit(f"--- Executing: {self.command} ---")
        try:
            # Run the command, merging stderr into stdout, line-buffered
            self.process = subprocess.Popen(
                self.command,
                stdout=subprocess.PIPE,
                stderr=subprocess.STDOUT,
                shell=True,
                cwd=self.cwd,
                text=True,
                bufsize=1,
            )

            # Read output line by line in real-time
            for line in iter(self.process.stdout.readline, ""):
                if not self._is_running:
                    break
                if line:
                    self.output_signal.emit(line.strip())

            self.process.stdout.close()
            return_code = self.process.wait()

            if self._is_running:
                self.finished_signal.emit(return_code)

        except Exception as e:
            self.output_signal.emit(f"ERROR: Failed to execute command.\n{str(e)}")
            self.finished_signal.emit(-1)

    def stop(self):
        """Terminate the running process."""
        self._is_running = False
        if self.process:
            self.process.terminate()
            self.output_signal.emit("\n--- Process Terminated by User ---")
            self.finished_signal.emit(-9)


class ExecutionTab(QWidget):
    def __init__(self, parent=None):
        super().__init__(parent)
        self.worker = None
        self.setup_ui()

    def setup_ui(self):
        main_layout = QVBoxLayout(self)

        # 1. Environment Selection
        env_group = QGroupBox("1. Environment Selection")
        env_layout = QHBoxLayout(env_group)
        self.rb_local = QRadioButton("Run Locally")
        self.rb_local.setChecked(True)
        self.rb_hpc = QRadioButton("Run on ERDC Carpenter (HPC)")
        env_layout.addWidget(self.rb_local)
        env_layout.addWidget(self.rb_hpc)
        env_layout.addStretch()
        main_layout.addWidget(env_group)

        # 2. Pre-Flight QAQC
        qaqc_group = QGroupBox("2. Pre-Flight QAQC")
        qaqc_layout = QVBoxLayout(qaqc_group)
        self.btn_qaqc = QPushButton("Run Simulation QAQC")
        self.btn_qaqc.clicked.connect(self.run_qaqc)
        qaqc_layout.addWidget(self.btn_qaqc)
        main_layout.addWidget(qaqc_group)

        # 3. Parallel Setup
        parallel_group = QGroupBox("3. Parallel Setup")
        parallel_layout = QFormLayout(parallel_group)
        self.spin_cores = QSpinBox()
        self.spin_cores.setRange(1, 256)
        self.spin_cores.setValue(4)
        self.btn_decompose = QPushButton("Decompose Mesh (decomposePar)")
        self.btn_decompose.clicked.connect(self.run_decompose)
        parallel_layout.addRow("Number of Subdomains (Cores):", self.spin_cores)
        parallel_layout.addRow(self.btn_decompose)
        main_layout.addWidget(parallel_group)

        # 4. Execution & Diagnostics
        exec_group = QGroupBox("4. Execution & Diagnostics")
        exec_layout = QVBoxLayout(exec_group)

        solver_layout = QHBoxLayout()
        solver_layout.addWidget(QLabel("Target Solver:"))
        self.combo_solver = QComboBox()
        self.combo_solver.addItems(["interFoam", "sedFoam", "simpleFoam", "pimpleFoam"])
        solver_layout.addWidget(self.combo_solver)
        solver_layout.addStretch()
        exec_layout.addLayout(solver_layout)

        self.btn_run = QPushButton("RUN SOLVER")
        self.btn_run.setStyleSheet(
            "font-size: 16px; font-weight: bold; background-color: #4CAF50; color: white; padding: 10px;"
        )
        self.btn_run.clicked.connect(self.run_solver)
        exec_layout.addWidget(self.btn_run)

        self.console = QTextEdit()
        self.console.setReadOnly(True)
        self.console.setStyleSheet(
            "background-color: #1e1e1e; color: #00FF00; font-family: Consolas, monospace;"
        )
        exec_layout.addWidget(self.console)

        self.btn_stop = QPushButton("Stop/Kill Simulation")
        self.btn_stop.setStyleSheet(
            "background-color: #f44336; color: white; font-weight: bold;"
        )
        self.btn_stop.setEnabled(False)
        self.btn_stop.clicked.connect(self.stop_simulation)
        exec_layout.addWidget(self.btn_stop)

        main_layout.addWidget(exec_group)

        # 5. Post-Processing
        post_group = QGroupBox("5. Post-Processing")
        post_layout = QVBoxLayout(post_group)
        self.btn_reconstruct = QPushButton("Reconstruct Mesh (reconstructPar)")
        self.btn_reconstruct.clicked.connect(self.run_reconstruct)
        post_layout.addWidget(self.btn_reconstruct)
        main_layout.addWidget(post_group)

    def append_log(self, text):
        """Safely appends text to the QTextEdit console from the worker thread."""
        self.console.append(text)
        # Auto-scroll to bottom
        scrollbar = self.console.verticalScrollBar()
        scrollbar.setValue(scrollbar.maximum())

    def toggle_ui_state(self, running):
        """Enables/Disables buttons during execution to prevent concurrent commands."""
        self.btn_qaqc.setEnabled(not running)
        self.btn_decompose.setEnabled(not running)
        self.btn_run.setEnabled(not running)
        self.btn_reconstruct.setEnabled(not running)
        self.btn_stop.setEnabled(running)

    def execute_command(self, command):
        """Routes the command to the local QThread or HPC placeholder based on radio selection."""
        if self.rb_hpc.isChecked():
            self.run_on_hpc(command)
            return

        self.toggle_ui_state(True)
        self.worker = OpenFOAMWorker(command)
        self.worker.output_signal.connect(self.append_log)
        self.worker.finished_signal.connect(self.on_process_finished)
        self.worker.start()

    def on_process_finished(self, returncode):
        self.append_log(f"--- Process Finished (Exit Code: {returncode}) ---\n")
        self.toggle_ui_state(False)
        self.worker = None

    def stop_simulation(self):
        if self.worker:
            self.worker.stop()

    def run_qaqc(self):
        missing = [
            d
            for d in ["0", "constant", "system"]
            if not os.path.exists(os.path.join(os.getcwd(), d))
        ]
        if missing:
            self.append_log(
                f"QAQC FAILED: Missing essential OpenFOAM directories: {', '.join(missing)}"
            )
        else:
            self.append_log(
                "QAQC PASS: '0', 'constant', and 'system' directories exist."
            )
            # 2. Execute checkMesh
            self.execute_command("checkMesh")

    def run_decompose(self):
        self.execute_command("decomposePar")

    def run_solver(self):
        solver = self.combo_solver.currentText()
        cores = self.spin_cores.value()
        if cores > 1:
            # Using mpirun for parallel execution
            self.execute_command(f"mpirun -np {cores} {solver} -parallel")
        else:
            # Standard sequential run
            self.execute_command(solver)

    def run_reconstruct(self):
        self.execute_command("reconstructPar")

    def run_on_hpc(self, command):
        """
        Placeholder for HPC submission via ERDC Carpenter.
        Future Implementation:
        1. Use 'paramiko' library to establish an SSH connection to the HPC node.
        2. Use SCPClient to push the local OpenFOAM case directory to the HPC scratch space.
        3. Dynamically generate a PBS Pro submission script (e.g., #PBS -l select=...).
        4. Execute 'qsub' via SSH.
        5. Tail/poll the resulting log file over SSH to stream outputs back to this GUI.
        """
        self.append_log("--- HPC Execution Mode Selected ---")
        self.append_log(f"[HPC Placeholder] Preparing to send command: {command}")
        self.append_log(
            "[HPC Placeholder] paramiko SSH connection to ERDC Carpenter pending..."
        )
        self.append_log(
            "[HPC Placeholder] Case transfer (SCP) and PBS script submission omitted in placeholder."
        )
