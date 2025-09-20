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
        # Poll interval in milliseconds (provided by the API, typically 500)
        self.interval_ms = kwargs.get('interval_ms', 500)
        self.device_code = kwargs.get('device_code', '')
        self.api_instance = kwargs.get('api_instance', None)
        self.start_time = None
        self.timer_thread = None
        self.is_running = True
        super().__init__(*args)

    def onInit(self):
        try:
            import time
            import threading
            
            # Set all the dialog content using our methods
            self.set_code(self.code)
            self.set_qr(self.qr_url)
            self.set_info(self.info)
            
            # Start timer
            self.start_time = time.time()
            self.timer_thread = threading.Thread(target=self._timer_loop, daemon=True)
            self.timer_thread.start()
            
        except Exception as e:
            import xbmc
            xbmc.log(f"[Crunchyroll] Error in ActivationDialog.onInit: {e}", xbmc.LOGERROR)
    
    def onAction(self, action):
        """Handle dialog actions."""
        import xbmc
        # ESC / Back / Left: close to avoid any stuck state when user navigates away
        if action.getId() in [10, 92, 1]:  # PreviousMenu, Back, Left
            self.is_running = False
            self.close()
    
    def _timer_loop(self):
        """Timer loop that updates countdown and regenerates QR when expired."""
        import time
        import xbmc
        
        while self.is_running:
            try:
                if self.start_time is None:
                    time.sleep(1)
                    continue
                
                elapsed = time.time() - self.start_time
                remaining = max(0, self.expires_in - elapsed)
                
                if remaining <= 0:
                    # Code expired, regenerate
                    xbmc.log("[Crunchyroll] Activation code expired, regenerating...", xbmc.LOGINFO)
                    self._regenerate_code()
                    return
                
                # Timer runs silently (no display)
                # Just log occasionally for debugging
                if int(remaining) % 30 == 0:  # Every 30 seconds
                    minutes = int(remaining // 60)
                    seconds = int(remaining % 60)
                    xbmc.log(f"[Crunchyroll] Activation code expires in: {minutes:02d}:{seconds:02d}", xbmc.LOGINFO)
                
                time.sleep(1)
                
            except Exception as e:
                xbmc.log(f"[Crunchyroll] Timer error: {e}", xbmc.LOGERROR)
                time.sleep(1)
    
    def _regenerate_code(self):
        """Regenerate activation code and QR."""
        import xbmc
        import time
        
        try:
            if not self.api_instance:
                xbmc.log("[Crunchyroll] No API instance for regeneration", xbmc.LOGERROR)
                return
            
            # Request new device code
            xbmc.log("[Crunchyroll] Requesting new device code...", xbmc.LOGINFO)
            device_data = self.api_instance.request_device_code()
            
            if device_data:
                # Update dialog with new data
                self.code = device_data.get('user_code', '')
                self.device_code = device_data.get('device_code', '')
                self.expires_in = device_data.get('expires_in', 300)
                self.interval_ms = device_data.get('interval', self.interval_ms)
                
                # Update QR URL
                self.qr_url = f"https://crunchyroll.com/activate?code={self.code.upper()}&device=Android%20TV"
                
                # Update dialog content
                self.set_code(self.code)
                self.set_qr(self.qr_url)
                
                # Reset timer
                self.start_time = time.time()
                
                xbmc.log(f"[Crunchyroll] New code generated: {self.code}", xbmc.LOGINFO)
            else:
                xbmc.log("[Crunchyroll] Failed to regenerate device code", xbmc.LOGERROR)
                
        except Exception as e:
            xbmc.log(f"[Crunchyroll] Error regenerating code: {e}", xbmc.LOGERROR)

    def set_code(self, code: str):
        self.code = code
        try:
            self.getControl(4000).setLabel(self.code)  # noqa
        except Exception:
            pass

    def set_qr(self, qr_url: str):
        self.qr_url = qr_url
        try:
            import xbmc
            xbmc.log(f"[Crunchyroll] Generating QR code for: {qr_url}", xbmc.LOGINFO)

            # Update status
            self._update_qr_status("Generating QR code...")

            # Generate QR code image using the lightweight pyqrcode module
            import os
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
            import os

            temp_dir = xbmcvfs.translatePath('special://temp/')
            qr_path = os.path.join(temp_dir, 'crunchyroll_qr.png')
            # Remove any older QR files to avoid cache confusion
            try:
                for old in ('special://temp/crunchyroll_qr.png', 'special://temp/crunchyroll_qr.bmp', 'special://temp/crunchyroll_qr.svg'):
                    if xbmcvfs.exists(old):
                        xbmcvfs.delete(old)
            except Exception:
                pass

            def _write_png_rgb(path, pixels, width, height):
                """Write a minimal 24-bit RGB PNG. pixels: iterable of rows of bytes (len=width*3)."""
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
                    ihdr = struct.pack('>IIBBBBB', width, height, 8, 2, 0, 0, 0)  # 8-bit, RGB
                    _chunk(fh, b'IHDR', ihdr)
                    # IDAT
                    # Prepend each scanline with filter type 0
                    raw = bytearray()
                    for row in pixels:
                        raw.append(0)
                        raw.extend(row)
                    compressed = zlib.compress(bytes(raw), level=9)
                    _chunk(fh, b'IDAT', compressed)
                    # IEND
                    _chunk(fh, b'IEND', b'')

            try:
                # Generate QR matrix
                qr = _pyqrcode.create(qr_url)
                qr_matrix = qr.code
                scale = 8  # pixels per module for better visibility
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
                        if is_black:
                            row += b'\x00\x00\x00'
                        else:
                            row += b'\xFF\xFF\xFF'
                    rows.append(bytes(row))

                _write_png_rgb(qr_path, rows, img_size, img_size)
                xbmc.log(f"[Crunchyroll] QR code PNG generated successfully", xbmc.LOGINFO)
            except Exception as e_gen:
                xbmc.log(f"[Crunchyroll] pyqrcode PNG generation failed: {e_gen}", xbmc.LOGERROR)
                self._update_qr_status("Unable to generate QR code. Use the code above.")
                return

            if xbmcvfs.exists(qr_path):
                stat = xbmcvfs.Stat(qr_path)
                xbmc.log(f"[Crunchyroll] QR code generated at: {qr_path} ({stat.st_size()} bytes)", xbmc.LOGINFO)
                # Use special:// path for Kodi to resolve correctly; disable cache to force refresh
                display_path = 'special://temp/crunchyroll_qr.png'
                ctrl = self.getControl(4001)
                set_ok = False
                try:
                    xbmc.log(f"[Crunchyroll] Setting QR image (special): {display_path}", xbmc.LOGINFO)
                    ctrl.setVisible(True)
                    try:
                        ctrl.setImage('', False)  # clear first
                    except Exception:
                        pass
                    # tiny delay to ensure file is flushed
                    try:
                        import time as _t
                        _t.sleep(0.05)
                    except Exception:
                        pass
                    ctrl.setImage(display_path, False)
                    set_ok = True
                except Exception as e1:
                    xbmc.log(f"[Crunchyroll] setImage special:// failed: {e1}", xbmc.LOGWARNING)
                    
                self._update_qr_status("")  # Clear status text
            else:
                xbmc.log("[Crunchyroll] QR file does not exist!", xbmc.LOGERROR)
                self._update_qr_status("QR file missing")
        except Exception as e:
            import xbmc
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