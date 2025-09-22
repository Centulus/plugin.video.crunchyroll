# -*- coding: utf-8 -*-
# Crunchyroll
# Copyright (C) 2023 smirgol
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
import json
import time
from typing import Optional, List
from urllib.parse import urlencode

import requests
import random
import xbmc
import xbmcgui
import xbmcplugin

from resources.lib import utils
from resources.lib.globals import G
from resources.lib.gui import SkipModalDialog, show_modal_dialog
from resources.lib.model import Object, CrunchyrollError, LoginError
from resources.lib.videostream import VideoPlayerStreamData, VideoStream


class CrunchyPlayer(xbmc.Player):
    """Custom player to capture playback events for immediate playhead updates."""
    def __init__(self, parent):
        super().__init__()
        self._parent = parent

    def onAVStarted(self):
        try:
            utils.crunchy_log("onAVStarted: playback started", xbmc.LOGINFO)
            self._parent._on_started()
        except Exception as e:
            try:
                utils.crunchy_log(f"onAVStarted handler error: {e}", xbmc.LOGERROR)
            except Exception:
                pass

    def onPlayBackStarted(self):
        try:
            utils.crunchy_log("onPlayBackStarted: playback started", xbmc.LOGINFO)
            self._parent._on_started()
        except Exception as e:
            try:
                utils.crunchy_log(f"onPlayBackStarted handler error: {e}", xbmc.LOGERROR)
            except Exception:
                pass

    def onPlayBackSeek(self, time, seekOffset):
        try:
            utils.crunchy_log(f"onPlayBackSeek: time={time}, offset={seekOffset}", xbmc.LOGINFO)
            # Kodi provides seek time in milliseconds; convert to seconds for playhead
            try:
                new_time_secs = int(round(float(time) / 1000.0))
            except Exception:
                # Fallback: assume already seconds
                new_time_secs = int(time)
            # Pass the normalized playback time to ensure reliable detection
            self._parent._on_seek(new_time_secs)
        except Exception as e:
            try:
                utils.crunchy_log(f"onPlayBackSeek handler error: {e}", xbmc.LOGERROR)
            except Exception:
                pass

class VideoPlayer(Object):
    """ Handles playing video using data contained in args object

    Keep instance of this class in scope, while playing, as threads started by it rely on it
    """

    def __init__(self):
        self._stream_data = None  # type: Optional[VideoPlayerStreamData]
        self._player = CrunchyPlayer(self)  # use custom player to receive events
        self._skip_modal_duration_max = 10
        self.waitForStart = True
        self.lastUpdatePlayhead = 0
        self.lastKnownTime = 0  # Track for seek detection
        self.wasPlaying = False  # Track for pause detection
        self.playheadSent = False  # Track if we sent initial playhead
        self.clearedStream = False
        self.createTime = time.time()
        self._playing_url = None  # type: Optional[str]  # actual URL Kodi is playing (may be local proxy)
        self._paused = False  # Track pause state to send one-shot update on pause
        self._last_seek_update_ts = 0.0  # Cooldown to prevent duplicate seek updates
        # serialize playhead updates across events and loop
        import threading as _threading
        self._playhead_lock = _threading.Lock()

    def start_playback(self):
        """ Set up player and start playback """

        # already playing for whatever reason?
        if self.isPlaying():
            utils.log("Skipping playback because already playing")
            return

        self.clear_all_active_streams()

        if not self._get_video_stream_data():
            return

        self._prepare_and_start_playback()

    def isPlaying(self) -> bool:
        if not self._stream_data or not self._player:
            return False
        # Rely on Kodi's state; comparing paths is unreliable (plugin:// vs local proxy)
        try:
            return bool(self._player.isPlayingVideo())
        except Exception:
            return False

    def isStartingOrPlaying(self) -> bool:
        """ Returns true if playback is running. Note that it also returns true when paused. """

        if not self._stream_data:
            return False

        # Consider paused state as active playback for our loop
        try:
            if xbmc.getCondVisibility('Player.Paused'):
                self.waitForStart = False
                return True
        except Exception:
            pass

        if self.isPlaying():
            self.waitForStart = False
            return True

        # Wait max 20 sec for start playing the stream
        if (time.time() - self.createTime) > 20:
            if self.waitForStart:
                self.waitForStart = False
                utils.crunchy_log("Timout start playing file")
        return self.waitForStart

    def finished(self, forced=False):
        if not self.clearedStream or forced:
            self.clearedStream = True
            self.waitForStart = False
            # Send final playhead update on finish to capture last position
            try:
                if self._player and self._player.isPlayingVideo():
                    final_pos = self._safe_playhead(int(self._player.getTime()))
                    if final_pos >= 10:
                        update_playhead(G.args.get_arg('episode_id'), final_pos)
            except Exception:
                pass
            self.clear_active_stream()

    def _get_video_stream_data(self) -> bool:
        """ Fetch all required stream data using VideoStream object """

        video_stream_helper = VideoStream()
        item = xbmcgui.ListItem(G.args.get_arg('title', 'Title not provided'))

        try:
            self._stream_data = video_stream_helper.get_player_stream_data()
            if not self._stream_data or not self._stream_data.stream_url:
                utils.crunchy_log("Failed to load stream info for playback", xbmc.LOGERROR)
                xbmcplugin.setResolvedUrl(int(G.args.argv[1]), False, item)
                xbmcgui.Dialog().ok(G.args.addon_name, G.args.addon.getLocalizedString(30064))
                return False

        except (CrunchyrollError, requests.exceptions.RequestException) as e:
            utils.log_error_with_trace("Failed to prepare stream info data", False)
            xbmcplugin.setResolvedUrl(int(G.args.argv[1]), False, item)

            # check for TOO_MANY_ACTIVE_STREAMS
            if 'TOO_MANY_ACTIVE_STREAMS' in str(e):
                xbmcgui.Dialog().ok(G.args.addon_name,
                                    G.args.addon.getLocalizedString(30080))
                playlist = xbmc.PlayList(xbmc.PLAYLIST_VIDEO)
                playlist.clear()
            else:
                xbmcgui.Dialog().ok(G.args.addon_name,
                                    G.args.addon.getLocalizedString(30064))
                playlist = xbmc.PlayList(xbmc.PLAYLIST_VIDEO)
                playlist.clear()
            return False

        return True

    def _prepare_and_start_playback(self):
        """ Sets up the playback"""

        # prepare playback
        # note: when setting only a couple of values to the item, kodi will fetch the remaining from the url args
        #       since we do a full overwrite of the item with data from the cms object, which does not contain all
        #       wanted data - like playhead - we need to copy over that information to the PlayableItem before
        #        converting it to a kodi item. be aware of this.

        # copy playhead to PlayableItem when available (do not depend on argv[3])
        try:
            ep_id = G.args.get_arg('episode_id')
            if ep_id and self._stream_data.playheads_data.get(ep_id):
                self._stream_data.playable_item.update_playcount_from_playhead(
                    self._stream_data.playheads_data.get(ep_id)
                )
        except Exception:
            pass

        item = self._stream_data.playable_item.to_item()
        # Use the original stream URL directly (no local file/proxy)
        item.setPath(self._stream_data.stream_url)
        self._playing_url = self._stream_data.stream_url
        item.setMimeType('application/dash+xml')
        item.setContentLookup(False)

        # inputstream adaptive
        try:
            from inputstreamhelper import Helper  # type: ignore
        except Exception:
            Helper = None  # type: ignore

        is_helper = Helper("mpd", drm='com.widevine.alpha') if Helper else None
        #if is_helper.check_inputstream():
        manifest_headers = {
            # Match Android TV okhttp behavior for MPD fetch - minimal headers only
            'Authorization': f"Bearer {G.api.account_data.access_token}"
        }
        license_headers = {
            'User-Agent': getattr(G.api, 'UA_ATV', None) or G.api.CRUNCHYROLL_UA,
            'Content-Type': 'application/octet-stream',
            'Origin': 'https://static.crunchyroll.com',
            'Authorization': f"Bearer {G.api.account_data.access_token}",
            'x-cr-content-id': G.args.get_arg('episode_id'),
            'x-cr-video-token': self._stream_data.token
        }
        # Ensure we have a Cloudflare cookie from API init if available
        try:
            if not getattr(G.api, 'cf_cookie', None):
                utils.crunchy_log("Initializing Cloudflare cookie for manifest", xbmc.LOGINFO)
                G.api.init_cf_cookie()
        except Exception:
            pass
        # Apply existing API CF cookie to both license and manifest headers (will be overridden if validation returns newer cookies)
        if hasattr(G.api, 'cf_cookie') and G.api.cf_cookie:
            license_headers['Cookie'] = G.api.cf_cookie
            manifest_headers['Cookie'] = G.api.cf_cookie
        license_config = {
            'license_server_url': G.api.LICENSE_ENDPOINT,
            'headers': urlencode(license_headers),
            'post_data': 'R{SSM}',
            'response_data': 'JBlicense'
        }

        # Validate MPD access and get cookies via cloudscraper (random UA from browsers.json)
        cf_cookie, ua_used, _ = self._validate_mpd_and_get_cookie(manifest_headers)
        try:
            if isinstance(ua_used, str) and ('Chrome' in ua_used or 'Chromium' in ua_used or 'CriOS' in ua_used):
                chosen_ua = ua_used
            else:
                chosen_ua = 'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/121.0.0.0 Safari/537.36'
        except Exception:
            chosen_ua = 'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/121.0.0.0 Safari/537.36'
        chosen_cookie = cf_cookie or getattr(G.api, 'cf_cookie', None)
        if chosen_cookie:
            manifest_headers['Cookie'] = chosen_cookie
            license_headers['Cookie'] = chosen_cookie
        
        # Add headers to manifest/stream headers
        manifest_headers['User-Agent'] = chosen_ua
        manifest_headers['Accept'] = 'application/dash+xml,application/xml,text/xml,*/*'
        manifest_headers['Accept-Language'] = 'fr-FR,fr;q=0.9,en-US;q=0.8,en;q=0.7'
        # Align license UA with the UA that obtained the cookies
        try:
            license_headers['User-Agent'] = chosen_ua
        except Exception:
            pass

        try:
            manifest_headers['x-cr-content-id'] = G.args.get_arg('episode_id') or ''
        except Exception:
            manifest_headers['x-cr-content-id'] = ''
        try:
            manifest_headers['x-cr-video-token'] = self._stream_data.token or ''
        except Exception:
            manifest_headers['x-cr-video-token'] = ''
        # Provide a neutral referer for www domain (optional but fine)
        manifest_headers['Referer'] = 'https://www.crunchyroll.com/'

        # Build header strings for ISA (URL-encoded key=value&key2=value2)
        manifest_headers_str = urlencode(manifest_headers)
        license_headers_str = urlencode(license_headers)
        # Update license config with updated headers
        license_config['headers'] = license_headers_str

        inputstream_config = {
            'ssl_verify_peer': False
        }

        item.setProperty("inputstream", "inputstream.adaptive")
        item.setProperty("inputstream.adaptive.license_type", "com.widevine.alpha")
        item.setProperty('inputstream.adaptive.stream_headers', manifest_headers_str)
        item.setProperty("inputstream.adaptive.manifest_headers", manifest_headers_str)
        item.setProperty('inputstream.adaptive.license_key', '|'.join([
            license_config['license_server_url'],
            license_config['headers'],
            license_config['post_data'],
            license_config['response_data']
        ]))
        item.setProperty('inputstream.adaptive.config', json.dumps(inputstream_config))

        # Keep remote MPD URL; ISA will fetch it using provided headers/cookies

        # @todo: i think other meta data like description and images are still fetched from args.
        #        we should call the objects endpoint and use this data to remove args dependency (besides id)

        # add soft subtitles url for configured language
        if self._stream_data.subtitle_urls:
            item.setSubtitles(self._stream_data.subtitle_urls)

        # Apply resume to the resolved item when playhead is present
        try:
            playhead = int(float(getattr(self._stream_data.playable_item, 'playhead', 0)) or 0)
            if playhead > 0:
                safe = self._safe_playhead(playhead)
                # Set StartOffset for the player to start at position
                item.setProperty('StartOffset', str(safe))
                # Also set InfoTag resume point for clients reading JSON (e.g., Yatse)
                try:
                    tag = item.getVideoInfoTag()
                    duration = int(float(getattr(self._stream_data.playable_item, 'duration', 0)) or 0)
                    if duration > 0 and safe >= 10 and safe <= int(duration * 0.95):
                        tag.setResumePoint(safe, duration)
                except Exception:
                    pass
        except Exception:
            pass

        """ start playback"""
        xbmcplugin.setResolvedUrl(int(G.args.argv[1]), True, item)

    def _safe_playhead(self, seconds: int) -> int:
        """Clamp playhead to a safe range [0, duration-1] to avoid overshoots/completions."""
        try:
            if seconds < 0:
                return 0
            total = 0
            try:
                total = int(self._player.getTotalTime()) if self._player else 0
            except Exception:
                total = 0
            if total > 0:
                return max(0, min(seconds, max(0, total - 1)))
            return seconds
        except Exception:
            return max(0, seconds)

    def _validate_mpd_and_get_cookie(self, manifest_headers):
        """Validate MPD access via cloudscraper using a random UA from browsers.json.
        Returns a tuple: (cookie_header_string, ua_used, mpd_text)
        """
        try:
            from ..modules import cloudscraper

            # Reuse recent cookies/UA when available to avoid re-solving Cloudflare every time
            try:
                if getattr(G.api, 'cf_cookie', None) and getattr(G.api, 'cf_ts', 0):
                    ttl_seconds = 600  # 10 minutes TTL; extend if stable for you
                    if (time.time() - getattr(G.api, 'cf_ts', 0)) < ttl_seconds:
                        return G.api.cf_cookie, getattr(G.api, 'cf_ua', None), None
            except Exception:
                pass

            # Build headers for the cloudscraper request
            prefetch_headers = dict(manifest_headers)
            # Let cloudscraper decide the UA; don't override here
            prefetch_headers.pop('User-Agent', None)
            # Include existing CF cookie if available
            if hasattr(G.api, 'cf_cookie') and G.api.cf_cookie:
                prefetch_headers['Cookie'] = G.api.cf_cookie
            
            # Add more headers to match what might be expected
            prefetch_headers['Accept'] = 'application/dash+xml,application/xml,text/xml,*/*'
            prefetch_headers['Accept-Language'] = 'en-US,en;q=0.9'
            prefetch_headers['Origin'] = 'https://static.crunchyroll.com'
            prefetch_headers['Referer'] = 'https://static.crunchyroll.com/'

            # Use a longer delay to ensure challenge is fully solved
            browser_candidates = [
                {'browser': 'chrome',  'platform': 'windows', 'mobile': False},
                {'browser': 'chrome',  'platform': 'android', 'mobile': True},
            ]
            browser_cfg = random.choice(browser_candidates)
            scraper = cloudscraper.create_scraper(
                delay=10,
                browser=browser_cfg,
                captcha={'provider': 'return_response'}  # Return response even if captcha
            )
            cf_cookie = None
            mpd_text = None
            ua_used = None
            resp = None
            try:
                # Warm-up visit on homepage to ensure domain-level CF cookies are set
                try:
                    scraper.get('https://www.crunchyroll.com/', headers=prefetch_headers, timeout=15)
                except Exception:
                    pass
                resp = scraper.get(self._stream_data.stream_url, headers=prefetch_headers, timeout=15)
                try:
                    ua_used = scraper.headers.get('User-Agent')
                except Exception:
                    ua_used = None
                if resp.ok:
                    try:
                        mpd_text = resp.text
                    except Exception:
                        mpd_text = None
                
                # Extract ALL cookies from the session (not just CF)
                if resp.ok:
                    # Get all cookies from the entire session (includes CF challenge cookies)
                    all_cookies = []
                    
                    # Extract from response cookies
                    for cookie in resp.cookies:
                        all_cookies.append(f"{cookie.name}={cookie.value}")
                    
                    # Also get cookies from the scraper session
                    try:
                        session_cookies = scraper.cookies
                        for cookie in session_cookies:
                            cookie_str = f"{cookie.name}={cookie.value}"
                            if cookie_str not in all_cookies:
                                all_cookies.append(cookie_str)
                    except Exception:
                        pass

                    # Merge cookies from prefetch (e.g., cr_exp) if not present yet
                    try:
                        pre_cookie_str = prefetch_headers.get('Cookie')
                        if pre_cookie_str:
                            existing_names = {c.split('=', 1)[0] for c in all_cookies}
                            for part in pre_cookie_str.split(';'):
                                part = part.strip()
                                if not part or '=' not in part:
                                    continue
                                name = part.split('=', 1)[0]
                                if name not in existing_names:
                                    all_cookies.append(part)
                    except Exception:
                        pass
                    
                    if all_cookies:
                        cf_cookie = '; '.join(all_cookies)
                    else:
                        # Fallback to existing CF cookie from API
                        cf_cookie = getattr(G.api, 'cf_cookie', None)
                # Persist cookies for global reuse
                try:
                    if cf_cookie:
                        G.api.cf_cookie = cf_cookie
                        G.api.cf_ua = ua_used
                        G.api.cf_ts = time.time()
                except Exception:
                    pass
            finally:
                try:
                    scraper.close()
                except Exception:
                    pass

            if not resp or not getattr(resp, 'ok', False):
                try:
                    code = getattr(resp, 'status_code', 'N/A')
                except Exception:
                    code = 'N/A'
                utils.crunchy_log(f"Failed to validate MPD via cloudscraper: {code}", xbmc.LOGERROR)
            
            return cf_cookie, ua_used, mpd_text

        except Exception as e:
            utils.log_error_with_trace(f"MPD validation failed: {e}", False)
            return None, None, None

    # ==== Playback event handlers ====
    def _emit_playhead(self, label: str, pos: int, force: bool = False):
        """Helper to clamp, log, send, and update state for playhead updates."""
        safe = self._safe_playhead(int(pos))
        # don't spam duplicates
        if not force and safe == int(self.lastUpdatePlayhead):
            self.lastKnownTime = safe
            return
        # gate tiny positions; we don't persist <10s
        if safe < 10:
            self.lastKnownTime = safe
            self.wasPlaying = True
            utils.crunchy_log(f"{label} below 10s -> skip send ({safe}s)", xbmc.LOGDEBUG)
            return
        utils.crunchy_log(f"{label} at {safe}", xbmc.LOGINFO)
        # prevent overlapping updates; network can be slow
        with self._playhead_lock:
            update_playhead(G.args.get_arg('episode_id'), safe)
            self.lastUpdatePlayhead = safe
            self.lastKnownTime = safe
            self.wasPlaying = True

    def is_paused(self) -> bool:
        try:
            return bool(xbmc.getCondVisibility('Player.Paused'))
        except Exception:
            return self._paused

    def _on_started(self):
        try:
            current = int(self._player.getTime()) if self._player else 0
            current = self._safe_playhead(current)
        except Exception:
            current = 0
        # Force an immediate playhead on start
        try:
            self._emit_playhead("Event: started -> playhead", current, force=True)
            self.playheadSent = True
        except Exception:
            pass

    def _on_paused(self):
        try:
            current = int(self._player.getTime()) if self._player else int(self.lastKnownTime)
            current = self._safe_playhead(current)
        except Exception:
            current = int(self.lastKnownTime)
        try:
            self._emit_playhead("Event: paused -> immediate playhead", current, force=True)
        except Exception:
            pass

    def _on_resumed(self):
        # No mandatory update, but keep tracking vars in sync
        try:
            current = int(self._player.getTime()) if self._player else int(self.lastKnownTime)
            self.lastKnownTime = current
            utils.crunchy_log("Event: resumed", xbmc.LOGINFO)
        except Exception:
            pass

    def _on_seek(self, new_time: Optional[int] = None):
        try:
            # Prefer the time provided by the event when available
            if new_time is not None:
                # Defensive: if mistakenly in ms, normalize to seconds
                current = int(new_time)
                if current > 24 * 60 * 60 * 12:  # > 12 hours is unrealistic for episodes
                    current = int(round(current / 1000.0))
            else:
                current = int(self._player.getTime()) if self._player else int(self.lastKnownTime)
            current = self._safe_playhead(current)
        except Exception:
            current = int(self.lastKnownTime)
        try:
            self._emit_playhead("Event: seek -> immediate playhead", current, force=True)
            self._last_seek_update_ts = time.time()
        except Exception:
            pass

    def _on_stopped(self, ended: bool):
        # Send final update and clean up
        try:
            utils.crunchy_log(f"Event: {'ended' if ended else 'stopped'} -> finalize", xbmc.LOGINFO)
        except Exception:
            pass
        self.finished(forced=True)

    def update_playhead(self):
        """ Smart playhead updates: immediate on events, periodic during normal playback """
        if not self.isPlaying():
            # If we were playing before and now stopped, send final position (pause/stop)
            if self.wasPlaying and self.lastKnownTime >= 10:
                utils.crunchy_log(f"Playback paused/stopped - immediate playhead update at {int(self.lastKnownTime)}", xbmc.LOGINFO)
                update_playhead(G.args.get_arg('episode_id'), int(self.lastKnownTime))
                self.wasPlaying = False
            return
        
        try:
            current = self._player.getTime()
            # Detect explicit pause via Kodi condition
            is_paused = False
            try:
                is_paused = xbmc.getCondVisibility('Player.Paused')
            except Exception:
                pass

            if is_paused:
                if not self._paused:
                    # Transition playing -> paused: send immediate update
                    self._paused = True
                    if int(current) >= 10:
                        self._emit_playhead("Paused - immediate playhead update", int(current), force=True)
                # Stay paused: do not spam
                return
            else:
                if self._paused:
                    # Transition paused -> playing
                    self._paused = False
            
            # First playback start - immediate update
            if not self.playheadSent:
                self.playheadSent = True
                self._emit_playhead("Playback started - immediate playhead update", int(current), force=True)
                return
            
            # Detect seek (jump >= 3 seconds) - immediate update
            # Guard against double triggering right after onPlayBackSeek
            now_ts = time.time()
            if (now_ts - self._last_seek_update_ts) >= 1.0 and abs(current - self.lastKnownTime) >= 3:
                self._emit_playhead("Seek detected - immediate playhead update", int(current), force=True)
                return
            
            # Normal playback - update every 10 seconds
            if (current - self.lastUpdatePlayhead) >= 10:
                self._emit_playhead("Regular playhead update", int(current))
                return
            
            # Update tracking vars even when not sending
            self.lastKnownTime = self._safe_playhead(int(current))
            self.wasPlaying = True
            
        except Exception as e:
            utils.crunchy_log(f"update_playhead failed: {e}", xbmc.LOGERROR)

    def check_skipping(self):
        """ background thread to check and handle skipping intro/credits/... """

        if len(self._stream_data.skip_events_data) == 0:
            return

        if not self.isPlaying():
            return

        for skip_type in list(self._stream_data.skip_events_data):
            # are we within the skip event timeframe?
            current_time = int(self._player.getTime())
            skip_time_start = self._stream_data.skip_events_data.get(skip_type).get('start')
            skip_time_end = self._stream_data.skip_events_data.get(skip_type).get('end')

            if skip_time_start <= current_time < skip_time_end:
                if G.args.addon.getSetting("ask_before_skipping") != "true":
                    self._instaskip(skip_type)
                else:
                    self._ask_to_skip(skip_type)
                    # remove the skip_type key from the data, so it won't trigger again
                    self._stream_data.skip_events_data.pop(skip_type, None)

    def _ask_to_skip(self, section):
        """ Show skip modal """

        utils.crunchy_log("_ask_to_skip", xbmc.LOGINFO)

        dialog_duration = (self._stream_data.skip_events_data.get(section, []).get('end', 0) -
                           self._stream_data.skip_events_data.get(section, []).get('start', 0))

        # show only for the first X seconds
        dialog_duration = min(dialog_duration, self._skip_modal_duration_max)

        # Open the dedicated skip dialog window and let it perform the seek
        try:
            dlg = SkipModalDialog('plugin-video-crunchyroll-skip.xml',
                                   G.args.addon.getAddonInfo('path'),
                                   'default', '1080i',
                                   seek_time=self._stream_data.skip_events_data.get(section).get('end'),
                                   content_id=G.args.get_arg('episode_id'),
                                   label=G.args.addon.getLocalizedString(30015))
            dlg.show()
            # Keep it visible only for a bounded duration
            t0 = time.time()
            while dlg and (time.time() - t0) < max(1, int(dialog_duration)):
                # Abort-aware wait in 100ms slices
                try:
                    _monitor = xbmc.Monitor()
                    if _monitor.waitForAbort(0.1):
                        break
                except Exception:
                    pass
                # If user pressed the button, the dialog will close itself
                if not dlg.isVisible():
                    break
            try:
                dlg.close()
            except Exception:
                pass
        except Exception:
            # Fallback: direct instaskip if dialog fails
            self._instaskip(section)

    def _instaskip(self, section):
        """ Skip immediatly without asking """

        utils.crunchy_log("_instaskip", xbmc.LOGINFO)

        self._player.seekTime(self._stream_data.skip_events_data.get(section, []).get('end', 0))
        self.update_playhead()

    def clear_active_stream(self, token: Optional[str] = None):
        """ Tell Crunchyroll that we no longer use the stream.
            Crunchyroll keeps track of started streams. If they are not released, CR will block starting a new one.
        """

        if not G.args.get_arg('episode_id') or not self._stream_data.token:
            return

        token = token or self._stream_data.token
        # try a couple of times with small backoff; network can be flaky
        for attempt in range(2):
            try:
                G.api.make_request(
                    method="DELETE",
                    url=G.api.STREAMS_ENDPOINT_CLEAR_STREAM.format(G.args.get_arg('episode_id'), token),
                    timeout=10
                )
                utils.crunchy_log("Cleared active stream for episode: %s" % G.args.get_arg('episode_id'))
                return
            except (CrunchyrollError, LoginError, requests.exceptions.RequestException) as _e:
                if attempt == 0:
                    # Abort-aware small backoff instead of time.sleep to keep Kodi responsive
                    try:
                        _monitor = xbmc.Monitor()
                        if _monitor.waitForAbort(0.5):
                            return
                    except Exception:
                        pass
                else:
                    utils.crunchy_log("Failed to clear active stream for episode: %s" % G.args.get_arg('episode_id'))
                    return


    def get_active_streams(self) -> List[str]:
        try:
            # This endpoint must be called with GET to list streams
            req = G.api.make_request(
                method="GET",
                url=G.api.STREAMS_ENDPOINT_GET_ACTIVE_STREAMS,
                timeout=10
            )
        except (CrunchyrollError, LoginError, requests.exceptions.RequestException):
            # catch timeout or any other possible exception
            utils.crunchy_log("Failed to get active streams")
            return []

        active: List[str] = []
        if not req:
            return active

        # Normalize response to a list of session dicts
        items = []
        if isinstance(req, list):
            # If it's a list of tokens already, return them directly
            if all(isinstance(x, str) for x in req):
                return list(req)
            items = req
        elif isinstance(req, dict):
            for key in ("sessions", "items", "data", "streams", "result"):
                val = req.get(key)
                if isinstance(val, list):
                    items = val
                    break
            if not items:
                # Some backends may return a single object
                items = [req]

        # Filter by this device when device_id is available; otherwise collect all tokens
        current_device = getattr(G.args, 'device_id', None)
        for entry in items:
            if not isinstance(entry, dict):
                continue
            device_id = entry.get('deviceId') or entry.get('device_id')
            token = entry.get('token') or entry.get('video_token') or entry.get('stream_token')
            if not token:
                continue
            if current_device and device_id and device_id != current_device:
                continue
            active.append(token)

        return active

    def clear_all_active_streams(self):
        active_streams_tokens = self.get_active_streams()
        if not active_streams_tokens:
            return

        for token in active_streams_tokens:
            self.clear_active_stream(token)
            utils.crunchy_log("Cleared stream token %s" % token)

def update_playhead(content_id: str, playhead: int):
    """ Update playtime to Crunchyroll """

    # if sync_playtime is disabled in settings, do nothing
    if G.args.addon.getSetting("sync_playtime") != "true":
        utils.crunchy_log("Playhead sync disabled in settings", xbmc.LOGINFO)
        return

    # don't store tiny blips; resume starts at >=10s
    min_resume = 10
    if playhead < min_resume:
        utils.crunchy_log(f"Skip playhead update (<{min_resume}s): content_id={content_id}, playhead={playhead}", xbmc.LOGDEBUG)
        return

    utils.crunchy_log(f"Sending playhead update: content_id={content_id}, playhead={playhead}", xbmc.LOGINFO)

    try:
        # Proactively refresh token well before expiry (safety window)
        try:
            from .api import str_to_date, get_date
            if getattr(G.api.account_data, 'expires', None):
                now = get_date()
                exp = str_to_date(G.api.account_data.expires)
                remaining = (exp - now).total_seconds()
                # Refresh if < 60 seconds remaining
                if remaining < 60:
                    utils.crunchy_log(
                        f"Access token refresh preemptive (remaining ~{int(remaining)}s)", xbmc.LOGINFO
                    )
                    G.api.create_session(action="refresh")
        except Exception:
            pass
        # Ensure Cloudflare cookie present for www endpoint requests
        if not getattr(G.api, 'cf_cookie', None):
            try:
                utils.crunchy_log("Initializing Cloudflare cookie for playhead request", xbmc.LOGINFO)
                G.api.init_cf_cookie()
            except Exception as e:
                utils.crunchy_log(f"Failed to init CF cookie: {e}", xbmc.LOGWARNING)
                pass
        # Post with cloudscraper to bypass Cloudflare on Android TV endpoints
        from ..modules import cloudscraper
        scraper = cloudscraper.create_scraper(
            delay=10,
            browser={'custom': getattr(G.api, 'UA_ATV', None) or G.api.CRUNCHYROLL_UA}
        )
        headers = {
            'User-Agent': getattr(G.api, 'UA_ATV', None) or G.api.CRUNCHYROLL_UA,
            'Authorization': f"Bearer {G.api.account_data.access_token}",
            'Accept': 'application/json',
            'Accept-Charset': 'UTF-8',
            'Content-Type': 'application/json'
        }
        url = G.api.PLAYHEADS_ENDPOINT_WWW.format(G.api.account_data.account_id)
        payload = {'playhead': playhead, 'content_id': content_id}
        
        utils.crunchy_log(f"POST {url} with payload {payload}", xbmc.LOGINFO)
        
        try:
            r = scraper.post(url, json=payload, headers=headers, timeout=15)
        finally:
            # Always close ad-hoc cloudscraper sessions
            try:
                scraper.close()
            except Exception:
                pass
        utils.crunchy_log(f"Playhead response: {r.status_code} - {r.text[:200]}", xbmc.LOGINFO)

        if r.status_code == 401:
            # Refresh token and retry once
            utils.crunchy_log("Playhead 401 - refreshing access token and retrying once", xbmc.LOGWARNING)
            try:
                G.api.create_session(action="refresh")
                # Update headers with new token and cookie
                headers['Authorization'] = f"Bearer {G.api.account_data.access_token}"
                if getattr(G.api, 'cf_cookie', None):
                    headers['Cookie'] = G.api.cf_cookie
                r = scraper.post(url, json=payload, headers=headers, timeout=15)
                utils.crunchy_log(f"Retry playhead response: {r.status_code} - {r.text[:200]}", xbmc.LOGINFO)
            except Exception as e:
                utils.crunchy_log(f"Token refresh failed during playhead retry: {e}", xbmc.LOGERROR)

        if not r.ok:
            raise CrunchyrollError(f"[{r.status_code}] {r.text[:200]}")

        utils.crunchy_log(f"Successfully updated playhead to {playhead} for {content_id}", xbmc.LOGINFO)

    except (CrunchyrollError, requests.exceptions.RequestException) as e:
        # catch timeout or any other possible exception
        utils.crunchy_log(
            f"Failed to update playhead to crunchyroll: {str(e)[:200]} for {content_id}",
            xbmc.LOGERROR
        )
        pass
    except Exception as e:
        utils.crunchy_log(f"Unexpected error updating playhead: {e}", xbmc.LOGERROR)