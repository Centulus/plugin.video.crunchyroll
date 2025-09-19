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
        except Exception:
            pass

    def onPlayBackStarted(self):
        try:
            utils.crunchy_log("onPlayBackStarted: playback started", xbmc.LOGINFO)
            self._parent._on_started()
        except Exception:
            pass

    def onPlayBackSeek(self, time, seekOffset):
        try:
            utils.crunchy_log(f"onPlayBackSeek: time={time}, offset={seekOffset}", xbmc.LOGINFO)
            self._parent._on_seek()
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
                    final_pos = int(self._player.getTime())
                    if final_pos > 0:
                        update_playhead(G.args.get_arg('episode_id'), final_pos)
            except Exception:
                pass
            self.clear_active_stream()
            # Clean up local MPD proxy server if running
            try:
                if hasattr(self, '_local_server') and self._local_server:
                    self._local_server.shutdown()
                    self._local_server.server_close()
                    self._local_server = None
                if hasattr(self, '_server_thread') and self._server_thread:
                    self._server_thread.join(timeout=1.0)
                    self._server_thread = None
            except Exception:
                pass

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

        # copy playhead to PlayableItem (if resume is true on argv[3]) - this is required for resume capability
        if (
                self._stream_data.playable_item.playhead == 0
                and self._stream_data.playheads_data.get(G.args.get_arg('episode_id'), {})
                and G.args.argv[3] == 'resume:true'
        ):
            self._stream_data.playable_item.update_playcount_from_playhead(
                self._stream_data.playheads_data.get(G.args.get_arg('episode_id'))
            )

        item = self._stream_data.playable_item.to_item()
        # Track the (initial) playing URL. Might change to local proxy later.
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
        if hasattr(G.api, 'cf_cookie') and G.api.cf_cookie:
            license_headers['Cookie'] = G.api.cf_cookie
        license_config = {
            'license_server_url': G.api.LICENSE_ENDPOINT,
            'headers': urlencode(license_headers),
            'post_data': 'R{SSM}',
            'response_data': 'JBlicense'
        }

        # Use cloudscraper to bypass Cloudflare protection via local HTTP proxy
        self._setup_mpd_proxy(item, manifest_headers)

        inputstream_config = {
            'ssl_verify_peer': False
        }

        item.setProperty("inputstream", "inputstream.adaptive")
        item.setProperty("inputstream.adaptive.manifest_type", "mpd")
        item.setProperty("inputstream.adaptive.license_type", "com.widevine.alpha")
        item.setProperty('inputstream.adaptive.stream_headers', urlencode(manifest_headers))
        item.setProperty("inputstream.adaptive.manifest_headers", urlencode(manifest_headers))
        item.setProperty('inputstream.adaptive.license_key', '|'.join(list(license_config.values())))
        item.setProperty('inputstream.adaptive.config', json.dumps(inputstream_config))

        # @todo: i think other meta data like description and images are still fetched from args.
        #        we should call the objects endpoint and use this data to remove args dependency (besides id)

        # add soft subtitles url for configured language
        if self._stream_data.subtitle_urls:
            item.setSubtitles(self._stream_data.subtitle_urls)

        """ start playback"""
        xbmcplugin.setResolvedUrl(int(G.args.argv[1]), True, item)

    def _setup_mpd_proxy(self, item, manifest_headers):
        """Setup local HTTP proxy to serve MPD content via cloudscraper."""
        try:
            import threading
            import http.server
            import socketserver
            from ..modules import cloudscraper
            
            # Fetch MPD via cloudscraper
            scraper = cloudscraper.create_scraper(delay=10, browser={'custom': 'okhttp/4.12.0'})
            resp = scraper.get(self._stream_data.stream_url, headers=manifest_headers, timeout=15)
            utils.crunchy_log(f"MPD fetch via cloudscraper: {resp.status_code}")
            
            if resp.ok and resp.headers.get('Content-Type', '').startswith('application/dash+xml'):
                mpd_content = resp.text
                
                class MPDHandler(http.server.BaseHTTPRequestHandler):
                    def do_GET(self):
                        if self.path == '/manifest.mpd':
                            self.send_response(200)
                            self.send_header('Content-Type', 'application/dash+xml')
                            self.send_header('Content-Length', str(len(mpd_content.encode('utf-8'))))
                            self.send_header('Access-Control-Allow-Origin', '*')
                            self.end_headers()
                            self.wfile.write(mpd_content.encode('utf-8'))
                        else:
                            self.send_error(404)
                    
                    def log_message(self, format, *args):
                        pass  # Suppress HTTP server logs
                
                # Start local HTTP server
                httpd = socketserver.TCPServer(("127.0.0.1", 0), MPDHandler)
                port = httpd.server_address[1]
                local_url = f"http://127.0.0.1:{port}/manifest.mpd"
                
                # Start server in background thread
                server_thread = threading.Thread(target=httpd.serve_forever)
                server_thread.daemon = True
                server_thread.start()
                
                # Update item to use local proxy
                item.setPath(local_url)
                utils.crunchy_log(f"MPD proxy serving at: {local_url}")
                
                # Keep references for cleanup
                self._local_server = httpd
                self._server_thread = server_thread
                # Ensure isPlaying() checks the local proxy URL
                self._playing_url = local_url
            else:
                utils.crunchy_log(f"Failed to fetch MPD via cloudscraper: {resp.status_code}")
                
        except Exception as e:
            utils.log_error_with_trace(f"MPD proxy setup failed: {e}", False)

    # ==== Playback event handlers ====
    def _on_started(self):
        try:
            current = int(self._player.getTime()) if self._player else 0
        except Exception:
            current = 0
        # Force an immediate playhead on start
        try:
            utils.crunchy_log(f"Event: started -> playhead {current}", xbmc.LOGINFO)
            update_playhead(G.args.get_arg('episode_id'), current)
            self.playheadSent = True
            self.lastUpdatePlayhead = current
            self.lastKnownTime = current
            self.wasPlaying = True
        except Exception:
            pass

    def _on_paused(self):
        try:
            current = int(self._player.getTime()) if self._player else int(self.lastKnownTime)
        except Exception:
            current = int(self.lastKnownTime)
        try:
            utils.crunchy_log(f"Event: paused -> immediate playhead {current}", xbmc.LOGINFO)
            update_playhead(G.args.get_arg('episode_id'), current)
            self.lastKnownTime = current
            self.wasPlaying = True
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

    def _on_seek(self):
        try:
            current = int(self._player.getTime()) if self._player else int(self.lastKnownTime)
        except Exception:
            current = int(self.lastKnownTime)
        try:
            utils.crunchy_log(f"Event: seek -> immediate playhead {current}", xbmc.LOGINFO)
            update_playhead(G.args.get_arg('episode_id'), current)
            self.lastUpdatePlayhead = current
            self.lastKnownTime = current
            self.wasPlaying = True
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
            if self.wasPlaying and self.lastKnownTime > 0:
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
                    self.lastUpdatePlayhead = current
                    self.lastKnownTime = current
                    self.wasPlaying = True
                    utils.crunchy_log(f"Paused - immediate playhead update at {int(current)}", xbmc.LOGINFO)
                    update_playhead(G.args.get_arg('episode_id'), int(current))
                # Stay paused: do not spam
                return
            else:
                if self._paused:
                    # Transition paused -> playing
                    self._paused = False
            
            # First playback start - immediate update
            if not self.playheadSent:
                self.playheadSent = True
                self.lastUpdatePlayhead = current
                self.lastKnownTime = current
                self.wasPlaying = True
                utils.crunchy_log(f"Playback started - immediate playhead update at {int(current)}", xbmc.LOGINFO)
                update_playhead(G.args.get_arg('episode_id'), int(current))
                return
            
            # Detect seek (jump >3 seconds) - immediate update
            if abs(current - self.lastKnownTime) > 3:
                self.lastUpdatePlayhead = current
                self.lastKnownTime = current
                self.wasPlaying = True
                utils.crunchy_log(f"Seek detected ({int(self.lastKnownTime)} -> {int(current)}) - immediate playhead update", xbmc.LOGINFO)
                update_playhead(G.args.get_arg('episode_id'), int(current))
                return
            
            # Normal playback - update every 20 seconds
            if (current - self.lastUpdatePlayhead) >= 20:
                self.lastUpdatePlayhead = current
                self.lastKnownTime = current
                self.wasPlaying = True
                utils.crunchy_log(f"Regular playhead update at {int(current)}", xbmc.LOGINFO)
                update_playhead(G.args.get_arg('episode_id'), int(current))
                return
            
            # Update tracking vars even when not sending
            self.lastKnownTime = current
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

        show_modal_dialog(SkipModalDialog, "plugin-video-crunchyroll-skip.xml", **{
            'seconds': dialog_duration,
            'seek_time': self._stream_data.skip_events_data.get(section).get('end'),
            'label': G.args.addon.getLocalizedString(30015),
            'addon_path': G.args.addon.getAddonInfo("path"),
            'content_id': G.args.get_arg('episode_id'),
        })

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

        try:
            token = token or self._stream_data.token

            G.api.make_request(
                method="DELETE",
                url=G.api.STREAMS_ENDPOINT_CLEAR_STREAM.format(G.args.get_arg('episode_id'), token),
            )
        except (CrunchyrollError, LoginError, requests.exceptions.RequestException):
            # catch timeout or any other possible exception
            utils.crunchy_log("Failed to clear active stream for episode: %s" % G.args.get_arg('episode_id'))
            return

        utils.crunchy_log("Cleared active stream for episode: %s" % G.args.get_arg('episode_id'))

    def get_active_streams(self) -> List[str]:
        try:
            req = G.api.make_request(
                method="DELETE",
                url=G.api.STREAMS_ENDPOINT_GET_ACTIVE_STREAMS
            )
        except (CrunchyrollError, LoginError, requests.exceptions.RequestException):
            # catch timeout or any other possible exception
            utils.crunchy_log("Failed to get active streams")
            return

        active = []

        if not req:
            return active

        for item in req:
            if item.get('deviceId') != G.args.device_id:
                continue
            active.append(item.get('token'))

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

    utils.crunchy_log(f"Sending playhead update: content_id={content_id}, playhead={playhead}", xbmc.LOGINFO)

    try:
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
        
        r = scraper.post(
            url,
            json=payload,
            headers=headers,
            timeout=15
        )
        
        utils.crunchy_log(f"Playhead response: {r.status_code} - {r.text[:200]}", xbmc.LOGINFO)
        
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