from unittest.mock import MagicMock, patch
import pytest
from publishing.desktop_window import DesktopWindow, gesture_bounds


def test_close_hides_without_ending_worker_or_destroying_window():
    desktop = DesktopWindow()
    window = MagicMock()
    desktop._window = window
    desktop.close_window()
    window.hide.assert_called_once()
    window.destroy.assert_not_called()


def test_state_event_updates_restore_state_and_excludes_before_load():
    desktop = DesktopWindow()
    window = MagicMock()
    desktop._window = window
    window.events.loaded.is_set.return_value = False
    desktop._state_changed(True)
    assert desktop.window_state() == {'maximized': True}
    window.evaluate_js.assert_not_called()
    window.events.loaded.is_set.return_value = True
    desktop._state_changed(False)
    assert desktop.window_state() == {'maximized': False}
    assert 'maximized: false' in window.evaluate_js.call_args.args[0]


def test_restore_existing_maximized_window():
    desktop = DesktopWindow()
    desktop._window = MagicMock()
    desktop._maximized = True
    desktop.toggle_maximize()
    desktop._window.restore.assert_called_once()


@pytest.mark.parametrize('edge, expected', [
    ('left',(150,200,950,700)),('right',(100,200,1050,700)),
    ('top',(100,230,1000,670)),('bottom',(100,200,1000,730)),
    ('top-left',(150,230,950,670)),('top-right',(100,230,1050,670)),
    ('bottom-left',(150,200,950,730)),('bottom-right',(100,200,1050,730)),
    ('move',(150,230,1000,700))])
def test_drag_geometry_changes_correct_edges(edge, expected):
    assert gesture_bounds((100,200,1000,700),edge,50,30,(780,560)) == expected


def test_minimum_size_keeps_opposite_corner_fixed():
    assert gesture_bounds((100,200,1000,700),'top-left',900,900,(780,560)) == (320,340,780,560)
    assert gesture_bounds((100,200,1000,700),'bottom-right',-900,-900,(780,560)) == (100,200,780,560)


def test_maximized_cannot_begin_resize():
    desktop = DesktopWindow()
    desktop._maximized = True
    assert desktop.begin_window_gesture('right', 100,100) is None


def test_stale_end_does_not_cancel_new_gesture():
    desktop = DesktopWindow()
    desktop._gesture = {'id':2}
    desktop.end_window_gesture(1)
    assert desktop._gesture == {'id':2}
    desktop.end_window_gesture(2)
    assert desktop._gesture is None


def test_invalid_edge_is_not_passed_to_native_window():
    with pytest.raises(ValueError):
        DesktopWindow().begin_window_gesture('not-an-edge',0,0)


def test_brand_icons_are_served_from_built_assets():
    import sau_backend
    client = sau_backend.app.test_client()
    for path, content_types in [('/publisher.svg',{'image/svg+xml'}),('/publisher.ico',{'image/vnd.microsoft.icon','image/x-icon'}),('/favicon.ico',{'image/vnd.microsoft.icon','image/x-icon'})]:
        response = client.get(path)
        assert response.status_code == 200
        assert response.mimetype in content_types
