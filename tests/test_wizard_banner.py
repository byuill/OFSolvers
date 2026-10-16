"""Crossing timing, drawing compatibility and decorative-widget lifecycle."""
import math

import pytest

from wizard_banner import WizardMotion, WizardBanner, TRICKS, SCALE, SCENE_HEIGHT


def test_one_trick_per_crossing_and_all_tricks_in_both_directions():
    motion = WizardMotion()
    half_walk = (440-44)/38/2
    seen = []
    for crossing in range(14):
        assert motion.phase == 'walk' and motion.progress == 0
        assert motion.direction == (1 if crossing % 2 == 0 else -1)
        motion.advance(half_walk, 440)
        assert motion.phase == 'trick' and motion.progress == .5
        seen.append(motion.trick)
        motion.advance(motion.duration/2, 440)
        assert motion.phase == 'trick'
        motion.advance(motion.duration/2+half_walk, 440)
        assert motion.pass_number == crossing+1
    assert seen == list(TRICKS)*2


def test_delayed_frames_preserve_crossings_and_resizing_keeps_wizard_in_view():
    motion = WizardMotion()
    crossing_time = (360-44)/38+motion.duration
    motion.advance(crossing_time+.25, 360)
    assert motion.pass_number == 1 and motion.phase == 'walk'
    for width in (107, 240, 800):
        assert 0 < motion.x(width) < width
        motion.advance(.25, width)
        assert 0 <= motion.progress <= 1
    for seconds in (-1, math.inf, math.nan):
        with pytest.raises(ValueError):
            motion.advance(seconds, 360)


def test_every_trick_renders_and_timer_stops_when_paused_or_hidden(qapp):
    banner = WizardBanner()
    banner.resize(1200, SCALE*SCENE_HEIGHT)
    assert not banner.timer.isActive()
    banner.show()
    qapp.processEvents()
    assert banner.timer.isActive()
    banner.pause_button.click()
    assert banner.paused and not banner.timer.isActive()
    assert banner.pause_button.text() == 'Resume wizard'
    frames = set()
    for crossing in range(14):
        banner.motion.phase = 'trick'
        banner.motion.progress = .5
        banner.motion.pass_number = crossing
        for fraction in (.2, .55, .85):
            banner.motion.trick_elapsed = banner.motion.duration*fraction
            frame = banner.frame_image()
            assert not frame.isNull() and frame.height() == SCENE_HEIGHT
            frames.add(bytes(frame.constBits().asstring(frame.sizeInBytes())))
    assert len(frames) > 14  # Effects/poses actually change; not a static picture.
    banner.pause_button.click()
    assert not banner.paused and banner.timer.isActive()
    banner.hide()
    assert not banner.timer.isActive()
    banner.show()
    qapp.processEvents()
    assert banner.timer.isActive()
    banner.close()
    assert not banner.timer.isActive()
