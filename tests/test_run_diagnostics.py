import csv
import json
from pathlib import Path
import sys

import pytest

from run_diagnostics import RunDiagnostics, Limits, parse_log, export_diagnostics_csv


EXAMPLE = Path(__file__).parent/'fixtures/diagnostics.log'


def report_from(text):
    report = RunDiagnostics()
    for line in text.splitlines():
        report.feed(line)
    return report


def test_v1912_step_alignment_and_sediment_units():
    report = parse_log(EXAMPLE)
    assert [step.time for step in report.steps] == [.01, .02, .02001]
    assert [step.co_max for step in report.steps] == [.2, .4, 3.2]
    assert [step.delta_t for step in report.steps] == [.01, .01, .00001]
    assert report.steps[1].wall_step_s == 2
    assert report.steps[2].wall_step_s == 5
    assert report.steps[0].external_out_kg_s == .001
    assert report.steps[0].budget_residual_kg == 2e-14
    assert report.steps[0].continuity_global == -2e-12
    assert len(report.residuals) == 9
    assert report.completed and report.exit_code == 0
    titles = [item.title for item in report.findings()]
    assert any('Courant number exceeds' in title for title in titles)
    assert any('100×' in title for title in titles)
    assert 'Negative suspended concentration' in titles
    assert 'Sediment step-budget residual exceeds tolerance' in titles
    assert 'Latest linear residuals exceed screening target' in titles


def test_thresholds_are_explicit_and_do_not_edit_the_log():
    original = EXAMPLE.read_bytes()
    report = parse_log(EXAMPLE)
    findings = report.findings(Limits(max_co=4, residual=.1, budget_kg=.01, negative_c=.02))
    assert not any('exceeds' in item.title or 'Negative suspended' in item.title for item in findings)
    assert EXAMPLE.read_bytes() == original


def test_pimple_residual_history_is_not_replaced_by_last_outer_iteration():
    report = report_from('''Time = 1
PCG: Solving for p, Initial residual = 0.3, Final residual = 0.01, No Iterations 100
PCG: Solving for p, Initial residual = 0.01, Final residual = 1e-10, No Iterations 8
End''')
    assert len(report.residuals) == 2
    assert not any('Latest linear residuals exceed' in item.title for item in report.findings())


@pytest.mark.parametrize('new_time', ['0.5', '1', '2'])
def test_restart_segments_do_not_join_clock_or_histories(new_time):
    report = report_from(f'''Time = 1
ExecutionTime = 10 s ClockTime = 12 s
End
Starting time loop
Time = {new_time}
ExecutionTime = 1 s ClockTime = 2 s''')
    assert [step.segment for step in report.steps] == [0, 1]
    assert report.steps[-1].wall_step_s is None
    assert not report.completed


def test_rank_prefixed_metrics_are_not_double_counted_and_errors_keep_context():
    report = report_from('''[0] Time = 0.1
[1] Time = 0.1
[0] PCG: Solving for p, Initial residual = .1, Final residual = 1E-08, No Iterations 4
[1] PCG: Solving for p, Initial residual = .2, Final residual = 1E-08, No Iterations 4
[1] --> FOAM FATAL IO ERROR:
[1] Unknown patch riverBed
[1] file: 0/U/boundaryField
--- Finished (exit code 1) ---''')
    assert len(report.steps) == 1 and len(report.residuals) == 1
    assert any('0/U/boundaryField' in line for line in report.events)
    assert any(item.level == 'Error' for item in report.findings())


@pytest.mark.parametrize('number', ['nan', 'NaN', '-inf', '+inf'])
def test_nonfinite_diagnostics_are_errors_not_dropped_as_good_values(number):
    report = report_from(f'Time = 1\nPCG: Solving for p, Initial residual = {number}, Final residual = 0, No Iterations 0')
    assert any(item.title == 'Nonfinite numeric diagnostic' for item in report.findings())


def test_unlogged_metrics_remain_missing_and_parcel_mass_does_not_imply_closure():
    report = report_from('Time = 1\nSand inventory: mobile=0.02 deposited=0.03 kg\nEnd')
    assert report.values('co_max') == [] and report.values('budget_residual_kg') == []
    assert report.steps[0].mobile_kg == .02
    assert any('closure is not inferred' in item.title for item in report.findings())
    assert any('Sediment Courant number was not logged' in item.title for item in report.findings())


def test_unfinished_next_step_courant_is_not_hidden_after_a_crash():
    report = report_from('Time = 1\nExecutionTime = 1 s ClockTime = 1 s\nCourant Number mean: 4 max: 40\nFloating point exception')
    assert max(report.values('co_max')) == 40
    assert report.steps[0].co_max is None
    assert any('Courant number exceeds' in item.title for item in report.findings())


def test_interface_courant_does_not_overwrite_water_courant():
    report = report_from('''Courant Number mean: 0.5 max: 1.2
Interface Courant Number mean: 0.01 max: 0.1
Sediment Courant Number mean: 0.2 max: 0.4
Time = 1''')
    assert report.steps[0].co_max == 1.2
    assert report.steps[0].interface_co_max == .1
    assert report.steps[0].sediment_co_max == .4


def test_csv_keeps_all_linear_solves_and_step_budget_separate(tmp_path):
    report = parse_log(EXAMPLE)
    path = tmp_path/'diagnostics.csv'
    export_diagnostics_csv(path, report)
    with path.open() as stream:
        records = list(csv.DictReader(stream))
    assert len(records) == 12
    assert [row['record'] for row in records].count('step') == 3
    assert [row['record'] for row in records].count('linearResidual') == 9
    assert records[0]['external_out_kg_s'] == '0.001'
    assert records[0]['budget_residual_kg'] == '2e-14'


def test_cancellation_is_distinct_from_success_even_if_process_exits_zero():
    report = report_from('Time = 1\nEnd\n--- Cancelled (exit code 0) ---')
    assert report.cancelled
    assert any(item.title == 'Command was canceled' for item in report.findings())


def test_missing_executable_error_is_available_in_debugging_context():
    report = report_from('ERROR: Failed to execute command: No such file or directory: missingSolver\n--- Finished (exit code -1) ---')
    assert any('missingSolver' in line for line in report.events)
    assert any(item.level == 'Error' for item in report.findings())


def test_worker_preserves_full_output_and_start_settings(qapp, tmp_path):
    from execution_tab import ExecutionTab
    from sediment_setup import create_starter
    from tests.test_gui_robustness import wait
    create_starter(tmp_path, 'sedimentPimpleFoam')
    original = (tmp_path/'system/controlDict').read_text()
    tab = ExecutionTab()
    tab.set_case_directory(str(tmp_path))
    assert tab.execute_command([sys.executable, '-c', 'print("Time = 0.1"); print("End"); print("<b>plain log</b>")'], 'solver')
    wait(qapp, lambda: tab.worker is None)
    assert tab.log_path.is_file()
    text = tab.log_path.read_text()
    assert '<b>plain log</b>' in text and 'Finished (exit code 0)' in text
    settings = json.loads(tab.log_path.with_name('settings.json').read_text())
    (tmp_path/'system/controlDict').write_text('case changed after run')
    assert settings['files']['system/controlDict'] == original
    assert parse_log(tab.log_path).steps[0].time == .1


def test_diagnostics_widget_load_compare_and_live_controls(qapp, tmp_path):
    from diagnostics_tab import DiagnosticsTab
    from tests.test_gui_robustness import wait
    tab = DiagnosticsTab()
    tab.case_dir = str(tmp_path)
    tab.load_log(EXAMPLE)
    wait(qapp, lambda: tab.worker is None)
    assert len(tab.report.steps) == 3
    assert tab.findings_table.rowCount() >= 5
    assert tab.field_combo.currentText() == 'p'
    assert 'No run settings snapshot' in tab.settings.toPlainText()
    tab.load_log(EXAMPLE, 'baseline')
    wait(qapp, lambda: tab.worker is None)
    assert tab.baseline is not None and len(tab.figure.axes) == 4
    tab.clear_baseline()
    assert tab.baseline is None
    tab.follow_live.setChecked(False)
    original = tab.report
    latest = RunDiagnostics(source='latest command log')
    tab.receive_live(latest)
    assert tab.report is original
    tab.follow_live.setChecked(True)
    assert tab.report is latest
    tab.timer.stop()
    tab.close()


def test_workbench_keeps_execution_and_diagnostics_available_while_busy(qapp):
    from main_window import MainWindow
    window = MainWindow()
    window.set_busy(True)
    assert window.tabs.isTabEnabled(window.tabs.indexOf(window.tab_execution))
    assert window.tabs.isTabEnabled(window.tabs.indexOf(window.tab_diagnostics))
    assert not window.tabs.isTabEnabled(window.tabs.indexOf(window.tab_sediment))
    assert not window.open_action.isEnabled() and not window.solve_action.isEnabled()
    window.navigation.setCurrentItem(window._navigation_items[8])
    assert window.tabs.currentWidget() is window.tab_diagnostics
    window.set_busy(False)
    assert window.open_action.isEnabled() and window.solve_action.isEnabled()
    window.close()
    assert not window.tab_diagnostics.timer.isActive()
