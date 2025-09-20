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

import random
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
        char_set = "0123456789abcdefghijklmnopqrstuvwxyz0123456789"
        G.args._device_id = (
                "".join(random.sample(char_set, 8)) +
                "-KODI-" +
                "".join(random.sample(char_set, 4)) +
                "-" +
                "".join(random.sample(char_set, 4)) +
                "-" +
                "".join(random.sample(char_set, 12))
        )
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

                # Clear/replace the current listing with an empty one before showing the modal dialog,
                # so nothing remains visible behind the overlay.
                try:
                    view.end_of_directory(update_listing=True, cache_to_disc=False)
                except Exception:
                    pass

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

                import time as _t
                start_ts = _t.time()
                user_cancelled = False
                try:
                    while _t.time() - start_ts < expires_in:
                        # Stop polling if user closed the dialog
                        if hasattr(dialog, 'is_running') and not dialog.is_running:
                            user_cancelled = True
                            break

                        # Use the current device_code from dialog (might be updated if regenerated)
                        current_device_code = dialog.device_code if hasattr(dialog, 'device_code') else device_code
                        token = G.api.poll_device_token(current_device_code)
                        if token and token.get('access_token'):
                            # finalize session then reload addon root to render fresh UI
                            G.api._finalize_session_from_token_response(token)
                            try:
                                xbmc.executebuiltin(f"Container.Update({G.args.addonurl}, replace)")
                            except Exception:
                                pass
                            return True
                        # Use the dialog's current interval if it was regenerated, else fallback to the initial one
                        sleep_ms = getattr(dialog, 'interval_ms', interval_ms)
                        xbmc.sleep(max(1, int(sleep_ms)))
                    else:
                        # expired; close and inform
                        xbmcgui.Dialog().notification(G.args.addon_name, 'Activation expired. Please try again.', xbmcgui.NOTIFICATION_INFO, 5)
                finally:
                    try:
                        dialog.is_running = False  # Stop timer thread
                        dialog.close()
                    except Exception:
                        pass

                # If user cancelled, just exit cleanly; listing was already ended before dialog
                if user_cancelled and not G.api.account_data.access_token:
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
