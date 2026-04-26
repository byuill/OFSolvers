from PyQt6.QtWidgets import QMessageBox

class CaseController:
    def __init__(self, model, view):
        self.model = model
        self.view = view

        # Connect view signals to controller slots
        self.view.run_button.clicked.connect(self.run_simulation)

        # Connect model signals to view slots (to keep UI synced if model changes)
        self.model.data_changed.connect(self.sync_view_to_model)

        # Connect signals for dynamic boundary advice updates
        self.view.solver_combo.currentTextChanged.connect(self.update_advice)
        for combo in self.view.boundary_combos.values():
            combo.currentTextChanged.connect(self.update_advice)

        # Initialize the view with default model data
        self.sync_view_to_model()
        self.update_advice()

    def sync_view_to_model(self):
        self.view.solver_combo.setCurrentText(self.model.solver)

        self.view.dim_x_spinbox.setValue(self.model.dim_x)
        self.view.dim_y_spinbox.setValue(self.model.dim_y)
        self.view.dim_z_spinbox.setValue(self.model.dim_z)

        self.view.cells_x_spinbox.setValue(self.model.cells_x)
        self.view.cells_y_spinbox.setValue(self.model.cells_y)
        self.view.cells_z_spinbox.setValue(self.model.cells_z)

        for key, combo in self.view.boundary_combos.items():
            combo.setCurrentText(self.model.boundaries.get(key, "Wall"))

        self.view.start_time_spinbox.setValue(self.model.start_time)
        self.view.end_time_spinbox.setValue(self.model.end_time)
        self.view.delta_t_spinbox.setValue(self.model.delta_t)

        # Link conceptual model with mesh generation tab
        self.view.tab_mesh.update_from_conceptual_model(
            self.model.dim_x, self.model.dim_y, self.model.dim_z,
            self.model.cells_x, self.model.cells_y, self.model.cells_z
        )

    def update_advice(self):
        solver = self.view.solver_combo.currentText()
        advice = []

        if solver in ["interFoam", "multiphaseEulerFoam"]:
            advice.append("💡 Open Channel / Multiphase: Set the top boundary (Max Z) to 'Atmosphere/Open'. Bottom (Min Z) is typically a 'Wall'.")
        elif solver == "buoyantBoussinesqPimpleFoam":
            advice.append("💡 Atmospheric Flow: Use 'Atmosphere/Open' or 'Inlet'/'Outlet' depending on wind direction. Ground is usually a 'Wall'.")

        has_inlet = any(c.currentText() == "Inlet" for c in self.view.boundary_combos.values())
        has_outlet = any(c.currentText() == "Outlet" for c in self.view.boundary_combos.values())

        if has_inlet and not has_outlet:
            advice.append("⚠️ Warning: You have an Inlet but no Outlet. Consider defining an Outlet to satisfy mass conservation.")

        if not advice:
            advice.append("💡 Advice: Define 'Inlet' and 'Outlet' for flow direction. Use 'Wall' for solid boundaries.")

        self.view.advice_label.setText("\n".join(advice))

    def run_simulation(self):
        # 1. Pull data from the view to update the model
        solver = self.view.solver_combo.currentText()
        dim_x, dim_y, dim_z = self.view.dim_x_spinbox.value(), self.view.dim_y_spinbox.value(), self.view.dim_z_spinbox.value()
        cells_x, cells_y, cells_z = self.view.cells_x_spinbox.value(), self.view.cells_y_spinbox.value(), self.view.cells_z_spinbox.value()

        boundaries = {key: combo.currentText() for key, combo in self.view.boundary_combos.items()}

        start_time = self.view.start_time_spinbox.value()
        end_time = self.view.end_time_spinbox.value()
        delta_t = self.view.delta_t_spinbox.value()

        self.model.update_conceptual_data(solver, dim_x, dim_y, dim_z, cells_x, cells_y, cells_z, boundaries, start_time, end_time, delta_t)

        # 2. Get case data and trigger OpenFOAM scripts
        case_data = self.model.get_case_dict()
        print("--- Conceptual Model Saved ---")
        print(f"Solver: {case_data['solver']}")
        print(f"Domain Extent (m): {case_data['domain']}")
        print(f"Domain Resolution (cells): {case_data['resolution']}")
        print(f"Boundaries: {case_data['boundaries']}")
        print(f"Simulation Period: {case_data['startTime']} to {case_data['endTime']} (dt={case_data['deltaT']})")

        QMessageBox.information(self.view, "Success", "Conceptual Model Saved Successfully!")
        # Future: Insert blockMesh generation and OpenFOAM execution logic here