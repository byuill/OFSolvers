#!/usr/bin/env python3
"""Native compressed-case and atmospheric-pressure regression; v1912 required."""
import gzip
import hashlib
import json
import os
from pathlib import Path
import re
import subprocess
import sys
import tempfile

import numpy as np

os.environ.setdefault('QT_QPA_PLATFORM','offscreen')
ROOT=Path(__file__).resolve().parents[2]
sys.path.insert(0,str(ROOT))
from PyQt6.QtWidgets import QApplication,QMessageBox
from boundary_tab import BoundaryTab
from case_qaqc import check_case,mesh_passed
from foam_io import read_foam_text,update_values,value
from scour_tracer_demo import generate_demo


def command(case,args,log):
    with (case/log).open('w') as stream:
        subprocess.run(args,cwd=case,stdout=stream,stderr=subprocess.STDOUT,check=True)
    return (case/log).read_text()


work=Path(tempfile.mkdtemp(prefix='ofsolvers-operations-'))
print('Validation artifacts:',work,flush=True)
case=work/'compressed-scour'
generate_demo(case,velocity=.1,end_time=.01,coarse=True)
path=case/'system/controlDict'
path.write_text(update_values(path.read_text(),{'writeInterval':.005,'maxDeltaT':.001}))
command(case,['sh','./Allmesh'],'log.initial-mesh')
for folder in (case/'0',case/'constant/polyMesh'):
    for path in folder.iterdir():
        if path.is_file():
            path.with_name(path.name+'.gz').write_bytes(gzip.compress(path.read_bytes(),mtime=0))
            path.unlink()
mesh={p:hashlib.sha256(p.read_bytes()).hexdigest() for p in (case/'constant/polyMesh').iterdir() if p.is_file()}
assert mesh_passed(command(case,['checkMesh'],'log.compressed-check'),0)
assert check_case(case,'interFoam').ok
print('PASS native gzip mesh and initial fields accepted by workbench and checkMesh',flush=True)

app=QApplication.instance() or QApplication([])
dialogs=[]
for kind in ('information','warning','critical'):
    setattr(QMessageBox,kind,lambda parent,title,text,*args:dialogs.append((title,text)))
tab=BoundaryTab(); tab.set_case_directory(str(case)); tab.set_solver_profile('interFoam')
tab.import_patches_from_boundary_file()
names=[tab.patch_list.item(i).text() for i in range(tab.patch_list.count())]
tab.patch_list.setCurrentRow(names.index('atmosphere'))
tab.bc_type_combo.setCurrentText('Atmosphere'); tab.atm_pressure.setValue(25)
internal={name:value(read_foam_text(case/'0'/name),'internalField') for name in ('U','p_rgh','alpha.water')}
tab.write_0_directory()
assert not tab.dirty_patches,dialogs
assert all(not (case/'0'/name).exists() and (case/'0'/(name+'.gz')).is_file() for name in internal)
assert all(value(read_foam_text(case/'0'/name),'internalField')==initial for name,initial in internal.items())
report=check_case(case,'interFoam'); assert report.ok,report.errors
print('PASS fluid boundary editing preserves compressed files and initialized internal fields',flush=True)

command(case,['interFoam'],'log.interFoam')
saved=max((p for p in case.iterdir() if p.is_dir() and (p/'p_rgh').is_file()),key=lambda p:float(p.name))
patch=value(value(read_foam_text(saved/'p_rgh'),'boundaryField')[1:-1],'atmosphere')[1:-1]
encoded=value(patch,'value')
if encoded.startswith('uniform'):
    pressure=np.array([float(encoded.split()[1])])
else:
    pressure=np.fromstring(re.search(r'List<scalar>\s+\d+\s*\((.*?)\)',encoded,re.S)[1],sep=' ')
# At z=1, p_rgh = p - rho*g*z. Air density is 1.2 kg/m3. Dynamic
# inlet correction can only lower the prescribed total pressure slightly.
assert pressure.size and np.isfinite(pressure).all()
assert np.max(np.abs(pressure-(25+1.2*9.81)))<.1,pressure
print('PASS native atmospheric pressure includes the hydrostatic head correction',flush=True)

result=subprocess.run(['sh','./Allmesh'],cwd=case,capture_output=True,text=True)
assert result.returncode!=0 and 'A mesh already exists' in result.stderr
assert all(hashlib.sha256(p.read_bytes()).hexdigest()==digest for p,digest in mesh.items())
print('PASS remeshing guard preserves a compressed mesh after a solver run',flush=True)
tab.close()
results=dict(compressed_mesh_files=len(mesh),saved_time=float(saved.name),
             atmosphere_p_rgh_min_pa=float(pressure.min()),atmosphere_p_rgh_max_pa=float(pressure.max()),
             expected_static_p_rgh_pa=25+1.2*9.81,mesh_unchanged=True)
(work/'results.json').write_text(json.dumps(results,indent=2)+'\n')
print(json.dumps(results,indent=2))
