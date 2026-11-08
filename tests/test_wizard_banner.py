"""Crossing timing, drawing compatibility and decorative-widget lifecycle."""
import math
import random

import pytest

from wizard_banner import WizardMotion, WizardBanner, TRICKS, SCALE, SCENE_HEIGHT


def test_random_tricks_and_quiet_crossings_never_interrupt_more_than_once():
    motion = WizardMotion(rng=random.Random(42))
    half_walk = (440-44)/38/2
    seen = []
    for crossing in range(120):
        assert motion.phase == 'walk' and motion.progress == 0
        assert motion.direction == (1 if crossing % 2 == 0 else -1)
        chosen = motion.trick
        seen.append(chosen)
        if chosen is None:
            motion.advance(half_walk+.1, 440)
            assert motion.phase == 'walk' and motion.progress > .5
            motion.advance(half_walk-.1, 440)
        else:
            motion.advance(half_walk, 440)
            assert motion.phase == 'trick' and motion.progress == .5
            motion.advance(motion.duration/2, 440)
            assert motion.phase == 'trick' and motion.trick == chosen
            motion.advance(motion.duration/2+half_walk, 440)
        assert motion.pass_number == crossing+1
    assert set(seen) == {None, *TRICKS}
    assert .2 < seen.count(None)/len(seen) < .4
    performed = [name for name in seen if name is not None]
    assert all(a != b for a, b in zip(performed, performed[1:]))
    assert performed[:len(TRICKS)] != list(TRICKS)


def test_quiet_passes_never_stop_and_probability_is_validated():
    motion = WizardMotion(trick_probability=0)
    motion.advance((320-44)/38*.75, 320)
    assert motion.phase == 'walk' and motion.progress == pytest.approx(.75)
    assert motion.trick is None and motion.trick_fraction == 0
    for probability in (-.1, 1.1, math.inf, math.nan):
        with pytest.raises(ValueError):
            WizardMotion(trick_probability=probability)


def test_animation_randomness_is_isolated_and_delayed_frames_do_not_change_choices():
    before = random.getstate()
    large_steps = WizardMotion(rng=random.Random(12))
    small_steps = WizardMotion(rng=random.Random(12))
    large_steps.advance(100.025, 320)
    for _ in range(4001):
        small_steps.advance(.025, 320)
    assert large_steps.pass_number == small_steps.pass_number
    assert large_steps.trick == small_steps.trick
    assert large_steps.phase == small_steps.phase
    assert large_steps.progress == pytest.approx(small_steps.progress)
    assert large_steps.trick_elapsed == pytest.approx(small_steps.trick_elapsed)
    WizardMotion().advance(1000, 320)
    assert random.getstate() == before


def test_delayed_frames_preserve_crossings_and_resizing_keeps_wizard_in_view():
    motion = WizardMotion(rng=random.Random(2))
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
    rng_state = banner.motion.rng.getstate()
    for crossing in range(2*len(TRICKS)):
        banner.motion.phase = 'trick'
        banner.motion.progress = .5
        banner.motion.pass_number = crossing
        banner.motion.trick = TRICKS[crossing % len(TRICKS)]
        for fraction in (.2, .55, .85):
            banner.motion.trick_elapsed = banner.motion.duration*fraction
            frame = banner.frame_image()
            assert not frame.isNull() and frame.height() == SCENE_HEIGHT
            frames.add(bytes(frame.constBits().asstring(frame.sizeInBytes())))
    assert len(frames) > 2*len(TRICKS)  # Effects/poses change, not a static picture.
    assert banner.motion.rng.getstate() == rng_state  # Painting never rerolls a pass.
    banner.pause_button.click()
    assert not banner.paused and banner.timer.isActive()
    banner.hide()
    assert not banner.timer.isActive()
    banner.show()
    qapp.processEvents()
    assert banner.timer.isActive()
    banner.close()
    assert not banner.timer.isActive()
