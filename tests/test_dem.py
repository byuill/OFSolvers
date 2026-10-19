from dataclasses import replace
import json
import math
from pathlib import Path
import subprocess
import sys

import numpy as np
import pytest

from bedload_dem import BedloadConfig,particles,generate_bedload
from foam_io import value
from case_qaqc import check_case
from sediment_setup import case_patches


def test_four_mm_physical_bed_population():
    cfg=BedloadConfig();grains=np.array(particles(cfg))
    assert len(grains)==2925 and sum(grains[:,1]==1)==2340 and sum(grains[:,1]==2)==585
    assert np.all(grains[:,2]==.004) and np.all(grains[:,3]==2650)
    np.testing.assert_array_equal(grains[:,0],np.arange(1,len(grains)+1))
    points=grains[:,4:7]
    # Check representative rows against the complete periodic population.
    delta=points[::29,None,:]-points[None,:,:]
    for k,length in enumerate((cfg.length,cfg.width)):
        delta[:,:,k]-=np.round(delta[:,:,k]/length)*length
    distance=np.linalg.norm(delta,axis=-1);distance[distance<1e-14]=np.inf
    assert distance.min()>=cfg.diameter
    mass=len(grains)*cfg.density*math.pi*cfg.diameter**3/6
    assert .25<mass<.27


@pytest.mark.parametrize('change',[
    {'diameter':0},{'density':999},{'mobile_layers':1.5},{'height':.004},
    {'young_modulus':float('nan')},{'restitution':1.1},{'poisson_ratio':.5},
    {'rolling_stiffness':0},{'kernel_ratio':.5},{'volume_samples':1},{'turbulence':'unknown'}])
def test_invalid_dem_configuration_leaves_folder_empty(tmp_path,change):
    with pytest.raises(ValueError):generate_bedload(tmp_path,replace(BedloadConfig(),**change))
    assert not list(tmp_path.iterdir())


def test_case_generation_and_source_preservation(tmp_path):
    generate_bedload(tmp_path)
    manifest=json.loads((tmp_path/'bedload-dem.json').read_text())
    assert manifest['particles']==2925
    assert value((tmp_path/'system/controlDict').read_text(),'application')=='bedloadDemPimpleFoam'
    props=(tmp_path/'constant/demCouplingProperties').read_text()
    assert value(props,'dragModel')=='DiFelice' and value(props,'youngModulus')=='100000000'
    assert {p['type'] for p in case_patches(tmp_path)}=={'cyclic','wall'}
    report=check_case(tmp_path,'bedloadDemPimpleFoam',check_programs=False)
    assert any('CFD–DEM' in warning for warning in report.warnings)
    before=(tmp_path/'constant/DEM/particles.data').read_bytes()
    with pytest.raises(ValueError,match='empty'):generate_bedload(tmp_path)
    assert (tmp_path/'constant/DEM/particles.data').read_bytes()==before


def test_bad_explicit_population_rejected_before_writes(tmp_path):
    with pytest.raises(ValueError):
        generate_bedload(tmp_path,population=[(2,1,.004,2650,.01,.01,.01)])
    assert not list(tmp_path.iterdir())


def test_cli_accepts_fractional_material_and_filter_settings(tmp_path):
    script=Path(__file__).resolve().parents[1]/'bedload_dem.py'
    subprocess.run([sys.executable,str(script),str(tmp_path),'--density','2650.5',
        '--young-modulus','1e8','--kernel-ratio','3.5'],check=True,capture_output=True,text=True)
    settings=json.loads((tmp_path/'bedload-dem.json').read_text())['settings']
    assert settings['density']==2650.5 and settings['young_modulus']==1e8 and settings['kernel_ratio']==3.5
