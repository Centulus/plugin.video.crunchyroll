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

import time
from datetime import timedelta, datetime
from typing import Optional, Dict

import requests
import xbmc
from requests import HTTPError, Response

from . import utils
from .globals import G
from .model import AccountData, LoginError, ProfileData, CrunchyrollError
from ..modules import cloudscraper


class API:
    """Api documentation
    https://github.com/CloudMax94/crunchyroll-api/wiki/Api
    """
    # URL = "https://api.crunchyroll.com/"
    # VERSION = "1.1.21.0"
    # TOKEN = "LNDJgOit5yaRIWN"
    # DEVICE = "com.crunchyroll.windows.desktop"
    # TIMEOUT = 30

    # Dynamic client configuration loaded from latest.json
    CRUNCHYROLL_UA = ""
    UA_MOBILE = ""
    UA_ATV = ""

    # Authentication endpoints
    INDEX_ENDPOINT = "https://www.crunchyroll.com/index/v2"
    # Deprecated: old profile endpoint; use multiprofile endpoints below
    PROFILE_ENDPOINT = "https://www.crunchyroll.com/accounts/v1/me/profile"
    TOKEN_ENDPOINT = "https://www.crunchyroll.com/auth/v1/token"
    DEVICE_CODE_ENDPOINT = "https://www.crunchyroll.com/auth/v1/device/code"
    DEVICE_TOKEN_ENDPOINT = "https://www.crunchyroll.com/auth/v1/device/token"
    # Content and search endpoints
    # Discover/Search
    SEARCH_ENDPOINT = "https://www.crunchyroll.com/content/v2/discover/search"
    
    # Playback endpoints
    STREAMS_ENDPOINT = "https://beta-api.crunchyroll.com/cms/v2{}/videos/{}/streams"
    STREAMS_ENDPOINT_DRM = "https://www.crunchyroll.com/playback/v2/{}/tv/android_tv/play"
    # Fallback legacy phone playback endpoint (kept for compatibility)
    STREAMS_ENDPOINT_DRM_PHONE = "https://cr-play-service.prd.crunchyrollsvc.com/v1/{}/android/phone/play"
    STREAMS_ENDPOINT_CLEAR_STREAM = "https://cr-play-service.prd.crunchyrollsvc.com/v1/token/{}/{}"
    STREAMS_ENDPOINT_GET_ACTIVE_STREAMS = "https://cr-play-service.prd.crunchyrollsvc.com/playback/v1/sessions/streaming"
    # SERIES_ENDPOINT = "https://beta-api.crunchyroll.com/cms/v2{}/series/{}"
    SEASONS_ENDPOINT = "https://beta-api.crunchyroll.com/cms/v2{}/seasons"
    EPISODES_ENDPOINT = "https://beta-api.crunchyroll.com/cms/v2{}/episodes"
    OBJECTS_BY_ID_LIST_ENDPOINT = "https://beta-api.crunchyroll.com/content/v2/cms/objects/{}"
    # SIMILAR_ENDPOINT = "https://beta-api.crunchyroll.com/content/v1/{}/similar_to"
    # NEWSFEED_ENDPOINT = "https://beta-api.crunchyroll.com/content/v1/news_feed"
    BROWSE_ENDPOINT = "https://www.crunchyroll.com/content/v2/discover/browse"
    # there is also a v2, but that will only deliver content_ids and no details about the entries
    WATCHLIST_LIST_ENDPOINT = "https://www.crunchyroll.com/content/v2/discover/{}/watchlist"
    # only v2 will allow removal of watchlist entries.
    # !!!! be super careful and always provide a content_id, or it will delete the whole playlist! *sighs* !!!!
    # WATCHLIST_REMOVE_ENDPOINT = "https://beta-api.crunchyroll.com/content/v2/{}/watchlist/{}"
    WATCHLIST_V2_ENDPOINT = "https://beta-api.crunchyroll.com/content/v2/{}/watchlist"
    PLAYHEADS_ENDPOINT = "https://beta-api.crunchyroll.com/content/v2/{}/playheads"
    # Android TV clients post playheads to the www host
    PLAYHEADS_ENDPOINT_WWW = "https://www.crunchyroll.com/content/v2/{}/playheads"
    HISTORY_ENDPOINT = "https://www.crunchyroll.com/content/v2/{}/watch-history"
    RESUME_ENDPOINT = "https://www.crunchyroll.com/content/v2/discover/{}/history"
    SEASONAL_TAGS_ENDPOINT = "https://beta-api.crunchyroll.com/content/v2/discover/seasonal_tags"
    CATEGORIES_ENDPOINT = "https://beta-api.crunchyroll.com/content/v1/tenant_categories"
    SKIP_EVENTS_ENDPOINT = "https://static.crunchyroll.com/skip-events/production/{}.json"  # request w/o auth req.
    INTRO_V2_ENDPOINT = "https://static.crunchyroll.com/datalab-intro-v2/{}.json"

    CRUNCHYLISTS_LISTS_ENDPOINT = "https://beta-api.crunchyroll.com/content/v2/{}/custom-lists"
    CRUNCHYLISTS_VIEW_ENDPOINT = "https://beta-api.crunchyroll.com/content/v2/{}/custom-lists/{}"

    # Dynamic client credentials (loaded from latest.json)
    CLIENT_AUTH_B64 = ""
    CLIENT_AUTH_B64_DEVICE = ""
    CLIENT_AUTH_B64_MOBILE = ""
    APP_VERSION = ""
    APP_VERSION_ATV = ""
    APP_VERSION_MOBILE = ""
    
    # DRM endpoints
    LICENSE_ENDPOINT = "https://cr-license-proxy.prd.crunchyrollsvc.com/v1/license/widevine"

    # Multiprofile
    PROFILES_LIST_ENDPOINT = "https://www.crunchyroll.com/accounts/v1/{}/multiprofile"
    PROFILE_BY_ID_ENDPOINT = "https://www.crunchyroll.com/accounts/v1/{}/multiprofile/{}"
    STATIC_IMG_PROFILE = "https://static.crunchyroll.com/assets/avatar/170x170/"
    STATIC_WALLPAPER_PROFILE = "https://static.crunchyroll.com/assets/wallpaper/720x180/"

    def __init__(
            self,
            locale: str = "en-US"
    ) -> None:
        self.http = requests.Session()
        self.locale: str = locale
        self.account_data: AccountData = AccountData(dict())
        self.profile_data: ProfileData = ProfileData(dict())
        self.api_headers: Dict = default_request_headers()
        self.retry_counter = 0
        self.etp_anonymous_id: str = ""
        self.DEVICE_CLIENT_ID: str = ""
        self.DEVICE_CLIENT_SECRET: str = ""
        self.session_client: str = "unknown"  # 'device' or 'mobile'
        self.cf_cookie: str = ""
        self.last_request: Dict = {}
        # try to load dynamic client config
        try:
            self._load_client_config()
        except Exception:
            pass

    def start(self) -> None:
        session_restart = G.args.get_arg('session_restart', False)

        # restore account data from file (if any)
        account_data = self.account_data.load_from_storage()

        # restore profile data from file (if any)
        self.profile_data = ProfileData(self.profile_data.load_from_storage())

        if account_data and not session_restart:
            self.account_data = AccountData(account_data)
            account_auth = {"Authorization": f"{self.account_data.token_type} {self.account_data.access_token}"}
            self.api_headers.update(account_auth)

            # check if tokes are expired
            if get_date() > str_to_date(self.account_data.expires):
                session_restart = True
            else:
                return

        # session management
        if session_restart:
            try:
                self.create_session(action="refresh")
                # Check if refresh was successful
                if not self.account_data.access_token:
                    utils.crunchy_log("Refresh failed - access token is empty, will need device-code flow")
                    return
                utils.crunchy_log("Session refreshed successfully")
            except (LoginError, CrunchyrollError) as e:
                utils.crunchy_log(f"Session refresh failed: {e}, will need device-code flow")
                # Clear any partial session data
                self.account_data.delete_storage()
                self.account_data = AccountData({})
                return
            except Exception as e:
                utils.crunchy_log(f"Unexpected error during session refresh: {e}")
                self.account_data.delete_storage()
                self.account_data = AccountData({})
                return
        else:
            # No session yet; caller will decide login method (user/pass or device-code)
            return

    def create_session(self, action: str = "refresh", profile_id: Optional[str] = None) -> None:
        """Create/refresh a session.
        When action='login' we use the mobile client Basic auth with username/password (if provided).
        Otherwise we use device client for refresh and profile refresh.
        """
        headers = {}
        data = {}

        if action == "refresh":
            # Use device client to refresh a session
            self.session_client = 'device'
            headers = {"Authorization": f"Basic {API.CLIENT_AUTH_B64_DEVICE}"}
            data = {
                "refresh_token": self.account_data.refresh_token,
                "grant_type": "refresh_token",
                "scope": "offline_access",
                "device_id": G.args.device_id,
                "device_name": 'Kodi',
                "device_type": 'MediaCenter'
            }
        elif action == "login":
            # Username/password grant using MOBILE client (if supported)
            # NOTE: This may fail if password grants are disabled; callers will fallback to device-code.
            self.session_client = 'mobile'
            headers = {"Authorization": f"Basic {API.CLIENT_AUTH_B64_MOBILE or API.CLIENT_AUTH_B64}"}
            username = G.args.addon.getSetting("crunchyroll_username")
            password = G.args.addon.getSetting("crunchyroll_password")
            data = {
                "username": username,
                "password": password,
                "grant_type": "password",
                "scope": "offline_access",
                "device_id": G.args.device_id,
                "device_name": 'Kodi',
                "device_type": 'MediaCenter'
            }
        elif action == "refresh_profile":
            self.session_client = 'device'
            headers = {"Authorization": f"Basic {API.CLIENT_AUTH_B64_DEVICE}"}
            data = {
                "device_id": G.args.device_id,
                "device_name": 'Kodi',
                "device_type": "MediaCenter",
                "grant_type": "refresh_token_profile_id",
                "profile_id": profile_id,
                "refresh_token": self.account_data.refresh_token
            }

        # Always use cloudscraper for token requests (CF by default)
        scraper = cloudscraper.create_scraper(delay=10, browser={'custom': API.UA_ATV or API.CRUNCHYROLL_UA})
        r = scraper.post(
            url=API.TOKEN_ENDPOINT,
            headers=headers,
            data=data,
            timeout=20
        )
        try:
            self._update_cookie_from_scraper(scraper)
        except Exception:
            pass

        # if refreshing and refresh token is expired, it will throw a 400
        # clear session data and let caller handle re-authentication
        if r.status_code == 400:
            utils.crunchy_log("Invalid/Expired credentials - refresh token is dead")
            self.retry_counter = self.retry_counter + 1
            
            # Clear session data
            self.account_data.delete_storage()
            self.account_data = AccountData({})
            
            if self.retry_counter > 2:
                utils.crunchy_log("Max retries exceeded. Aborting!", xbmc.LOGERROR)
                raise LoginError("Failed to authenticate twice")
            
            # Don't retry here - let start() handle the re-authentication
            utils.crunchy_log("Session cleared - will trigger device-code flow")
            return

        if r.status_code == 403:
            utils.crunchy_log("Cloudflare blocked token request", xbmc.LOGERROR)
            raise LoginError("Failed to bypass cloudflare")

        r_json = get_json_from_response(r)

        # Build account/profile and persist session
        self._finalize_session_from_token_response(r_json)

        if action == "refresh_profile":
            # fetch all profiles from API
            r = self.make_request(
                method="GET",
                url=self.PROFILES_LIST_ENDPOINT.format(self.account_data.account_id),
            )

            # Extract current profile data as dict from ProfileData obj
            profile_data = vars(self.profile_data)

            # Update extracted profile data with fresh data from API for requested profile_id
            profile_data.update(
                next(profile for profile in r.get("profiles") if profile["profile_id"] == profile_id)
            )

            # update our ProfileData obj with updated data
            self.profile_data = ProfileData(profile_data)

            # cache to file
            self.profile_data.write_to_storage()

        # reset consecutive retry counter after a successful call
        self.retry_counter = 0

    def _load_client_config(self) -> None:
        """Load dynamic client configuration from latest.json setting."""
        latest_url = G.args.addon.getSetting("latest_json_url") or "https://reroll.is-cool.dev/latest.json"
        utils.crunchy_log(f"Loading client config from: {latest_url}")
        
        try:
            resp = self.http.get(latest_url, timeout=10)
            if resp.ok:
                cfg = resp.json()
                utils.crunchy_log("Successfully loaded client configuration")
                
                # Load Android TV configuration
                android_tv = cfg.get("android-tv", {})
                if android_tv:
                    API.CLIENT_AUTH_B64_DEVICE = android_tv.get("auth", API.CLIENT_AUTH_B64_DEVICE)
                    API.UA_ATV = android_tv.get("user-agent", API.UA_ATV)
                    API.APP_VERSION_ATV = android_tv.get("app-version", API.APP_VERSION_ATV)
                    utils.crunchy_log("Loaded Android TV client configuration")

                # Load mobile configuration
                mobile = cfg.get("mobile", {})
                if mobile:
                    API.CLIENT_AUTH_B64_MOBILE = mobile.get("auth", API.CLIENT_AUTH_B64_MOBILE)
                    API.UA_MOBILE = mobile.get("user-agent", API.UA_MOBILE)
                    API.APP_VERSION_MOBILE = mobile.get("app-version", API.APP_VERSION_MOBILE)
                    utils.crunchy_log("Loaded mobile client configuration")

                # Backwards compatibility with flat structure
                if not android_tv and not mobile:
                    API.CLIENT_AUTH_B64 = cfg.get("auth", API.CLIENT_AUTH_B64)
                    API.UA_MOBILE = cfg.get("user-agent", API.UA_MOBILE)
                    API.APP_VERSION_MOBILE = cfg.get("app-version", API.APP_VERSION_MOBILE)
                    utils.crunchy_log("Using legacy flat configuration structure")

                # Set legacy attributes for backwards compatibility
                API.CRUNCHYROLL_UA = API.UA_MOBILE or API.CRUNCHYROLL_UA
                API.APP_VERSION = API.APP_VERSION_MOBILE or API.APP_VERSION

                # Parse device client credentials from base64 (for Android TV auth)
                if API.CLIENT_AUTH_B64_DEVICE:
                    try:
                        import base64
                        decoded = base64.b64decode(API.CLIENT_AUTH_B64_DEVICE).decode('utf-8')
                        client_id, client_secret = decoded.split(":", 1)
                        self.DEVICE_CLIENT_ID = client_id
                        self.DEVICE_CLIENT_SECRET = client_secret
                        utils.crunchy_log("Parsed Android TV device client credentials")
                    except (ValueError, Exception) as e:
                        utils.crunchy_log(f"Failed to parse device credentials: {e}")
            else:
                utils.crunchy_log(f"Failed to load client config: HTTP {resp.status_code}")
        except Exception as e:
            utils.crunchy_log(f"Error loading client config: {e}")

        # Update default headers with new user agent
        self.api_headers = default_request_headers()

    def init_cf_cookie(self) -> None:
        """Trigger a 401 on content endpoint to obtain __cf_bm cookie."""
        try:
            scraper = cloudscraper.create_scraper(delay=10, browser={'custom': API.UA_ATV or API.CRUNCHYROLL_UA})
            resp = scraper.get(
                "https://www.crunchyroll.com/content/v2/discover/browse",
                params={"locale": "en-US", "sort_by": "popularity", "n": 1},
                headers={
                    "Authorization": "Bearer",
                    "Accept": "application/json",
                    "Accept-Charset": "UTF-8",
                    "Content-Type": "application/x-www-form-urlencoded"
                },
                timeout=10
            )
            self._update_cookie_from_scraper(scraper)
        except requests.exceptions.RequestException:
            pass

    def acquire_anonymous_token(self) -> Optional[Dict]:
        """Acquire anonymous access token (not used for content, helps establish session)."""
        import uuid
        self.etp_anonymous_id = str(uuid.uuid4())
        try:
            scraper = cloudscraper.create_scraper(delay=10, browser={'custom': API.UA_ATV or API.CRUNCHYROLL_UA})
            r = scraper.post(
                    API.TOKEN_ENDPOINT,
                    headers={
                        "ETP-Anonymous-ID": self.etp_anonymous_id,
                        "Accept": "application/json",
                        "Accept-Charset": "UTF-8",
                        "Content-Type": "application/x-www-form-urlencoded"
                    },
                    data={
                        "grant_type": "client_id",
                        "scope": "offline_access",
                        "client_id": self.DEVICE_CLIENT_ID,
                        "client_secret": self.DEVICE_CLIENT_SECRET
                    },
                    timeout=15
                )
            if r.ok:
                return r.json()
        except requests.exceptions.RequestException:
            pass
        return None

    def request_device_code(self) -> Optional[Dict]:
        """Request device code for Android TV activation."""
        try:
            utils.crunchy_log(f"Requesting device code with Android TV client auth: {API.CLIENT_AUTH_B64_DEVICE[:20]}...")
            scraper = cloudscraper.create_scraper(delay=10, browser={'custom': API.UA_ATV or API.CRUNCHYROLL_UA})
            r = scraper.post(
                API.DEVICE_CODE_ENDPOINT,
                headers={
                    "Authorization": f"Basic {API.CLIENT_AUTH_B64_DEVICE}",
                    "Accept": "application/json",
                    "Accept-Charset": "UTF-8",
                    "Content-Type": "application/x-www-form-urlencoded"
                },
                data={},
                timeout=15
            )
            if r.ok:
                self._update_cookie_from_scraper(scraper)
                return r.json()
        except requests.exceptions.RequestException:
            pass
        return None

    def poll_device_token(self, device_code: str) -> Optional[Dict]:
        """Poll for device token until activation occurs."""
        try:
            scraper = cloudscraper.create_scraper(delay=10, browser={'custom': API.UA_ATV or API.CRUNCHYROLL_UA})
            r = scraper.post(
                    API.DEVICE_TOKEN_ENDPOINT,
                    headers={
                        "Authorization": f"Basic {API.CLIENT_AUTH_B64_DEVICE}",
                        "Accept": "application/json",
                        "Accept-Charset": "UTF-8",
                        "Content-Type": "application/json"
                    },
                    json={"device_code": device_code},
                    timeout=15
                )
            if r.ok:
                self.session_client = 'device'
                self._update_cookie_from_scraper(scraper)
                return r.json()
        except requests.exceptions.RequestException:
            pass
        return None

    def _finalize_session_from_token_response(self, r_json: Dict) -> None:
        """Build session/account data from token response and fetch profile/index."""
        access_token = r_json.get("access_token")
        token_type = r_json.get("token_type", "Bearer")
        account_auth = {"Authorization": f"{token_type} {access_token}"}

        account_data = dict()
        account_data.update(r_json)
        self.account_data = AccountData({})
        # switch UA based on session client
        if self.session_client == 'device' and API.UA_ATV:
            API.CRUNCHYROLL_UA = API.UA_ATV
        elif API.UA_MOBILE:
            API.CRUNCHYROLL_UA = API.UA_MOBILE

        self.api_headers = default_request_headers()
        self.api_headers.update(account_auth)
        if r_json.get("expires_in"):
            account_data["expires"] = date_to_str(
                get_date() + timedelta(seconds=float(account_data["expires_in"])) )

        r = self.make_request(
            method="GET",
            url=API.INDEX_ENDPOINT
        )
        account_data.update(r)

        # Fetch profiles via multiprofile list on www host and select the active profile
        try:
            profiles_resp = self.make_request(
                method="GET",
                url=API.PROFILES_LIST_ENDPOINT.format(account_data.get("account_id"))
            )
            if profiles_resp and profiles_resp.get("profiles"):
                # Pick selected profile or the first
                profiles = profiles_resp.get("profiles")
                selected = next((p for p in profiles if p.get("is_selected")), profiles[0])
                # Also fetch full profile-by-id to get extra fields if available
                try:
                    profile_full = self.make_request(
                        method="GET",
                        url=API.PROFILE_BY_ID_ENDPOINT.format(account_data.get("account_id"), selected.get("profile_id"))
                    ) or {}
                except Exception:
                    profile_full = {}
                # Merge profile info into account_data-like fields
                merged_profile = {**selected, **profile_full}
                account_data.update({
                    "preferred_communication_language": merged_profile.get("preferred_communication_language"),
                    "preferred_content_audio_language": merged_profile.get("preferred_content_audio_language"),
                    "preferred_content_subtitle_language": merged_profile.get("preferred_content_subtitle_language"),
                    "maturity_rating": merged_profile.get("maturity_rating"),
                    "username": merged_profile.get("username"),
                    "email": merged_profile.get("email"),
                    "avatar": merged_profile.get("avatar"),
                })
                # Persist ProfileData separately
                from .model import ProfileData as _ProfileData
                self.profile_data = _ProfileData(merged_profile)
                self.profile_data.write_to_storage()
        except Exception:
            pass

        self.account_data = AccountData(account_data)
        self.account_data.write_to_storage()

    def close(self) -> None:
        """Saves cookies and session
        """
        # no longer required, data is saved upon session update already

    def destroy(self) -> None:
        """Destroys session
        """
        self.account_data.delete_storage()
        self.profile_data.delete_storage()

    def make_request(
            self,
            method: str,
            url: str,
            headers=None,
            params=None,
            data=None,
            json_data=None,
            is_retry=False,
    ) -> Optional[Dict]:
        if params is None:
            params = dict()
        if headers is None:
            headers = dict()
        if self.account_data and ("/cms/" in url):
            if expiration := self.account_data.expires:
                current_time = get_date()
                if current_time > str_to_date(expiration):
                    utils.crunchy_log("make_request_proposal: session renewal due to expired token", xbmc.LOGINFO)
                    self.create_session(action="refresh")
            params.update({
                "Policy": self.account_data.cms.policy,
                "Signature": self.account_data.cms.signature,
                "Key-Pair-Id": self.account_data.cms.key_pair_id
            })
        request_headers = {}
        request_headers.update(self.api_headers)
        request_headers.update(headers)

        # ensure UA reflects active session; use ATV UA for ATV playback endpoint
        if "playback/v2" in url and API.UA_ATV:
            request_headers["User-Agent"] = API.UA_ATV
        else:
            request_headers["User-Agent"] = API.CRUNCHYROLL_UA
        # Route all www requests through cloudscraper (CF by default)
        if url.startswith("https://www.crunchyroll.com"):
            try:
                scraper = cloudscraper.create_scraper(delay=10, browser={'custom': API.UA_ATV or API.CRUNCHYROLL_UA})
                if getattr(self, 'cf_cookie', None):
                    request_headers["Cookie"] = self.cf_cookie
                r = scraper.request(
                    method=method,
                    url=url,
                    headers=request_headers,
                    params=params,
                    data=data if json_data is None else None,
                    json=json_data,
                    timeout=20
                )
                try:
                    self._update_cookie_from_scraper(scraper)
                except Exception:
                    pass
            except requests.exceptions.RequestException as _e:
                raise CrunchyrollError(f"Request failed: {_e}")
        else:
            r = self.http.request(
                method,
                url,
                headers=request_headers,
                params=params,
                data=data,
                json=json_data
            )

        # record last request info for debugging
        try:
            self.last_request = {
                'method': method,
                'url': url,
                'status': r.status_code,
                'error': None if r.ok else r.reason
            }
        except Exception:
            pass

        # something went wrong with authentication, possibly an expired token that wasn't caught above due to host
        # clock issues. set expiration date to 0 and re-call, triggering a full session refresh.
        if r.status_code == 401:
            if is_retry:
                raise LoginError('Request to API failed twice due to authentication issues.')

            utils.crunchy_log("make_request_proposal: request failed due to auth error", xbmc.LOGERROR)
            self.account_data.expires = date_to_str(get_date() - timedelta(seconds=1))
            return self.make_request(method, url, headers, params, data, json_data, True)

        return get_json_from_response(r)

    def request_playback_v2(self, episode_id: str, audio: Optional[str] = None, queue: bool = False) -> Optional[Dict]:
        """Call the Android TV playback v2 endpoint using cloudscraper."""
        try:
            scraper = cloudscraper.create_scraper(delay=10, browser={'custom': API.UA_ATV or API.CRUNCHYROLL_UA})
            params = {"queue": str(queue).lower()}
            if audio:
                params["audio"] = audio
            r = scraper.get(
                API.STREAMS_ENDPOINT_DRM.format(episode_id),
                headers={
                    "Authorization": self.api_headers.get("Authorization", ""),
                    "Accept": "application/json",
                    "Accept-Charset": "UTF-8",
                    "x-cr-stream-limits": "true",
                    "Cookie": self.cf_cookie
                },
                params=params,
                timeout=20
            )
            try:
                self.last_request = {
                    'method': 'GET',
                    'url': API.STREAMS_ENDPOINT_DRM.format(episode_id),
                    'status': r.status_code,
                    'error': None if r.ok else r.reason
                }
            except Exception:
                pass
            if r.ok:
                self._update_cookie_from_scraper(scraper)
                return r.json()
        except requests.exceptions.RequestException:
            pass
        return None

    def request_playback_phone(self, episode_id: str) -> Optional[Dict]:
        """Fallback to legacy phone playback endpoint when ATV playback v2 fails."""
        try:
            return self.make_request(
                method="GET",
                url=API.STREAMS_ENDPOINT_DRM_PHONE.format(episode_id)
            )
        except Exception:
            return None

    def _update_cookie_from_scraper(self, scraper) -> None:
        try:
            # build cookie string for www.crunchyroll.com
            cookie_names = [
                '__cf_bm', 'SSID_GuUe', 'SSSC_GuUe', 'SSRT_GuUe', 'cr_exp'
            ]
            parts = []
            for name in cookie_names:
                val = scraper.cookies.get(name)
                if val:
                    parts.append(f"{name}={val}")
            if parts:
                self.cf_cookie = "; ".join(parts)
        except Exception:
            pass

    def make_unauthenticated_request(
            self,
            method: str,
            url: str,
            headers=None,
            params=None,
            data=None,
            json_data=None,
    ) -> Optional[Dict]:
        """ Send a raw request without any session information """

        req = requests.Request(method, url, data=data, params=params, headers=headers, json=json_data)
        prepped = req.prepare()
        r = self.http.send(prepped)

        return get_json_from_response(r)


def default_request_headers() -> Dict:
    """Default headers for general API requests (content, navigation, etc.) using mobile client."""
    headers = {
        # Select a sane default UA (mobile) for general API requests.
        "User-Agent": API.CRUNCHYROLL_UA,
        "Accept": "application/json",
           "Content-Type": "application/x-www-form-urlencoded",
           "Accept-Charset": "UTF-8"
    }
    
    # Add mobile client basic auth for general API requests
    if API.CLIENT_AUTH_B64_MOBILE:
        headers["Authorization"] = f"Basic {API.CLIENT_AUTH_B64_MOBILE}"
    
    return headers


def get_date() -> datetime:
    return datetime.utcnow()


def date_to_str(date: datetime) -> str:
    return "{}-{}-{}T{}:{}:{}Z".format(
        date.year, date.month,
        date.day, date.hour,
        date.minute, date.second
    )


def str_to_date(string: str) -> datetime:
    time_format = "%Y-%m-%dT%H:%M:%SZ"

    try:
        res = datetime.strptime(string, time_format)
    except TypeError:
        res = datetime(*(time.strptime(string, time_format)[0:6]))

    return res


def get_json_from_response(r: Response) -> Optional[Dict]:
    from .utils import log_error_with_trace
    from .model import CrunchyrollError

    code: int = r.status_code
    response_type: str = r.headers.get("Content-Type")
    # no content - possibly POST/DELETE request?
    if not r or not r.text:
        try:
            r.raise_for_status()
            return None
        except HTTPError as e:
            # r.text is empty when status code cause raise
            r = e.response

    # handle text/plain response (e.g. fetch subtitle)
    if response_type == "text/plain":
        # if encoding is not provided in the response, Requests will make an educated guess and very likely fail
        # messing encoding up - which did cost me hours. We will always receive utf-8 from crunchy, so enforce that
        r.encoding = "utf-8"
        d = dict()
        d.update({
            'data': r.text
        })
        return d

    if not r.ok and r.text[0] != "{":
        raise CrunchyrollError(f"[{code}] {r.text}")

    try:
        r_json: Dict = r.json()
    except requests.exceptions.JSONDecodeError:
        log_error_with_trace("Failed to parse response data")
        return None

    if "error" in r_json:
        error_code = r_json.get("error")
        # only password grant failures should surface as LoginError here
        if error_code == "invalid_grant":
            raise LoginError(f"[{code}] Invalid login credentials.")
    elif "message" in r_json and "code" in r_json:
        message = r_json.get("message")
        raise CrunchyrollError(f"[{code}] Error occurred: {message}")
    if not r.ok:
        # do not map general errors to LoginError here; callers decide based on status
        raise CrunchyrollError(f"[{code}] {r.text}")

    return r_json
