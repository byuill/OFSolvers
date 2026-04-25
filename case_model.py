from PyQt6.QtCore import QObject, pyqtSignal

class CaseModel(QObject):
    # Signals to notify the view if the model changes externally
    data_changed = pyqtSignal()

    def __init__(self):
        super().__init__()
        # OpenFOAM controlDict and case parameters
        self.solver = "interFoam"
        
        # Domain Extent (meters)
        self.dim_x = 10.0
        self.dim_y = 10.0
        self.dim_z = 10.0
        
        # Domain Resolution (number of cells)
        self.cells_x = 100
        self.cells_y = 100
        self.cells_z = 100
        
        # Time configuration
        self.start_time = 0.0
        self.end_time = 100.0
        self.delta_t = 0.001
        
        # Boundary configuration
        self.boundaries = {
            "Min X": "Wall", "Max X": "Wall",
            "Min Y": "Wall", "Max Y": "Wall",
            "Min Z": "Wall", "Max Z": "Atmosphere/Open"
        }
        
    def update_conceptual_data(self, solver, dim_x, dim_y, dim_z, cells_x, cells_y, cells_z, boundaries, start_time, end_time, delta_t):
        self.solver = solver
        self.dim_x, self.dim_y, self.dim_z = dim_x, dim_y, dim_z
        self.cells_x, self.cells_y, self.cells_z = cells_x, cells_y, cells_z
        self.boundaries = boundaries
        self.start_time = start_time
        self.end_time = end_time
        self.delta_t = delta_t
        self.data_changed.emit()
        
    def get_case_dict(self):
        return {
            "solver": self.solver,
            "domain": (self.dim_x, self.dim_y, self.dim_z),
            "resolution": (self.cells_x, self.cells_y, self.cells_z),
            "boundaries": self.boundaries,
            "startTime": self.start_time,
            "endTime": self.end_time,
            "deltaT": self.delta_t
        }