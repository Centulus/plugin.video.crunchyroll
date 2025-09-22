# -*- coding: utf-8 -*-
# Crunchyroll
# Copyright (C) 2018 MrKrabat
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

import secrets
import re

import xbmc
import xbmcaddon
import xbmcgui
import xbmcplugin

from . import controller
from . import utils
from . import view
from .globals import G
from .model import CrunchyrollError, LoginError


def main(argv):
    """Main function for the addon
    """

    G.init(argv)

    # inputstream adaptive settings
    if G.args.get_arg('mode') == "hls":
        from inputstreamhelper import Helper  # noqa
        is_helper = Helper("hls")
        if is_helper.check_inputstream():
            xbmcaddon.Addon(id="inputstream.adaptive").openSettings()
        return True

    # remove legacy credential gating; we no longer use username/password
    G.args._device_id = G.args.addon.getSetting("device_id")
    if not G.args.device_id:
        # Generate a stable but hard-to-guess device id.
        # Keep a readable pattern while using cryptographically secure randomness.
        def _rand(n: int) -> str:
            alphabet = "0123456789abcdefghijklmnopqrstuvwxyz"
            return "".join(secrets.choice(alphabet) for _ in range(n))

        G.args._device_id = f"{_rand(8)}-KODI-{_rand(4)}-{_rand(4)}-{_rand(12)}"
        G.args.addon.setSetting("device_id", G.args.device_id)

    # get subtitle language
    G.args._subtitle = G.args.addon.getSetting("subtitle_language")
    G.args._subtitle_fallback = G.args.addon.getSetting("subtitle_language_fallback")  # @todo: test with empty

    # temporary dialog to notify about subtitle settings change
    # @todo: remove eventually
    if G.args.subtitle is int or G.args.subtitle_fallback is int or re.match("^([0-9]+)$", G.args.subtitle):
        xbmcgui.Dialog().notification(
            '%s INFO' % G.args.addon_name,
            'Language settings have changed. Please adjust settings.',
            xbmcgui.NOTIFICATION_INFO,
            10
        )

    # login/session init
    try:
        G.api.start()
        
        # If we're explicitly navigating to the retry screen, do not start device-code login again.
        # Render the retry listing instead.
        requested_mode = G.args.get_arg('mode')
        if requested_mode == 'activation_retry' and not G.api.account_data.access_token:
            return check_mode()
        
        if not G.api.account_data.access_token:
            # Hybrid auth: try username/password first (mobile client), then fallback to device-code.
            username = G.args.addon.getSetting("crunchyroll_username")
            password = G.args.addon.getSetting("crunchyroll_password")

            tried_password = False
            if username and password:
                try:
                    utils.crunchy_log("Attempting username/password login (mobile client)")
                    G.api.create_session(action="login")
                    tried_password = True
                except LoginError:
                    utils.crunchy_log("Password login failed, will fallback to device-code")
                except Exception as e:
                    utils.crunchy_log(f"Unexpected error during password login: {e}")

            if not G.api.account_data.access_token:
                # begin device code flow (preferred when no creds or creds fail)
                from .gui import ActivationDialog

                # init cloudflare cookie and anonymous token for www.crunchyroll.com
                G.api.init_cf_cookie()
                G.api.acquire_anonymous_token()

                # Keep the container open; we'll render the retry listing later in this invocation.

                device = G.api.request_device_code()
                if not device:
                    raise LoginError("Failed to request device code")

                user_code = device.get("user_code", "------").upper()
                device_code = device.get("device_code")
                interval_ms = int(device.get("interval", 500))  # milliseconds
                expires_in = int(device.get("expires_in", 300))  # seconds

                qr_url = f"https://crunchyroll.com/activate?code={user_code}&device=Android%20TV"
                info_text = f"1. Go to https://crunchyroll.com/activate\n2. Enter code: {user_code}\n3. Or scan the QR code below"

                dialog = ActivationDialog('plugin-video-crunchyroll-activation.xml', G.args.addon.getAddonInfo('path'), 'default', '1080i', 
                                        code=user_code, qr_url=qr_url, info=info_text, 
                                        expires_in=expires_in, interval_ms=interval_ms,
                                        device_code=device_code, api_instance=G.api)
                dialog.show()
                # Make sure the timer thread is stopped on shutdown as a last resort
                try:
                    if getattr(G, 'shutdown', None):
                        def _cleanup_activation_dialog():
                            try:
                                if hasattr(dialog, 'stop_timer'):
                                    dialog.stop_timer(timeout=1.0)
                            except Exception:
                                pass
                            try:
                                dialog.close()
                            except Exception:
                                pass
                        G.shutdown.register('activation_dialog', _cleanup_activation_dialog)
                except Exception:
                    pass
                from . import utils as _utils
                _utils.crunchy_log("Activation dialog shown", xbmc.LOGINFO)

                import time as _t
                start_ts = _t.time()
                expirations = 0  # count consecutive expirations
                user_cancelled = False
                show_retry_listing = False  # after 3 expirations, return to empty menu with a Retry folder
                try:
                    # Loop until user authenticates or cancels; expiry handled here (no GUI ops from timer)
                    while True:
                        # 1) If user canceled (Back), exit immediately; do not reopen or regenerate
                        if getattr(dialog, 'canceled', False):
                            user_cancelled = True
                            break

                        # 2) Handle expiry (timer sets flag and stops)
                        if getattr(dialog, 'expired', False):
                            try:
                                expirations += 1
                                if expirations >= 3:
                                    # After 3 timeouts, stop here and return to an empty listing with a Retry folder.
                                    show_retry_listing = True
                                    # ensure timer is stopped and exit loop to render listing
                                    try:
                                        if hasattr(dialog, 'stop_timer'):
                                            dialog.stop_timer()
                                    except Exception:
                                        pass
                                    break
                                else:
                                    _utils.crunchy_log("Activation expired - regenerating code (main loop)", xbmc.LOGINFO)
                                
                                # Request/refresh device_code when either <3 expirations or after Retry
                                device = G.api.request_device_code()
                                if device:
                                    user_code = device.get("user_code", "------").upper()
                                    device_code = device.get("device_code")
                                    interval_ms = int(device.get("interval", 500))
                                    expires_in = int(device.get("expires_in", 300))
                                    qr_url = f"https://crunchyroll.com/activate?code={user_code}&device=Android%20TV"

                                    # Update dialog and restart timer
                                    try:
                                        dialog.update_activation(user_code, device_code, expires_in, interval_ms, qr_url)
                                    except Exception:
                                        pass
                                    dialog.start_timer()
                                    start_ts = _t.time()
                                    continue
                                else:
                                    xbmcgui.Dialog().notification(G.args.addon_name, 'Activation expired. Please try again.', xbmcgui.NOTIFICATION_INFO, 5)
                                    try:
                                        if hasattr(dialog, 'stop_timer'):
                                            dialog.stop_timer()
                                        dialog.close()
                                    except Exception:
                                        pass
                                    return False
                            except Exception:
                                pass

                        # 3) If dialog stopped running and not due to expiry/cancel, try to recover by reopening it
                        if (not getattr(dialog, 'is_running', True)
                                and not getattr(dialog, 'expired', False)
                                and not getattr(dialog, 'canceled', False)):
                            _utils.crunchy_log("Activation dialog closed unexpectedly - reopening", xbmc.LOGWARNING)
                            try:
                                # Ensure any timer from old instance is stopped
                                if hasattr(dialog, 'stop_timer'):
                                    dialog.stop_timer()
                            except Exception:
                                pass
                            try:
                                # Re-create dialog with current activation data
                                info_text = f"1. Go to https://crunchyroll.com/activate\n2. Enter code: {user_code}\n3. Or scan the QR code below"
                                dialog = ActivationDialog('plugin-video-crunchyroll-activation.xml', G.args.addon.getAddonInfo('path'), 'default', '1080i',
                                                         code=user_code, qr_url=qr_url, info=info_text,
                                                         expires_in=expires_in, interval_ms=interval_ms,
                                                         device_code=device_code, api_instance=G.api)
                                dialog.show()
                                dialog.start_timer()
                                _utils.crunchy_log("Activation dialog re-opened", xbmc.LOGINFO)
                                # Small delay to let UI settle (abort-aware)
                                try:
                                    _monitor = xbmc.Monitor()
                                    _monitor.waitForAbort(0.1)
                                except Exception:
                                    pass
                                continue
                            except Exception as _re_err:
                                _utils.crunchy_log(f"Failed to reopen activation dialog: {_re_err}", xbmc.LOGERROR)
                                user_cancelled = True
                                break

                        # Use the current device_code from dialog (may be updated after regen)
                        current_device_code = getattr(dialog, 'device_code', device_code)
                        # No inactive/pause mode; proceed to poll
                        token = None
                        try:
                            token = G.api.poll_device_token(current_device_code)
                        except Exception as _poll_err:
                            _utils.crunchy_log(f"Poll error (ignored): {_poll_err}", xbmc.LOGWARNING)
                            # brief wait to avoid tight loop in case of repeated errors
                            _monitor = xbmc.Monitor()
                            if _monitor.waitForAbort(0.25):
                                user_cancelled = True
                                break
                        if token and token.get('access_token'):
                            # finalize session then reload addon root to render fresh UI
                            G.api._finalize_session_from_token_response(token)
                            # Ensure dialog thread is stopped and dialog is closed before returning
                            try:
                                if hasattr(dialog, 'stop_timer'):
                                    dialog.stop_timer()
                                dialog.close()
                            except Exception:
                                pass
                            try:
                                xbmc.executebuiltin(f"Container.Update({G.args.addonurl}, replace)")
                            except Exception:
                                pass
                            return True
                        # Use the dialog's current interval if it was regenerated, else fallback to the initial one
                        # Use abort-aware wait instead of blocking sleep to keep UI responsive
                        sleep_ms = getattr(dialog, 'interval_ms', interval_ms)
                        _monitor = xbmc.Monitor()
                        if _monitor.waitForAbort(max(0.001, float(sleep_ms) / 1000.0)):
                            # Abort requested by Kodi (shutdown); exit cleanly
                            user_cancelled = True
                            break
                finally:
                    try:
                        # Safe thread shutdown to prevent PyTuple_Resize crashes
                        if hasattr(dialog, 'stop_timer'):
                            dialog.stop_timer(timeout=5.0)  # Longer timeout for safety
                        
                        # Close dialog safely
                        if hasattr(dialog, 'close'):
                            dialog.close()
                    except Exception as cleanup_error:
                        # Log cleanup errors but don't propagate them
                        try:
                            _utils.crunchy_log(f"Dialog cleanup error: {cleanup_error}", xbmc.LOGWARNING)
                        except Exception:
                            pass  # Even logging can fail during shutdown

                # If we decided to show the retry listing, render it now and stop.
                if show_retry_listing and not G.api.account_data.access_token:
                    # Render the listing directly to avoid re-triggering activation flow
                    try:
                        handle = int(G.args.argv[1])
                        try:
                            xbmcplugin.setContent(handle, "files")
                        except Exception:
                            pass
                        li = xbmcgui.ListItem(label="Retry activation")
                        try:
                            li.setLabel2("Restart activation and get a new code")
                        except Exception:
                            pass
                        try:
                            li.setInfo('video', {'plot': 'Restart the activation flow to get a new QR code and activation code.'})
                        except Exception:
                            pass
                        # Clicking this non-folder item will run the plugin; handler will update the container to root
                        url = f"{G.args.addonurl}?mode=activation_retry_start"
                        # Non-folder item prevents Kodi auto-enter and ensures visibility
                        xbmcplugin.addDirectoryItem(handle=handle, url=url, listitem=li, isFolder=False, totalItems=1)
                        xbmcplugin.addSortMethod(handle, xbmcplugin.SORT_METHOD_NONE)
                        xbmcplugin.endOfDirectory(handle=handle, updateListing=False, cacheToDisc=False)
                    except Exception:
                        pass
                    return True

                # If user cancelled, just exit cleanly; listing was already ended before dialog
                if user_cancelled and not G.api.account_data.access_token:
                    return False
            # After activation flow, ensure we don’t proceed without a valid access token
            if not G.api.account_data.access_token:
                return False

        # request to select profile if not set already
        if G.api.profile_data.profile_id is None:
            controller.show_profiles()

        # If a previous step triggered a Container.Update (e.g., after profile selection),
        # skip building any listing in this invocation to avoid spinner/race conditions.
        if getattr(G.args, '_redirected', False):
            return True

        xbmcplugin.setContent(int(G.args.argv[1]), "tvshows")
        return check_mode()
    except LoginError:
        utils.crunchy_log("Login failed", xbmc.LOGERROR)
        view.add_item({"title": G.args.addon.getLocalizedString(30060)})
        view.end_of_directory()
        xbmcgui.Dialog().ok(G.args.addon_name, G.args.addon.getLocalizedString(30060))
        return False
    except CrunchyrollError as e:
        try:
            utils.crunchy_log(f"Request failed: {e}; last_request={getattr(G.api, 'last_request', {})}", xbmc.LOGERROR)
        except Exception:
            utils.crunchy_log(f"Request failed: {e}", xbmc.LOGERROR)
        view.add_item({"title": G.args.addon.getLocalizedString(30061)})
        view.end_of_directory()
        xbmcgui.Dialog().notification(G.args.addon_name, G.args.addon.getLocalizedString(30061), xbmcgui.NOTIFICATION_ERROR, 4)
        return False


def check_mode():
    """Run mode-specific functions
    """
    if G.args.get_arg('mode'):
        mode = G.args.get_arg('mode')
    elif G.args.get_arg('id'):
        # call from other plugin
        mode = "videoplay"
        G.args.set_arg('url', "/media-" + G.args.get_arg('id'))
    elif G.args.get_arg('url'):
        # call from other plugin
        mode = "videoplay"
        G.args.set_arg('url', G.args.get_arg('url')[26:])  # @todo: does this actually work? truncated?
    else:
        mode = None

    if not mode:
        show_main_menu()

    elif mode == "queue":
        controller.show_queue()
    elif mode == "search":
        controller.search_anime()
    elif mode == "history":
        controller.show_history()
    elif mode == "resume":
        controller.show_resume_episodes()
    # elif mode == "random":
    #     controller.showRandom()

    elif mode == "anime":
        show_main_category("anime")
    elif mode == "drama":
        show_main_category("drama")

    # elif mode == "featured":  # https://www.crunchyroll.com/content/v2/discover/account_id/home_feed -> hero_carousel ?
    #     controller.list_series("featured", api)
    elif mode == "popular":  # DONE
        controller.list_filter()
    # elif mode == "simulcast":  # https://www.crunchyroll.com/de/simulcasts/seasons/fall-2023 ???
    #     controller.listSeries("simulcast", api)
    # elif mode == "updated":
    #    controller.listSeries("updated", api)
    elif mode == "newest":
        controller.list_filter()
    elif mode == "alpha":
        controller.list_filter()
    elif mode == "season":  # DONE
        controller.list_anime_seasons()
    elif mode == "genre":  # DONE
        controller.list_filter()

    elif mode == "seasons":
        controller.view_season()
    elif mode == "episodes":
        controller.view_episodes()
    elif mode == "videoplay":
        controller.start_playback()
    elif mode == "add_to_queue":
        controller.add_to_queue()
    # elif mode == "remove_from_queue":
    #     controller.remove_from_queue()
    elif mode == "crunchylists_lists":
        controller.crunchylists_lists()
    elif mode == 'crunchylists_item':
        controller.crunchylists_item()
    elif mode == 'profiles_list':
        controller.show_profiles()
    elif mode == 'activation_retry':
        # Render a simple directory with a single non-folder item to retry activation
        try:
            handle = int(G.args.argv[1])
            try:
                xbmcplugin.setContent(handle, "files")
            except Exception:
                pass
            li = xbmcgui.ListItem(label="Retry activation")
            # Brief info so users know what this does
            try:
                li.setLabel2("Restart activation and get a new code")
            except Exception:
                pass
            try:
                li.setInfo('video', {'plot': 'Restart the activation flow to get a new QR code and activation code.'})
            except Exception:
                pass
            # Clicking this non-folder item will run the plugin; handler will update the container to root
            url = f"{G.args.addonurl}?mode=activation_retry_start"
            xbmcplugin.addDirectoryItem(handle=handle, url=url, listitem=li, isFolder=False, totalItems=1)
            xbmcplugin.addSortMethod(handle, xbmcplugin.SORT_METHOD_NONE)
            xbmcplugin.endOfDirectory(handle=handle, updateListing=False, cacheToDisc=False)
        except Exception as _retry_err:
            try:
                xbmc.log(f"[Crunchyroll] Failed to render activation_retry listing: {_retry_err}", xbmc.LOGERROR)
            except Exception:
                pass
        return True
    elif mode == 'activation_retry_start':
        # Switch the container to the addon root so the retry menu disappears, then let main() handle activation.
        try:
            xbmc.executebuiltin(f"Container.Update({G.args.addonurl})")
        except Exception:
            pass
        return True
    else:
        # unknown mode
        utils.crunchy_log("Failed in check_mode '%s'" % str(mode), xbmc.LOGERROR)
        xbmcgui.Dialog().notification(
            G.args.addon_name,
            G.args.addon.getLocalizedString(30061),
            xbmcgui.NOTIFICATION_ERROR
        )
        show_main_menu()


def show_main_menu():
    """Show main menu
    """
    # Replace legacy 'Queue' with 'Watchlist'
    view.add_item({"title": G.args.addon.getLocalizedString(30096),
                   "mode": "queue"})
    view.add_item({"title": G.args.addon.getLocalizedString(30047),
                   "mode": "resume"})
    # (Removed duplicate Watchlist entry that was placed after Resume)
    view.add_item({"title": G.args.addon.getLocalizedString(30041),
                   "mode": "search"})
    view.add_item({"title": G.args.addon.getLocalizedString(30042),
                   "mode": "history"})
    # #view.add_item(args,
    # #              {"title": G.args.addon.getLocalizedString(30043),
    # #               "mode":  "random"})
    view.add_item({"title": G.args.addon.getLocalizedString(30050),
                   "mode": "anime"})
    view.add_item({"title": G.args.addon.getLocalizedString(30049),
                   "mode": "crunchylists_lists"})
    view.add_item({"title": G.args.addon.getLocalizedString(30072) % str(G.api.profile_data.profile_name),
                   "mode": "profiles_list", "thumb": utils.get_img_from_static(G.api.profile_data.avatar)})
    # @TODO: i think there are no longer dramas. should we add music videos and movies?
    # view.add_item(args,
    #              {"title": G.args.addon.getLocalizedString(30051),
    #               "mode":  "drama"})
    view.end_of_directory(update_listing=True, cache_to_disc=False)


def show_main_category(genre):
    """Show main category
    """
    # view.add_item(args,
    #               {"title": G.args.addon.getLocalizedString(30058),
    #                "mode": "featured",
    #                "category_filter": "popular",
    #                "genre": genre})
    view.add_item({"title": G.args.addon.getLocalizedString(30052),
                   "category_filter": "popularity",
                   "mode": "popular",
                   "genre": genre})
    # view.add_item(args,
    #               {"title": "TODO | " + G.args.addon.getLocalizedString(30053),
    #                "mode": "simulcast",
    #                "genre": genre})
    # view.add_item(args,
    #               {"title": "TODO | " + G.args.addon.getLocalizedString(30054),
    #                "mode": "updated",
    #                "genre": genre})
    view.add_item({"title": G.args.addon.getLocalizedString(30059),
                   "category_filter": "newly_added",
                   "mode": "newest",
                   "genre": genre})
    view.add_item({"title": G.args.addon.getLocalizedString(30055),
                   "category_filter": "alphabetical",
                   "items_per_page": 100,
                   "mode": "alpha",
                   "genre": genre})
    view.add_item({"title": G.args.addon.getLocalizedString(30057),
                   "mode": "season",
                   "genre": genre})
    view.add_item({"title": G.args.addon.getLocalizedString(30056),
                   "mode": "genre",
                   "genre": genre})
    view.end_of_directory()
