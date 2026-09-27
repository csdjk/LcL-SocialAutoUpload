"""Native window controls exposed only inside the desktop WebView."""
import ctypes
import os
import math

APP_ID = 'LcL.VideoPublisher.Desktop'
EDGES = {'left', 'right', 'top', 'bottom', 'top-left', 'top-right', 'bottom-left', 'bottom-right'}


def gesture_bounds(rect, kind, dx, dy, minimum):
    x, y, width, height = rect
    if kind == 'move':
        return round(x + dx), round(y + dy), width, height
    if kind not in EDGES:
        raise ValueError('无效的窗口边缘')
    if 'left' in kind:
        new_width = max(minimum[0], width - dx)
        x += width - new_width
        width = new_width
    elif 'right' in kind:
        width = max(minimum[0], width + dx)
    if 'top' in kind:
        new_height = max(minimum[1], height - dy)
        y += height - new_height
        height = new_height
    elif 'bottom' in kind:
        height = max(minimum[1], height + dy)
    return tuple(round(value) for value in (x, y, width, height))


def set_app_identity():
    if os.name == 'nt':
        result = ctypes.windll.shell32.SetCurrentProcessExplicitAppUserModelID(APP_ID)
        if result != 0:
            raise OSError(f'无法设置桌面应用标识：{result}')


class DesktopWindow:
    def __init__(self):
        self._window = None
        self._maximized = False
        self._gesture = None
        self._gesture_id = 0

    def _bind(self, window):
        self._window = window
        window.events.maximized += lambda: self._state_changed(True)
        window.events.restored += lambda: self._state_changed(False)

    def _state_changed(self, maximized):
        self._maximized = maximized
        if self._window.events.loaded.is_set():
            self._window.evaluate_js(
                'window.dispatchEvent(new CustomEvent("desktop-window-state", '
                '{detail: {maximized: ' + str(maximized).lower() + '}}))')

    def window_state(self):
        return {'maximized': self._maximized}

    def minimize_window(self):
        self._window.minimize()

    def toggle_maximize(self):
        if self._maximized:
            self._window.restore()
        else:
            if os.name == 'nt':
                from System import Action
                from System.Windows.Forms import Screen
                form = self._window.native
                def work_area():
                    form.MaximizedBounds = Screen.FromControl(form).WorkingArea
                form.Invoke(Action(work_area))
            self._window.maximize()

    def close_window(self):
        # Keep background uploads running, matching the existing tray workflow.
        self._window.hide()

    def begin_window_gesture(self, kind, screen_x, screen_y):
        if kind not in EDGES | {'move'}:
            raise ValueError('无效的窗口操作')
        if not all(isinstance(v, (int, float)) and math.isfinite(v) for v in (screen_x, screen_y)):
            raise ValueError('无效的鼠标坐标')
        if self._maximized and kind != 'move':
            return None
        from System import Action
        form = self._window.native
        def begin():
            bounds = form.Bounds
            self._gesture_id += 1
            self._gesture = {'id': self._gesture_id, 'kind': kind,
                             'rect': (bounds.X, bounds.Y, bounds.Width, bounds.Height),
                             'scale': form.DeviceDpi / 96, 'maximized': self._maximized,
                             'pointer': (screen_x, screen_y)}
        form.Invoke(Action(begin))
        return self._gesture_id

    def update_window_gesture(self, gesture_id, dx, dy):
        if not all(isinstance(v, (int, float)) and math.isfinite(v) and abs(v) < 100000 for v in (dx, dy)):
            raise ValueError('无效的拖动距离')
        from System import Action
        from System.Windows.Forms import FormWindowState
        form = self._window.native
        def update():
            gesture = self._gesture
            if gesture is None or gesture['id'] != gesture_id:
                return
            scale = gesture['scale']
            if gesture['maximized']:
                # Restore under the pointer only once actual dragging starts.
                old_x, _, old_width, _ = gesture['rect']
                grab_x, grab_y = gesture['pointer']
                fraction = min(1, max(0, (grab_x * scale - old_x) / old_width))
                form.WindowState = FormWindowState.Normal
                gesture['rect'] = (round(grab_x * scale - form.Width * fraction),
                                   round(grab_y * scale - 30 * scale), form.Width, form.Height)
                gesture['maximized'] = False
            minimum = tuple(round(v * scale) for v in self._window.min_size)
            bounds = gesture_bounds(gesture['rect'], gesture['kind'], dx * scale, dy * scale, minimum)
            form.SetBounds(*bounds)
        form.Invoke(Action(update))

    def end_window_gesture(self, gesture_id):
        if self._gesture is not None and self._gesture['id'] == gesture_id:
            self._gesture = None
