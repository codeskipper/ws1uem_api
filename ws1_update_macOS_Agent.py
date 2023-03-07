#!/Library/ManagedFrameworks/Python/Python3.framework/Versions/Current/bin/python3

"""
Script to workaround VMWare WorkSpace One Hub for macOS update issue
see https://kb.vmware.com/s/article/88834?lang=en_US

Made for running on a Mac, tested with MacAdmins Python recommended
"""

import base64
from datetime import datetime
import requests
import subprocess
import logging
from optparse import OptionParser

# DEBUG = os.environ.get("DEBUG", "False").lower() in ("true", "1", "t")
# DRY_RUN = os.environ.get("DRY_RUN", "False").lower() in ("true", "1", "t")

log = logging.getLogger("ws1-script-logger")


def get_from_keychain(keychain, secret):
    """ fetch secrets from dedicated macOS Keychain """
    cmd = [
        '/usr/bin/security',
        'find-generic-password',
        '-a',
        f'"{secret}"',
        '-w',
        f'{keychain}'
    ]
    hide_cmd_output = True
    log.debug("Running " + " ".join(cmd))
    try:
        result = subprocess.run(" ".join(cmd), check=True, shell=True, capture_output=hide_cmd_output, text=True)
        log.debug(result)
    except subprocess.CalledProcessError as e:
        log.error(e.stderr)
        raise e
    return result.stdout.strip()


def get_basicauth_headers(ws1_api_username, ws1_api_password, ws1_api_token):
    """ format API V1 headers supplied for Basic authentication to WS1 UEM """
    hashed_auth = base64.b64encode(f'{ws1_api_username}:{ws1_api_password}'.encode("UTF-8"))
    basicauth = 'Basic {}'.format(hashed_auth.decode("utf-8"))
    log.debug('Authorization header: {}'.format(basicauth))
    headers = {'aw-tenant-code': ws1_api_token,
               'Accept': 'application/json',
               'Content-Type': 'application/json',
               'authorization': basicauth}
    # headers_v2 = dict(headers)
    # headers_v2['Accept'] = headers['Accept'] + ';version=2'
    # log.debug(f'API v.2 call headers: {headers_v2}', verbose_level=3)
    return headers


def get_all_pages(url, headers, data_type):
    """ fetch output from API which might span multiple pages
    - thanks https://macadmins.slack.com/archives/C053TS6JT/p1664288919563019?thread_ts=1664199787.549039&cid=C053TS6JT
    """
    data = {data_type: []}
    page = 0
    params = {"page": page}
    while True:
        resp = requests.get(url=url, params=params, headers=headers)
        rawdata = resp.json()
        data[data_type].extend(rawdata[data_type])

        if (rawdata["Page"] + 1) * rawdata["PageSize"] < rawdata["Total"]:
            params = {"page": page + 1}
            continue
        break
    return data


def main():
    parser = OptionParser(description="Use WorkSpace ONE UEM API to fetch Mac devices with old Hub and run "
                                      "InstallPackagedMacOSXAgent command on those.")
    parser.add_option(
        "-V",
        "--versions",
        action="store_true",
        default="22.12.0.9 23.01.0.19",
        dest="versions",
        help="Which macOS Hub versions are acceptable, space separated.",
    )
    parser.add_option(
        "-k",
        "--keychain",
        action="store_true",
        default="autopkg_tools_launcher_keychain",
        help="Which macOS Keychain to use for settings and credentials",
    )
    parser.add_option(
        "-v",
        "--verbose",
        action="count",
        dest="verbosity",
        default=0,
        help="increment output verbosity; may be specified multiple times",
    )
    parser.add_option(
        "-d",
        "--dry-run",
        dest="dry_run",
        default=False,
        action="store_true",
        help="fetch only, do not run action.",
    )
    (opts, _) = parser.parse_args()

    # For each -v passed on the commandline, a lower log.level will be enabled.
    # log.ERROR by default, log.INFO with -vv, etc.
    log.addHandler(logging.StreamHandler())
    log.level = max(logging.ERROR - (opts.verbosity * 10), 1)

    accepted_versions = opts.versions.split(" ")
    log.debug(f"Hub versions specified as accepted_versions:[{accepted_versions}]")

    print("ws1_update macOS_Agent starting.")

    # get credentials from keychain
    ws1_api_url = get_from_keychain(opts.keychain, "WS1_API_URL")
    print(f"The WorkSpace ONE API URL found in the keychain [{opts.keychain}] is [{ws1_api_url}]")
    ws1_api_username = get_from_keychain(opts.keychain, "WS1_API_USERNAME")
    ws1_api_password = get_from_keychain(opts.keychain, "WS1_API_PASSWORD")
    ws1_api_token = get_from_keychain(opts.keychain, "WS1_API_TOKEN")

    # prepare request headers
    headers = get_basicauth_headers(ws1_api_username, ws1_api_password, ws1_api_token)

    # Get all macOS devices
    url = f"{ws1_api_url}/API/mdm/devices/search?platform=AppleOsX"
    data = get_all_pages(url=url, headers=headers, data_type="Devices")

    i = 0
    hub_version_num_not_found = 0
    hub_version_num_install_requests = 0
    for item in data["Devices"]:
        device_id = str(item["Id"]["Value"])
        device_uuid = str(item["Uuid"])
        # last_seen = str(item["LastSeen"])
        last_seen = datetime.fromisoformat(item["LastSeen"])
        diff = datetime.today() - last_seen
        last_seen_hours = diff.total_seconds() / 3600
        i += 1

        # get value for sensor hub_version
        url = f"{ws1_api_url}/API/mdm/devices/{device_uuid}/sensors"
        params = {"search_text": "hub_version"}
        resp = requests.get(url=url, params=params, headers=headers)
        rawdata = resp.json()
        if rawdata["total_results"] == 1:
            log.debug(f"Sensor search result: [{rawdata}]")
            hub_version = rawdata["results"][0]["value"]
        else:
            hub_version = "No result"
            hub_version_num_not_found += 1

        if hub_version not in accepted_versions:
            log.info(
                f"Mac device #{i} device_Id:[{device_id}] UUID:[{device_uuid}] hub_version:[{hub_version}] "
                f"last seen:[{last_seen_hours:.0f}] hours ago")
            if not opts.dry_run:
                url = f"{ws1_api_url}/API/mdm/devices/{device_id}/commands?command=InstallPackagedMacOSXAgent"
                resp = requests.post(url=url, headers=headers)
                hub_version_num_install_requests += 1
                print(f"Request to update hub on device {device_id}. Result: {resp.status_code}")
                # time.sleep(1)
    log.info(f"hub_version_num_not_found on : [{hub_version_num_not_found}] Macs")
    log.info(f"hub_version_num_install_requests sent: [{hub_version_num_install_requests}]")


if __name__ == "__main__":
    main()
