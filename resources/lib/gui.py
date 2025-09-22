# -*- coding: utf-8 -*-
# Crunchyroll
# based on work by stefanodvx
# Copyright (C) 2023 smirgol
#
#
# This program is free software: you can redistribute it and/or modify
# it under the terms of the GNU Affero General Public License as
# published by the Free Software Foundation, either version 3 of the
# License, or (at your option) any later version.
#
# This program is distributed in the hope that it will be useful,
# but WITHOUT ANY WARRANTY; without even the implied warranty of
# MERCHANTABILITY or FITNESS FOR A PARTICULAR PURPOSE.  See the
# GNU Affero General Public License for more details.
#
# You should have received a copy of the GNU Affero General Public License
# along with this program.  If not, see <http://www.gnu.org/licenses/>.

import xbmc
import xbmcgui

# Minimal no-op lock when threading lock is unavailable
class DummyLock:
    def __enter__(self):
        return None
    def __exit__(self, exc_type, exc, tb):
        return False

ACTION_PREVIOUS_MENU = 10
ACTION_PLAYER_STOP = 13
ACTION_NAV_BACK = 92
ACTION_NOOP = 999

CMD_CLOSE_DIALOG_BY_NOOP = 'AlarmClock(closedialog,Action(noop),{},silent)'


class SkipModalDialog(xbmcgui.WindowXMLDialog):
    """Dialog for skipping video parts (intro, [credits, recap], ...)"""

    def __init__(self, *args, **kwargs):
        self.seek_time = kwargs['seek_time']
        self.content_id = kwargs['content_id']
        self.label = kwargs['label']
        self.action_exit_keys_id = [ACTION_PREVIOUS_MENU,
                                    ACTION_PLAYER_STOP,
                                    ACTION_NAV_BACK,
                                    ACTION_NOOP]
        super().__init__(*args)

    def onInit(self):
        # The skip dialog XML exposes a single button with id=1000. We set its label here.
        try:
            self.getControl(1000).setLabel(self.label)  # keep XML id alignment
        except Exception:
            pass

    def onAction(self, action):
        if action.getId() in self.action_exit_keys_id:
            self.close()

    def onClick(self, control_id):
        # XML button id is 1000; seek and close when pressed.
        if control_id == 1000:
            from . import utils
            utils.seek_to_time(self.seek_time)
            self.close()


def show_modal_dialog(title, text):
    """Show a simple modal dialog with title and text."""
    try:
        dialog = xbmcgui.Dialog()
        dialog.ok(title, text)
    except Exception:
        pass


def show_skip_dialog(seek_time, content_id, label):
    """Show skip dialog for video parts."""
    try:
        dialog = SkipModalDialog('plugin-video-crunchyroll-skip.xml', 
                               xbmc.getInfoLabel('System.AddonPath(plugin.video.crunchyroll)'), 
                               'default', '1080i', 
                               seek_time=seek_time, 
                               content_id=content_id, 
                               label=label)
        dialog.show()
        return dialog
    except Exception:
        return None


class ActivationDialog(xbmcgui.WindowXMLDialog):
    """Dialog to display activation code and QR for device login"""

    def __init__(self, *args, **kwargs):
        self.code = kwargs.get('code', '')
        self.qr_url = kwargs.get('qr_url', '')
        self.info = kwargs.get('info', '')
        # Expires in seconds (provided by the API, typically 300)
        self.expires_in = kwargs.get('expires_in', 300)
        # Poll interval in milliseconds (provided by the API, typically 500) but the official client does 400ms polling ??
        self.interval_ms = kwargs.get('interval_ms', 500)
        self.device_code = kwargs.get('device_code', '')
        self.api_instance = kwargs.get('api_instance', None)
        self.start_time = None
        self.timer_thread = None
        # True while dialog is alive (used by main loop to detect user cancel)
        self.is_running = True
        # True only while the expiry timer thread should run; separated from is_running
        self._timer_running = False
        self.expired = False
        self.canceled = False  # user closed dialog
    # No in-dialog retry; handled via separate listing
        # Thread-safety: protect shared state between UI/main thread and timer thread
        try:
            import threading
            self._lock = threading.RLock()
        except Exception:
            self._lock = None
        super().__init__(*args)
    # Use expires_in provided by the API

    def onInit(self):
        try:
            # Set all the dialog content using our methods
            self.set_code(self.code)
            self.set_qr(self.qr_url)
            self.set_info(self.info)
            # Start timer
            self.start_timer()
        except Exception as e:
            try:
                import xbmc
                xbmc.log(f"[Crunchyroll] Error in ActivationDialog.onInit: {e}", xbmc.LOGERROR)
            except Exception:
                pass

    def start_timer(self):
        """(Re)start the background timer thread."""
        try:
            import time
            import threading
            self.stop_timer()
            # Reset timing refs
            self.expired = False
            # Keep dialog running; only (re)start timer thread
            self._timer_running = True
            with self._lock or DummyLock():
                self.start_time = time.time()
            # Run as daemon; we still join on stop, but this prevents teardown crashes if something slips through
            self.timer_thread = threading.Thread(target=self._timer_loop, daemon=True)
            self.timer_thread.start()
        except Exception:
            try:
                xbmc.log("[Crunchyroll] Failed to start activation timer", xbmc.LOGERROR)
            except Exception:
                pass
    
    def stop_timer(self, timeout: float = 5.0):
        """Stop the background timer thread and join it safely to prevent shutdown crashes."""
        try:
            # Signal only the timer thread to stop (dialog stays alive)
            self._timer_running = False
            
            # Get thread reference safely
            th = getattr(self, 'timer_thread', None)
            if th and hasattr(th, 'is_alive') and th.is_alive():
                try:
                    # Wait for thread to finish cleanly
                    th.join(timeout=timeout)
                    if th.is_alive():
                        xbmc.log("[Crunchyroll] Warning: Timer thread did not stop cleanly", xbmc.LOGWARNING)
                except Exception as e:
                    try:
                        xbmc.log(f"[Crunchyroll] Error joining timer thread: {e}", xbmc.LOGWARNING)
                    except Exception:
                        pass
            
            # Clear reference to prevent memory issues
            self.timer_thread = None
            
        except Exception as e:
            try:
                xbmc.log(f"[Crunchyroll] Error stopping timer: {e}", xbmc.LOGWARNING)
            except Exception:
                pass

    def onAction(self, action):
        """Handle dialog actions."""
        import xbmc
        # ESC / Back: close; do not close on Left to avoid accidental exits
        if action.getId() in [10, 92]:  # PreviousMenu, Back
            # Stop and join timer thread before closing
            self.canceled = True
            self.is_running = False
            self.stop_timer()
            self.close()

    def onDeinit(self):
        """Ensure background work stops when dialog is destroyed."""
        try:
            self.is_running = False
            # Signal the timer loop to stop and join it to avoid stray threads during shutdown
            self.stop_timer(timeout=1.0)
            # Once deinitialized, also make sure no stray expiry state remains
            self._timer_running = False
        except Exception:
            pass
    
    def _timer_loop(self):
        """Timer loop: only tracks expiry; avoid any xbmc or logging calls to be safe during shutdown."""
        import time as _t
        # Capture functions locally to be resilient during interpreter teardown
        _sleep = _t.sleep
        _now = _t.time
        sleep_time = 0.2

        try:
            while getattr(self, '_timer_running', False):
                # Guard: if timing not initialized yet, wait
                st = getattr(self, 'start_time', None)
                if st is None:
                    _sleep(sleep_time)
                    continue

                # Read shared state under lock when available
                try:
                    with self._lock or DummyLock():
                        exp = getattr(self, 'expires_in', 0) or 0
                        start = self.start_time or st
                except Exception:
                    # If lock failed for any reason, fall back to last known values
                    exp = getattr(self, 'expires_in', 0) or 0
                    start = st

                elapsed = _now() - start
                remaining = max(0.0, float(exp) - float(elapsed))

                if remaining <= 0.0:
                    # Signal expiry to main loop; keep dialog alive and just stop the timer
                    self.expired = True
                    self._timer_running = False
                    break

                # Sleep a bounded amount to re-check stop flag frequently
                _sleep(min(sleep_time, remaining))
        except Exception:
            # Swallow all exceptions to avoid interpreter teardown crashes
            pass
        finally:
            return

    def update_activation(self, code: str, device_code: str, expires_in: int, interval_ms: int, qr_url: str):
        """Atomically update activation data and refresh UI (call from main thread)."""
        try:
            with self._lock or DummyLock():
                self.code = (code or '').upper()
                self.device_code = device_code or ''
                # Keep API-provided expiration
                self.expires_in = int(expires_in or 300)
                self.interval_ms = int(interval_ms or 500)
                self.qr_url = qr_url or ''
                # Reset timer reference for new code; caller should restart timer
                import time as _t
                self.start_time = _t.time()
                self.expired = False
        except Exception:
            pass
        # UI updates must happen on main thread (this method is designed to be called there)
        try:
            self.set_code(self.code)
            self.set_qr(self.qr_url)
        except Exception:
            pass

    def set_code(self, code: str):
        """Update the displayed activation code (UI thread only)."""
        self.code = code.upper()  # enforce uppercase
        try:
            self.getControl(4000).setLabel(self.code)
        except Exception:
            pass

    def set_qr(self, qr_url: str):
        self.qr_url = qr_url
        try:
            # If dialog is no longer running, skip
            if not getattr(self, 'is_running', True):
                return

            # Generate QR code image using the lightweight pyqrcode module
            import os, time as _t
            import struct
            import zlib
            _pyqrcode = None
            try:
                from resources.modules import pyqrcode as _pyqrcode
            except Exception:
                try:
                    from ..modules import pyqrcode as _pyqrcode
                except Exception:
                    try:
                        import sys
                        addon_root = os.path.dirname(os.path.dirname(os.path.dirname(__file__)))
                        if addon_root and addon_root not in sys.path:
                            sys.path.insert(0, addon_root)
                        from resources.modules import pyqrcode as _pyqrcode
                    except Exception:
                        xbmc.log("[Crunchyroll] Failed to import pyqrcode module", xbmc.LOGERROR)
                        self._update_qr_status("pyqrcode module not found. Use the code above.")
                        return
            import xbmcvfs
            temp_dir = xbmcvfs.translatePath('special://temp/')
            # Unique filename to avoid caching or races
            qr_path = os.path.join(temp_dir, f"crunchyroll_qr_{int(_t.time()*1000)}.png")
            prev_path = getattr(self, '_last_qr_path', None)

            def _write_png_gray(path, pixels, width, height):
                """Write a minimal 8-bit grayscale PNG. pixels: iterable of rows of bytes (len=width)."""
                def _chunk(fh, ctype, data):
                    fh.write(struct.pack('>I', len(data)))
                    fh.write(ctype)
                    fh.write(data)
                    crc = zlib.crc32(ctype)
                    crc = zlib.crc32(data, crc) & 0xffffffff
                    fh.write(struct.pack('>I', crc))

                with open(path, 'wb') as fh:
                    # PNG signature
                    fh.write(b'\x89PNG\r\n\x1a\n')
                    # IHDR
                    ihdr = struct.pack('>IIBBBBB', width, height, 8, 0, 0, 0, 0)  # 8-bit, grayscale
                    _chunk(fh, b'IHDR', ihdr)
                    # IDAT
                    # Prepend each scanline with filter type 0
                    raw = bytearray()
                    for row in pixels:
                        raw.append(0)
                        raw.extend(row)
                    # Use fast compression for performance
                    compressed = zlib.compress(bytes(raw), level=1)
                    _chunk(fh, b'IDAT', compressed)
                    # IEND
                    _chunk(fh, b'IEND', b'')

            try:
                # Generate QR matrix
                qr = _pyqrcode.create(qr_url)
                qr_matrix = qr.code
                # Slightly smaller scale to reduce pixel count and improve speed while keeping readability
                scale = 6  # pixels per module
                quiet_zone = 4
                matrix_size = len(qr_matrix)
                img_size = (matrix_size + 2 * quiet_zone) * scale

                # Build RGB rows top-to-bottom (PNG uses top-down)
                rows = []
                for y in range(img_size):
                    row = bytearray()
                    for x in range(img_size):
                        mx = (x // scale) - quiet_zone
                        my = (y // scale) - quiet_zone
                        is_black = 0 <= mx < matrix_size and 0 <= my < matrix_size and qr_matrix[my][mx] == 1
                        # grayscale: 0x00 (black) or 0xFF (white)
                        row.append(0x00 if is_black else 0xFF)
                    rows.append(bytes(row))

                _write_png_gray(qr_path, rows, img_size, img_size)
            except Exception as e_gen:
                xbmc.log(f"[Crunchyroll] pyqrcode PNG generation failed: {e_gen}", xbmc.LOGERROR)
                self._update_qr_status("Unable to generate QR code. Use the code above.")
                return

            if xbmcvfs.exists(qr_path):
                # Use direct filesystem path; we already clear the image to avoid caching
                display_path = qr_path
                try:
                    ctrl = self.getControl(4001)
                except Exception:
                    ctrl = None
                set_ok = False
                if ctrl is not None:
                    try:
                        ctrl.setVisible(True)
                        try:
                            ctrl.setImage('', False)  # clear first
                        except Exception:
                            pass
                        # tiny delay to ensure file is flushed
                        _t.sleep(0.02)
                        if getattr(self, 'is_running', True):
                            ctrl.setImage(display_path, False)
                        set_ok = True
                    except Exception as e1:
                        xbmc.log(f"[Crunchyroll] setImage failed: {e1}", xbmc.LOGWARNING)

                self._update_qr_status("")  # Clear status text
                # Cleanup previous file
                try:
                    self._last_qr_path = qr_path
                    if prev_path and prev_path != qr_path and xbmcvfs.exists(prev_path):
                        _t.sleep(0.05)
                        xbmcvfs.delete(prev_path)
                except Exception:
                    pass
            else:
                xbmc.log("[Crunchyroll] QR file does not exist!", xbmc.LOGERROR)
                self._update_qr_status("QR file missing")
        except Exception as e:
            xbmc.log(f"[Crunchyroll] Error setting QR code: {e}", xbmc.LOGERROR)
            self._update_qr_status("QR code error")

    def _update_qr_status(self, status: str):
        """Update QR status text."""
        try:
            self.getControl(4003).setLabel(status)  # noqa
        except Exception:
            pass

    def set_info(self, info: str):
        self.info = info
        try:
            self.getControl(4002).setText(self.info)  # noqa
        except Exception:
            pass