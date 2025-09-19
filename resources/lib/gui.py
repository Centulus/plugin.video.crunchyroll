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

            # Generate QR code image with the known-good segno API (make_qr)
            from .segno import make_qr
            import xbmcvfs
            import os

            temp_dir = xbmcvfs.translatePath('special://temp/')
            qr_path = os.path.join(temp_dir, 'crunchyroll_qr.png')

            qr = make_qr(qr_url)
            qr.save(qr_path, scale=10, border=4)

            if xbmcvfs.exists(qr_path):
                stat = xbmcvfs.Stat(qr_path)
                xbmc.log(f"[Crunchyroll] QR code generated at: {qr_path} ({stat.st_size()} bytes)", xbmc.LOGINFO)
                self.getControl(4001).setImage(qr_path)  # noqa
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

    def _generate_qr_code(self, url: str) -> str:
        """Kept for compatibility but no longer used (generation moved to set_qr)."""
        from .segno import make_qr
        import xbmcvfs, os
        temp_dir = xbmcvfs.translatePath('special://temp/')
        qr_path = os.path.join(temp_dir, 'crunchyroll_qr.png')
        qr = make_qr(url)
        qr.save(qr_path, scale=10, border=4)
        return qr_path if xbmcvfs.exists(qr_path) else ""