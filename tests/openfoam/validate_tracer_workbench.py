#!/usr/bin/env python3
"""Real particle-preprocessing GUI -> mesh/setFields -> native seed preview.

Activate OpenFOAM v1912; run with QT_QPA_PLATFORM=offscreen or under Xvfb.
"""
import csv
import hashlib
import json
from pathlib import Path
import sys
import tempfile
import time
from unittest.mock import patch

ROOT=Path(__file__).resolve().parents[2]
sys.path.insert(0,str(ROOT))
from PyQt6.QtWidgets import QApplication,QMessageBox
from case_controller import CaseController
from case_model import CaseModel
from main_window import MainWindow
from scour_tracer_demo import generate_demo
from foam_io import value
from visualize_tracers import visualize

WORK=Path(tempfile.mkdtemp(prefix='tracer-workbench-validation-'))
print('Validation artifacts:',WORK,flush=True)
case=generate_demo(WORK/'scour',end_time=3,coarse=True)
app=QApplication([])
messages=[]
window=MainWindow(); controller=CaseController(CaseModel(),window)


def wait_execution():
    deadline=time.monotonic()+60
    while window.tab_execution.worker is not None and time.monotonic()<deadline:
        app.processEvents(); time.sleep(.01)
    app.processEvents()
    assert window.tab_execution.worker is None,'Command did not finish'
    assert window.tab_execution._success,window.tab_execution.console.toPlainText()[-5000:]


with patch.object(QMessageBox,'warning',side_effect=lambda p,t,m:messages.append((t,m))), \
     patch.object(QMessageBox,'information',side_effect=lambda p,t,m:messages.append((t,m))):
    window.set_case_directory(str(case))
    assert window.particle_workflow
    tab=window.tab_tracers
    tab.mesh_demo(); wait_execution()
    tab.preprocessing.refresh(); tab.preprocessing.use_carrier()
    tab.preprocessing.preset.setCurrentIndex(1); tab.preprocessing.apply_placement()
    tab.count.setValue(12)
    tab.collision_mode.setCurrentIndex(tab.collision_mode.findData('largeOnly'))
    bed=tab.boundary_editor.rows['bed']
    bed['action'].setCurrentIndex(bed['action'].findData('contact'))
    bed['e_own'].setChecked(True); bed['e'].setValue(.05)
    bed['mu_own'].setChecked(True); bed['mu'].setValue(.65)
    before_control=(case/'system/controlDict').read_bytes()
    window.end_time_spinbox.setValue(999)
    window.run_button.click()
    assert (case/'system/controlDict').read_bytes()==before_control
    assert value((case/'system/controlDict').read_text(),'application')=='interFoam'
    carrier_files=[p for p in (case/'0').iterdir() if p.is_file()]
    carrier_files += [p for p in (case/'constant/polyMesh').iterdir() if p.is_file()]
    digest={p:hashlib.sha256(p.read_bytes()).hexdigest() for p in carrier_files}
    tab.preview(); wait_execution()
    folder=case/'postProcessing/largeTracers/tracers01-preview'
    assert (folder/'complete').is_file()
    rows=list(csv.DictReader((folder/'tracks.csv').open()))
    assert len(rows)==12 and {float(r['time']) for r in rows}=={0}
    assert all(float(r['vx'])==float(r['vy'])==float(r['vz'])==0 for r in rows)
    native=(folder/'largeTracerProperties').read_text()
    assert value(native,'previewOnly')=='true'
    bed_rule=value(value(native,'particleBoundaries')[1:-1],'bed')[1:-1]
    assert float(value(bed_rule,'restitution'))==.05 and float(value(bed_rule,'friction'))==.65
    assert all(hashlib.sha256(p.read_bytes()).hexdigest()==h for p,h in digest.items())
    # Preview from initialized U/alpha is allowed; transport needs future fields.
    tab.run()
    assert window.tab_execution.worker is None and messages and 'cover' in messages[-1][1]
    results=visualize(folder,WORK/'preview')
    assert 'Initialization only.' in (results/'replay.html').read_text(encoding='utf-8')
    (WORK/'native-output.txt').write_text(window.tab_execution.console.toPlainText(),encoding='utf-8')
    window.tabs.setCurrentWidget(tab)
    window.resize(1400,1000); window.show(); app.processEvents()
    tab.pages.setCurrentIndex(0); app.processEvents(); window.grab().save(str(WORK/'preprocessing.png'))
    tab.pages.setCurrentIndex(2); app.processEvents(); window.grab().save(str(WORK/'particle-boundaries.png'))
    summary=dict(particles=len(rows),transport_steps=0,carrier_mesh_files_unchanged=len(digest),
                 carrier_control_unchanged=True,preview_complete=True,full_run_blocked_without_future_fields=True,
                 restitution=.05,friction=.65)
    (WORK/'results.json').write_text(json.dumps(summary,indent=2)+'\n')
    print('PASS specialized GUI setup, native preview, particle boundaries and carrier preservation',flush=True)
    print(json.dumps(summary,indent=2),flush=True)
window.close()
