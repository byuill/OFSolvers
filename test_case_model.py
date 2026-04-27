import pytest
from case_model import CaseModel

def test_case_model_initialization():
    model = CaseModel()
    assert model.solver == "interFoam"
    assert model.dim_x == 10.0
    assert model.dim_y == 10.0
    assert model.dim_z == 10.0
    assert model.cells_x == 100
    assert model.cells_y == 100
    assert model.cells_z == 100
    assert model.start_time == 0.0
    assert model.end_time == 100.0
    assert model.delta_t == 0.001
    assert model.boundaries == {
        "Min X": "Wall",
        "Max X": "Wall",
        "Min Y": "Wall",
        "Max Y": "Wall",
        "Min Z": "Wall",
        "Max Z": "Atmosphere/Open",
    }

def test_case_model_update_conceptual_data():
    model = CaseModel()

    # Mock slot to verify signal emission
    signal_emitted = False
    def on_data_changed():
        nonlocal signal_emitted
        signal_emitted = True

    model.data_changed.connect(on_data_changed)

    boundaries = {
        "Min X": "Inlet",
        "Max X": "Outlet",
        "Min Y": "Wall",
        "Max Y": "Wall",
        "Min Z": "Wall",
        "Max Z": "Atmosphere/Open",
    }

    model.update_conceptual_data(
        "sedFoam",
        20.0, 30.0, 40.0,
        200, 300, 400,
        boundaries,
        10.0, 200.0, 0.01
    )

    assert model.solver == "sedFoam"
    assert model.dim_x == 20.0
    assert model.dim_y == 30.0
    assert model.dim_z == 40.0
    assert model.cells_x == 200
    assert model.cells_y == 300
    assert model.cells_z == 400
    assert model.boundaries == boundaries
    assert model.start_time == 10.0
    assert model.end_time == 200.0
    assert model.delta_t == 0.01
    assert signal_emitted

def test_case_model_get_case_dict():
    model = CaseModel()

    case_dict = model.get_case_dict()
    assert case_dict["solver"] == "interFoam"
    assert case_dict["domain"] == (10.0, 10.0, 10.0)
    assert case_dict["resolution"] == (100, 100, 100)
    assert case_dict["boundaries"] == {
        "Min X": "Wall",
        "Max X": "Wall",
        "Min Y": "Wall",
        "Max Y": "Wall",
        "Min Z": "Wall",
        "Max Z": "Atmosphere/Open",
    }
    assert case_dict["startTime"] == 0.0
    assert case_dict["endTime"] == 100.0
    assert case_dict["deltaT"] == 0.001
