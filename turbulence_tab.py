from PyQt6.QtWidgets import (QWidget, QVBoxLayout, QGroupBox, QLineEdit, QComboBox,
                             QStackedWidget, QFormLayout, QDoubleSpinBox, QPushButton, QLabel, QMessageBox)
from PyQt6.QtCore import pyqtSlot

class TurbulenceTab(QWidget):
    def __init__(self, parent=None):
        super().__init__(parent)
        self.setup_ui()

    def setup_ui(self):
        main_layout = QVBoxLayout(self)

        # 1. Regime Tracker
        regime_group = QGroupBox("Regime Tracker")
        regime_layout = QVBoxLayout()
        self.regime_edit = QLineEdit()
        self.regime_edit.setReadOnly(True)
        self.regime_edit.setPlaceholderText("Current Flow Regime")
        regime_layout.addWidget(QLabel("Current Flow Regime:"))
        regime_layout.addWidget(self.regime_edit)
        regime_group.setLayout(regime_layout)
        main_layout.addWidget(regime_group)

        # 2. Model Selection
        model_group = QGroupBox("Model Selection")
        model_layout = QVBoxLayout()
        self.model_combo = QComboBox()
        self.model_combo.currentTextChanged.connect(self.on_model_changed)
        model_layout.addWidget(QLabel("Turbulence Model:"))
        model_layout.addWidget(self.model_combo)
        model_group.setLayout(model_layout)
        main_layout.addWidget(model_group)

        # 3. Model Parameterization
        param_group = QGroupBox("Model Parameterization")
        param_layout = QVBoxLayout()
        self.stacked_widget = QStackedWidget()

        # Page: None/Laminar
        self.page_none = QWidget()
        none_layout = QVBoxLayout(self.page_none)
        none_layout.addWidget(QLabel("None required for laminar flow."))
        self.stacked_widget.addWidget(self.page_none)

        # Page: Default
        self.page_default = QWidget()
        default_layout = QVBoxLayout(self.page_default)
        default_layout.addWidget(QLabel("Using OpenFOAM default internal coefficients."))
        self.stacked_widget.addWidget(self.page_default)

        # Page: kEpsilon
        self.page_kEpsilon = QWidget()
        kEps_layout = QFormLayout(self.page_kEpsilon)
        self.spin_cmu = QDoubleSpinBox(); self.spin_cmu.setValue(0.09); self.spin_cmu.setDecimals(3)
        self.spin_c1 = QDoubleSpinBox(); self.spin_c1.setValue(1.44); self.spin_c1.setDecimals(3)
        self.spin_c2 = QDoubleSpinBox(); self.spin_c2.setValue(1.92); self.spin_c2.setDecimals(3)
        self.spin_sigmaEps = QDoubleSpinBox(); self.spin_sigmaEps.setValue(1.3); self.spin_sigmaEps.setDecimals(3)
        kEps_layout.addRow("Cmu:", self.spin_cmu)
        kEps_layout.addRow("C1:", self.spin_c1)
        kEps_layout.addRow("C2:", self.spin_c2)
        kEps_layout.addRow("sigmaEps:", self.spin_sigmaEps)
        self.stacked_widget.addWidget(self.page_kEpsilon)

        # Page: Smagorinsky
        self.page_smagorinsky = QWidget()
        smag_layout = QFormLayout(self.page_smagorinsky)
        self.spin_ck = QDoubleSpinBox(); self.spin_ck.setValue(0.094); self.spin_ck.setDecimals(3)
        self.spin_ce = QDoubleSpinBox(); self.spin_ce.setValue(1.048); self.spin_ce.setDecimals(3)
        smag_layout.addRow("Ck:", self.spin_ck)
        smag_layout.addRow("Ce:", self.spin_ce)
        self.stacked_widget.addWidget(self.page_smagorinsky)

        param_layout.addWidget(self.stacked_widget)
        param_group.setLayout(param_layout)
        main_layout.addWidget(param_group)

        # 4. File Generation
        self.btn_write = QPushButton("Write turbulenceProperties")
        self.btn_write.clicked.connect(self.write_turbulence_properties)
        main_layout.addWidget(self.btn_write)

        main_layout.addStretch()

    @pyqtSlot(str)
    def update_regime(self, regime_string):
        """
        Updates the tab based on the selected flow regime.
        regime_string: 'laminar', 'RAS', or 'LES'
        """
        self.regime_edit.setText(regime_string)
        self.model_combo.clear()

        if regime_string == "laminar":
            self.model_combo.setEnabled(False)
            self.model_combo.addItem("None")
        elif regime_string == "RAS":
            self.model_combo.setEnabled(True)
            self.model_combo.addItems(["kEpsilon", "kOmegaSST", "RNGkEpsilon", "realizableKE", "BuoyantkEpsilon"])
        elif regime_string == "LES":
            self.model_combo.setEnabled(True)
            self.model_combo.addItems(["Smagorinsky", "kEqn", "WALE", "DeardorffDiffSGS", "dynamicSmagorinsky"])

    def on_model_changed(self, model_name):
        """Changes the stacked widget page based on the selected model."""
        if model_name == "None":
            self.stacked_widget.setCurrentWidget(self.page_none)
        elif model_name == "kEpsilon":
            self.stacked_widget.setCurrentWidget(self.page_kEpsilon)
        elif model_name == "Smagorinsky":
            self.stacked_widget.setCurrentWidget(self.page_smagorinsky)
        else:
            self.stacked_widget.setCurrentWidget(self.page_default)

    def write_turbulence_properties(self):
        """
        Placeholder function to demonstrate generating the turbulenceProperties dictionary.
        This would use the existing dictionary parser to format the syntax.
        """
        regime = self.regime_edit.text()
        model = self.model_combo.currentText()

        # Example of how this might look
        output = f"simulationType {regime};\n\n"

        if regime in ["RAS", "LES"]:
            output += f"{regime}\n{{\n"
            if regime == "RAS":
                output += f"    RASModel {model};\n"
                output += "    turbulence on;\n"
                output += "    printCoeffs on;\n"

                if model == "kEpsilon":
                    output += f"    kEpsilonCoeffs\n    {{\n"
                    output += f"        Cmu {self.spin_cmu.value()};\n"
                    output += f"        C1 {self.spin_c1.value()};\n"
                    output += f"        C2 {self.spin_c2.value()};\n"
                    output += f"        sigmaEps {self.spin_sigmaEps.value()};\n"
                    output += "    }\n"
            elif regime == "LES":
                output += f"    LESModel {model};\n"
                output += "    turbulence on;\n"
                output += "    printCoeffs on;\n"

                if model == "Smagorinsky":
                    output += f"    SmagorinskyCoeffs\n    {{\n"
                    output += f"        Ck {self.spin_ck.value()};\n"
                    output += f"        Ce {self.spin_ce.value()};\n"
                    output += "    }\n"
            output += "}\n"

        print("--- Generated turbulenceProperties ---")
        print(output)
        QMessageBox.information(self, "Success", "turbulenceProperties dictionary generated! (See console)")
