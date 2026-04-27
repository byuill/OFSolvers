import os
import pytest
from openfoam_dict_parser import OpenFoamDictParser

def test_openfoam_dict_parser_create_template(tmp_path):
    target_path = tmp_path / "U"
    parser = OpenFoamDictParser(
        str(target_path),
        default_class="volVectorField",
        object_name="U",
        default_dimensions="[0 1 -1 0 0 0 0]",
        default_internal_field="uniform (0 0 0)"
    )

    boundary_field = {
        "inlet": {
            "type": "fixedValue",
            "value": "uniform (1 0 0)"
        },
        "outlet": {
            "type": "zeroGradient"
        }
    }

    parser.write(boundary_field)

    with open(target_path, "r", encoding="utf-8") as f:
        content = f.read()

    assert "class       volVectorField;" in content
    assert "object      U;" in content
    assert "dimensions      [0 1 -1 0 0 0 0];" in content
    assert "internalField   uniform (0 0 0);" in content

    # Check boundary field
    assert "boundaryField" in content
    assert "inlet" in content
    assert "type            fixedValue;" in content
    assert "value           uniform (1 0 0);" in content
    assert "outlet" in content
    assert "type            zeroGradient;" in content

def test_openfoam_dict_parser_update_existing(tmp_path):
    target_path = tmp_path / "p"

    initial_content = """FoamFile
{
    version     2.0;
    format      ascii;
    class       volScalarField;
    object      p;
}

dimensions      [0 2 -2 0 0 0 0];
internalField   uniform 0;

boundaryField
{
    inlet
    {
        type            zeroGradient;
    }
}
"""
    with open(target_path, "w", encoding="utf-8") as f:
        f.write(initial_content)

    parser = OpenFoamDictParser(
        str(target_path),
        default_class="volScalarField",
        object_name="p",
    )

    boundary_field = {
        "inlet": {
            "type": "fixedValue",
            "value": "uniform 10"
        },
        "outlet": {
            "type": "zeroGradient"
        }
    }

    parser.write(boundary_field)

    with open(target_path, "r", encoding="utf-8") as f:
        content = f.read()

    # Original headers should be intact
    assert "class       volScalarField;" in content
    assert "object      p;" in content

    # Boundary field should be updated
    assert "boundaryField" in content
    assert "inlet" in content
    assert "type            fixedValue;" in content
    assert "value           uniform 10;" in content
    assert "outlet" in content
    assert "type            zeroGradient;" in content

    # We should have correctly replaced the block and not duplicated
    assert content.count("boundaryField") == 1
