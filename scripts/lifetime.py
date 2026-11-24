#!/usr/bin/env python3
"""Fetch an OutSystems mobile app's binaries (AAB/APK/IPA) from LifeTime and inspect them.

Subcommands:
  plan          Resolve the release workflow inputs against config/app.json (no network).
  fetch         Download the binary running in a LifeTime environment and verify it.
  status        Print the app's native build status in every LifeTime environment (read-only).
  inspect       Print identifiers/versions/signing of a local .aab/.apk/.ipa.
  check-config  Validate config/app.json.

Uses only the standard library so it runs on any CI runner without pip installs.
Environment (fetch/status): LIFETIME_HOST (e.g. mycompany-lt.outsystemsenterprise.com), LIFETIME_API_TOKEN.
"""

import argparse
import datetime
import http.client
import json
import os
import plistlib
import re
import shutil
import subprocess
import sys
import urllib.error
import urllib.parse
import urllib.request
import xml.parsers.expat
import zipfile
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
CONFIG_PATH = ROOT / "config" / "app.json"
PLATFORMS = ("android", "ios")
LIFETIME_PLATFORM = {"android": "Android", "ios": "iOS"}
IDENTIFIER_KEY = {"android": "androidPackage", "ios": "iosBundleId"}
UUID_RE = re.compile(r"^[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}$")
# Android application IDs: segments of letters, digits and underscores, each starting with a letter.
ANDROID_ID_RE = re.compile(r"^[A-Za-z][A-Za-z0-9_]*(\.[A-Za-z][A-Za-z0-9_]*)+$")
# iOS bundle IDs: segments of letters, digits and hyphens (e.g. com.example.my-app).
IOS_ID_RE = re.compile(r"^[A-Za-z0-9-]+(\.[A-Za-z0-9-]+)+$")
IDENTIFIER_RE = {"android": ANDROID_ID_RE, "ios": IOS_ID_RE}
# Versions end up in GITHUB_OUTPUT, file names and release tags: keep them to a safe character set.
VERSION_RE = re.compile(r"^[0-9A-Za-z][0-9A-Za-z._-]{0,63}$")


class PipelineError(Exception):
    pass


# --------------------------------------------------------------------------- config


def load_config(path=CONFIG_PATH):
    try:
        with open(path, encoding="utf-8") as f:
            return json.load(f)
    except OSError as e:
        raise PipelineError(f"Cannot read {path}: {e.strerror or e}") from None
    except ValueError as e:
        raise PipelineError(f"{Path(path).name} is not valid JSON: {e}") from None


def _text(app, key):
    """A config value as a string; anything that is not a string counts as missing."""
    value = app.get(key)
    return value if isinstance(value, str) else ""


def check_config(app):
    if not isinstance(app, dict):
        return ["the file must contain a JSON object"]
    errors = []
    if not re.fullmatch(r"[a-z0-9][a-z0-9-]*", _text(app, "id")):
        errors.append("id must be lowercase letters, digits and dashes")
    if not _text(app, "name").strip():
        errors.append("name is required")
    if not UUID_RE.match(_text(app, "lifetimeAppId")):
        errors.append("lifetimeAppId must be the LifeTime application key (a lowercase UUID)")
    if not _text(app, "environment").strip():
        errors.append("environment is required")
    platforms = app.get("platforms")
    if not isinstance(platforms, list) or not platforms or any(p not in PLATFORMS for p in platforms):
        errors.append(f"platforms must be a non-empty subset of {list(PLATFORMS)}")
        platforms = [p for p in platforms if p in PLATFORMS] if isinstance(platforms, list) else []
    for platform in dict.fromkeys(platforms):
        key = IDENTIFIER_KEY[platform]
        if not IDENTIFIER_RE[platform].match(_text(app, key)):
            errors.append(f"{key} is required for {platform} and must look like 'com.example.app'")
    return errors


def load_app(path=CONFIG_PATH):
    app = load_config(path)
    errors = check_config(app)
    if errors:
        raise PipelineError(f"{Path(path).name}: " + "; ".join(errors))
    return app


# --------------------------------------------------------------------------- LifeTime API


def ci(obj, key):
    """Case-insensitive dict lookup: LifeTime mixes casings (e.g. 'nativePlatform' vs swagger 'NativePlatform')."""
    if not isinstance(obj, dict):
        return None
    if key in obj:
        return obj[key]
    lowered = key.lower()
    return next((v for k, v in obj.items() if k.lower() == lowered), None)


class _HttpsOnlyRedirects(urllib.request.HTTPRedirectHandler):
    def redirect_request(self, req, fp, code, msg, headers, newurl):
        if urllib.parse.urlparse(newurl).scheme != "https":
            raise PipelineError(f"Refusing redirect to non-HTTPS URL ({urllib.parse.urlparse(newurl).scheme}://...)")
        return super().redirect_request(req, fp, code, msg, headers, newurl)


_opener = urllib.request.build_opener(_HttpsOnlyRedirects)


class LifeTime:
    def __init__(self, host, token):
        # Accept a bare host or a URL; keep only host[:port]. Always HTTPS, whatever scheme was given.
        host = re.sub(r"^[A-Za-z][A-Za-z0-9+.-]*://", "", host.strip()).split("/")[0].lower()
        if not re.fullmatch(r"[a-z0-9]([a-z0-9.-]*[a-z0-9])?(:[0-9]+)?", host):
            raise PipelineError("LIFETIME_HOST must be the LifeTime host name, e.g. lifetime.example.com")
        self.host = host
        self.base = f"https://{self.host}/lifetimeapi/rest/v2"
        self.token = token

    def _request(self, url, accept=None):
        req = urllib.request.Request(url, headers={"Accept": accept} if accept else {})
        # add_unredirected_header: the token is never forwarded if the server redirects elsewhere.
        req.add_unredirected_header("Authorization", f"Bearer {self.token}")
        return req

    def get(self, path, **query):
        url = self.base + path
        if query:
            url += "?" + urllib.parse.urlencode(query)
        try:
            with _opener.open(self._request(url, "application/json"), timeout=120) as resp:
                body = resp.read()
                status = resp.status
        except urllib.error.HTTPError as e:
            detail = e.read().decode("utf-8", "replace")[:500]
            raise PipelineError(f"LifeTime GET {path} failed: HTTP {e.code} {detail}") from None
        except (OSError, http.client.HTTPException) as e:
            # URLError, timeouts and dropped connections (also while reading the body).
            raise PipelineError(
                f"Cannot reach LifeTime at {self.host}: {getattr(e, 'reason', None) or str(e) or type(e).__name__}. "
                "If LifeTime has an IP allowlist, use a self-hosted runner or allowlist the runner IPs."
            ) from None
        if not body:
            return status, None
        try:
            return status, json.loads(body)
        except ValueError:
            # e.g. an SSO/proxy HTML page instead of the API: LIFETIME_HOST points at the wrong host.
            raise PipelineError(
                f"LifeTime GET {path} returned a non-JSON response (HTTP {status}): {body[:200].decode('utf-8', 'replace')!r}. "
                "Check that LIFETIME_HOST is the LifeTime host and LIFETIME_API_TOKEN is a LifeTime service account token."
            ) from None

    def environments(self):
        _, envs = self.get("/environments/")
        return [(ci(e, "Key"), ci(e, "Name")) for e in envs or []]

    def environment_key(self, name_or_key):
        envs = self.environments()
        for key, name in envs:
            if name_or_key in (key, name):
                return key, name
        raise PipelineError(f"Environment '{name_or_key}' not found. Available: {', '.join(n for _, n in envs)}")

    def app_status(self, env_key, app_key):
        _, app = self.get(f"/environments/{env_key}/applications/{app_key}/", IncludeEnvStatus="true")
        app = app or {}
        if ci(app, "Kind") != "Mobile":
            raise PipelineError(f"LifeTime app '{ci(app, 'Name')}' is not a mobile app (Kind={ci(app, 'Kind')})")
        status_in_env = next((s for s in ci(app, "AppStatusInEnvs") or [] if ci(s, "EnvironmentKey") == env_key), None)
        return app, status_in_env

    def mobile_status(self, env_key, app_key, platform):
        app, status_in_env = self.app_status(env_key, app_key)
        if not status_in_env:
            raise PipelineError(f"'{ci(app, 'Name')}' is not deployed in this environment")
        mobile = next(
            (m for m in ci(status_in_env, "MobileAppsStatus") or [] if ci(m, "NativePlatform") == LIFETIME_PLATFORM[platform]),
            None,
        )
        return app, status_in_env, mobile

    def download_link(self, env_key, app_key, binary_type):
        status, body = self.get(f"/environments/{env_key}/applications/{app_key}/content/", Type=binary_type)
        if status == 204 or not body:
            return None
        return ci(body, "url")

    def download(self, url, dest):
        if url.startswith("/"):
            url = f"https://{self.host}{url}"
        parsed = urllib.parse.urlparse(url)
        if parsed.scheme != "https":
            raise PipelineError(f"Refusing non-HTTPS download URL ({parsed.scheme}://{parsed.netloc})")
        # Only send the token to LifeTime itself, never to a storage/CDN host.
        req = self._request(url) if parsed.netloc.lower() == self.host else urllib.request.Request(url)
        try:
            with _opener.open(req, timeout=600) as resp, open(dest, "wb") as out:
                shutil.copyfileobj(resp, out, length=1024 * 1024)
        except urllib.error.HTTPError as e:
            raise PipelineError(f"Binary download from {parsed.netloc} failed: HTTP {e.code}") from None
        except (OSError, http.client.HTTPException) as e:
            # URLError, timeouts and connections dropped half way through the file.
            reason = getattr(e, "reason", None) or str(e) or type(e).__name__
            raise PipelineError(f"Binary download from {parsed.netloc} failed: {reason}") from None


# --------------------------------------------------------------------------- binary inspection


def _varint(buf, i):
    shift = result = 0
    while True:
        b = buf[i]
        i += 1
        result |= (b & 0x7F) << shift
        if not b & 0x80:
            return result, i
        shift += 7


def _proto_fields(buf):
    """Yield (field_number, wire_type, value) for a protobuf message; raise on malformed input."""
    i = 0
    while i < len(buf):
        key, i = _varint(buf, i)
        field, wire = key >> 3, key & 7
        if wire == 0:
            value, i = _varint(buf, i)
        elif wire == 1:
            value, i = buf[i : i + 8], i + 8
        elif wire == 2:
            length, i = _varint(buf, i)
            value, i = buf[i : i + length], i + length
        elif wire == 5:
            value, i = buf[i : i + 4], i + 4
        else:
            raise ValueError("unsupported wire type")
        if field == 0 or i > len(buf):
            raise ValueError("malformed")
        yield field, wire, value


def proto_manifest_attributes(buf, wanted, found=None, depth=0):
    """Find XmlAttribute{name=2, value=3} entries in an aapt2 proto AndroidManifest.xml."""
    found = {} if found is None else found
    if depth > 12:
        return found
    try:
        fields = list(_proto_fields(buf))
    except (ValueError, IndexError):
        return found
    strings = {f: v for f, w, v in fields if w == 2}
    name = strings.get(2)
    if name is not None and name.decode("utf-8", "replace") in wanted and 3 in strings:
        found.setdefault(name.decode("utf-8", "replace"), strings[3].decode("utf-8", "replace"))
    for _, wire, value in fields:
        if wire == 2 and len(value) > 1:
            proto_manifest_attributes(value, wanted, found, depth + 1)
    return found


def inspect_aab(path):
    with zipfile.ZipFile(path) as z:
        manifest = z.read("base/manifest/AndroidManifest.xml")
    attrs = proto_manifest_attributes(manifest, {"package", "versionCode", "versionName"})
    return {
        "identifier": attrs.get("package"),
        "versionName": attrs.get("versionName"),
        "versionCode": attrs.get("versionCode"),
    }


def inspect_apk(path):
    """APK manifests are binary XML; rely on aapt2 from the Android SDK when available."""
    unknown = {"identifier": None, "versionName": None, "versionCode": None}
    aapt = shutil.which("aapt2")
    sdk = os.environ.get("ANDROID_HOME") or os.environ.get("ANDROID_SDK_ROOT")
    if not aapt and sdk:
        candidates = sorted(Path(sdk).glob("build-tools/*/aapt2"), reverse=True)
        aapt = str(candidates[0]) if candidates else None
    if not aapt:
        return unknown
    out = subprocess.run([aapt, "dump", "badging", str(path)], capture_output=True, text=True).stdout
    m = re.search(r"package: name='([^']+)' versionCode='([^']*)' versionName='([^']*)'", out)
    if not m:
        return unknown
    return {"identifier": m.group(1), "versionCode": m.group(2), "versionName": m.group(3)}


def provisioning_profile_type(profile):
    """Classify a provisioning profile; anything that is not clearly App Store distribution is never 'app-store'."""
    if profile is None:
        return "unsigned"
    if profile.get("ProvisionsAllDevices"):
        return "enterprise"
    # get-task-allow (debugger attach) is only granted by development profiles, with or without a device list.
    if (profile.get("Entitlements") or {}).get("get-task-allow"):
        return "development"
    if profile.get("ProvisionedDevices"):
        return "ad-hoc"
    return "app-store"


def profile_expired(profile, now=None):
    expires = (profile or {}).get("ExpirationDate")
    if not isinstance(expires, datetime.datetime):
        return False
    # plistlib returns naive datetimes in UTC.
    now = now or datetime.datetime.now(datetime.timezone.utc).replace(tzinfo=None)
    return expires.replace(tzinfo=None) <= now


def parse_mobileprovision(data):
    start, end = data.find(b"<?xml"), data.find(b"</plist>")
    if start < 0 or end < 0:
        return None
    return plistlib.loads(data[start : end + len(b"</plist>")])


def inspect_ipa(path):
    with zipfile.ZipFile(path) as z:
        names = z.namelist()
        app_dir = next((m.group(0) for n in names for m in [re.match(r"Payload/[^/]+\.app/", n)] if m), None)
        if not app_dir:
            raise PipelineError(f"{path} is not a valid IPA (no Payload/*.app)")
        info = plistlib.loads(z.read(app_dir + "Info.plist"))
        profile_name = app_dir + "embedded.mobileprovision"
        profile = parse_mobileprovision(z.read(profile_name)) if profile_name in names else None
    return {
        "identifier": info.get("CFBundleIdentifier"),
        "versionName": info.get("CFBundleShortVersionString"),
        "versionCode": info.get("CFBundleVersion"),
        "profileType": provisioning_profile_type(profile),
        "profileName": (profile or {}).get("Name"),
        "teamId": ((profile or {}).get("TeamIdentifier") or [None])[0],
        "profileExpires": str((profile or {}).get("ExpirationDate") or ""),
        "profileExpired": profile_expired(profile),
    }


def inspect_binary(path):
    suffix = Path(path).suffix.lower()
    try:
        if suffix == ".aab":
            return inspect_aab(path)
        if suffix == ".apk":
            return inspect_apk(path)
        if suffix == ".ipa":
            return inspect_ipa(path)
    except (zipfile.BadZipFile, KeyError, plistlib.InvalidFileException, ValueError, xml.parsers.expat.ExpatError) as e:
        raise PipelineError(f"{Path(path).name} is not a valid {suffix} file: {e}") from None
    raise PipelineError(f"Unsupported binary type: {path}")


# --------------------------------------------------------------------------- CI helpers


def set_outputs(**values):
    target = os.environ.get("GITHUB_OUTPUT")
    for key, value in values.items():
        value = "" if value is None else str(value)
        if "\n" in value or "\r" in value:
            raise PipelineError(f"Refusing multi-line value for output '{key}'")
        print(f"  {key} = {value}")
        if target:
            with open(target, "a", encoding="utf-8") as f:
                f.write(f"{key}={value}\n")


def summary(markdown):
    target = os.environ.get("GITHUB_STEP_SUMMARY")
    if target:
        with open(target, "a", encoding="utf-8") as f:
            f.write(markdown + "\n")


def _annotate(kind, message):
    # GitHub Actions renders ::error:: / ::warning:: lines as annotations on the run page.
    if os.environ.get("GITHUB_ACTIONS"):
        # A workflow command is one line: escape the message so that text taken from a server
        # response or a binary can neither cut it short nor start another command.
        message = message.replace("%", "%25").replace("\r", "%0D").replace("\n", "%0A")
        print(f"::{kind}::{message}", file=sys.stderr)
    else:
        print(f"{kind.upper()}: {message}", file=sys.stderr)


def error(message):
    _annotate("error", message)


def warning(message):
    _annotate("warning", message)


PLACEHOLDER_APP_ID = "00000000-0000-0000-0000-000000000000"


def require_configured(app):
    """The template ships with placeholder values; refuse to call LifeTime until they are replaced."""
    if app["lifetimeAppId"] == PLACEHOLDER_APP_ID:
        raise PipelineError(
            "config/app.json still has the template placeholder lifetimeAppId. "
            "Set it (and name, androidPackage, iosBundleId, environment) to your app: see docs/SETUP.md."
        )


def lifetime_client():
    host, token = os.environ.get("LIFETIME_HOST"), os.environ.get("LIFETIME_API_TOKEN")
    if not host or not token:
        raise PipelineError("LIFETIME_HOST and LIFETIME_API_TOKEN must be set")
    return LifeTime(host, token)


# --------------------------------------------------------------------------- commands


def cmd_plan(args):
    app = load_app()
    requested = PLATFORMS if args.platform == "both" else (args.platform,)
    platforms = [p for p in requested if p in app["platforms"]]
    if not platforms:
        raise PipelineError(f"{app['name']} is not configured for {args.platform} (configured: {app['platforms']})")
    environment = args.environment if args.environment not in ("", "default") else app["environment"]
    set_outputs(
        id=app["id"],
        name=app["name"],
        environment=environment,
        android=str("android" in platforms).lower(),
        ios=str("ios" in platforms).lower(),
        android_package=app.get("androidPackage"),
        ios_bundle_id=app.get("iosBundleId"),
    )


def cmd_fetch(args):
    app = load_app()
    if args.platform not in app["platforms"]:
        raise PipelineError(f"{app['name']} is not configured for {args.platform}")
    require_configured(app)
    lt = lifetime_client()

    env_key, env_name = lt.environment_key(args.environment or app["environment"])
    lt_app, env_status, mobile = lt.mobile_status(env_key, app["lifetimeAppId"], args.platform)
    app_name = ci(lt_app, "Name") or app["name"]
    label = f"{app_name} / {LIFETIME_PLATFORM[args.platform]} in {env_name}"
    if not mobile:
        raise PipelineError(f"{label}: no {LIFETIME_PLATFORM[args.platform]} build configured in LifeTime")
    print(f"{label}: version {ci(mobile, 'VersionNumber') or '?'} (app version {ci(env_status, 'BaseApplicationVersionLabel') or '-'})")
    if not ci(mobile, "IsConfigured"):
        raise PipelineError(f"{label}: native platform not configured in LifeTime (Applications > {app_name} > Native Platforms)")
    if ci(mobile, "IsConfigurationChanged"):
        warning(f"{label}: native configuration changed since the last build; the binary may be outdated. Regenerate it in LifeTime if needed.")
    if not ci(mobile, "HasBinaryAvailable"):
        raise PipelineError(f"{label}: no binary available. Generate the build in LifeTime first and wait for it to finish.")

    types = ("aab", "apk") if args.platform == "android" else ("ipa",)
    url = binary_type = None
    for binary_type in types:
        url = lt.download_link(env_key, app["lifetimeAppId"], binary_type)
        if url:
            break
    if not url:
        raise PipelineError(
            f"{label}: the API returned no {'/'.join(types)} (HTTP 204). LifeTime only serves builds attached to a tagged version, "
            "and the running version isn't tagged (e.g. a hotfix, or a build generated from Service Center). "
            "Deploy a tagged version to this environment through LifeTime, or tag the running version there."
        )

    out_dir = Path(args.out)
    out_dir.mkdir(parents=True, exist_ok=True)
    tmp = out_dir / f"{app['id']}-{args.platform}.download"
    if args.dry_run:
        print(f"Dry run: download link obtained for .{binary_type}, skipping download.")
        set_outputs(file="", version=ci(mobile, "VersionNumber"), environment=env_name)
        return
    lt.download(url, tmp)

    # The API may serve an .apk when .aab was requested; detect it from the file.
    try:
        with zipfile.ZipFile(tmp) as z:
            names = set(z.namelist())
    except zipfile.BadZipFile:
        raise PipelineError(f"{label}: the downloaded file is not a valid app archive") from None
    ext = "ipa" if args.platform == "ios" else ("aab" if "base/manifest/AndroidManifest.xml" in names else "apk")
    dest = tmp.with_suffix("." + ext)
    tmp.replace(dest)
    if ext == "apk":
        warning(f"{label}: LifeTime served an APK. Google Play requires an Android App Bundle (.aab) for new apps; set the build to AAB in LifeTime Native Platforms.")

    meta = inspect_binary(dest)
    expected = app[IDENTIFIER_KEY[args.platform]]
    if not meta.get("identifier"):
        raise PipelineError(
            f"{label}: could not read the app identifier from {dest.name}, so it cannot be verified against {expected}"
            + (" (APK inspection needs aapt2 from the Android SDK)" if ext == "apk" else "")
        )
    if meta["identifier"] != expected:
        raise PipelineError(f"{label}: binary identifier {meta['identifier']} does not match configured {expected}")
    if args.platform == "ios" and meta.get("profileType") != "app-store":
        raise PipelineError(
            f"{label}: IPA is signed with a '{meta.get('profileType')}' profile ({meta.get('profileName')}). "
            "App Store Connect only accepts App Store distribution builds; set the build type to 'App Store' in LifeTime Native Platforms for this environment."
        )
    if args.platform == "ios" and meta.get("profileExpired"):
        raise PipelineError(
            f"{label}: the IPA's provisioning profile ({meta.get('profileName')}) expired on {meta.get('profileExpires')} UTC, "
            "so App Store Connect would reject it. Renew the distribution profile, update it in LifeTime Native Platforms and regenerate the build."
        )

    # Name the file after the version inside the binary; LifeTime's VersionNumber can lag behind it.
    version = meta.get("versionName") or ci(mobile, "VersionNumber")
    build = meta.get("versionCode")
    for name, value in (("version", version), ("build", build)):
        if not VERSION_RE.match(str(value or "")):
            raise PipelineError(f"{label}: unexpected {name} '{value}' in the binary")
    dest = dest.replace(out_dir / f"{app['id']}-{version}-{build}.{ext}")

    size_mb = dest.stat().st_size / 1_048_576
    print(json.dumps(meta, indent=2))
    set_outputs(file=str(dest.resolve()), identifier=meta["identifier"], version=version, build=build, environment=env_name)
    summary(
        f"### {LIFETIME_PLATFORM[args.platform]} binary\n\n"
        f"| | |\n|---|---|\n"
        f"| App | {app_name} |\n| Environment | {env_name} |\n"
        f"| App version (LifeTime) | {ci(env_status, 'BaseApplicationVersionLabel') or '-'} |\n"
        f"| File | `{dest.name}` ({size_mb:.1f} MB) |\n"
        f"| Identifier | `{meta['identifier']}` |\n"
        f"| Version / build | {version} / {build} (LifeTime reports {ci(mobile, 'VersionNumber')}) |\n"
        + (f"| Signing | {meta.get('profileType')} ({meta.get('profileName')}, team {meta.get('teamId')}) |\n" if args.platform == "ios" else "")
    )


def cmd_status(_args):
    app = load_app()
    require_configured(app)
    lt = lifetime_client()
    rows = []
    for env_key, env_name in lt.environments():
        try:
            _, status_in_env = lt.app_status(env_key, app["lifetimeAppId"])
        except PipelineError as e:
            rows.append(f"| {env_name} | - | - | {e} |")
            continue
        if not status_in_env:
            rows.append(f"| {env_name} | not deployed | - | - |")
            continue
        version = ci(status_in_env, "BaseApplicationVersionLabel") or "-"
        builds = []
        for m in ci(status_in_env, "MobileAppsStatus") or []:
            flags = [f for f, ok in (("configured", ci(m, "IsConfigured")), ("binary", ci(m, "HasBinaryAvailable"))) if ok]
            if ci(m, "IsConfigurationChanged"):
                flags.append("config changed")
            builds.append(f"{ci(m, 'NativePlatform')} {ci(m, 'VersionNumber') or '?'} ({', '.join(flags) or 'not built'})")
        rows.append(f"| {env_name} | {version} | {'; '.join(builds) or '-'} | |")
    table = "| Environment | App version | Native builds | Error |\n|---|---|---|---|\n" + "\n".join(rows)
    print(table)
    summary(f"### {app['name']} in LifeTime\n\n{table}")


def cmd_inspect(args):
    print(json.dumps(inspect_binary(args.file), indent=2))


def cmd_check_config(_args):
    errors = check_config(load_config())
    for e in errors:
        error(f"config/app.json: {e}")
    if errors:
        sys.exit(1)
    print("config/app.json OK")


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    sub = parser.add_subparsers(dest="command", required=True)

    p = sub.add_parser("plan", help="Resolve workflow inputs (no network)")
    p.add_argument("--platform", choices=("both",) + PLATFORMS, default="both")
    p.add_argument("--environment", default="", help="LifeTime environment name; empty or 'default' uses config/app.json")
    p.set_defaults(func=cmd_plan)

    p = sub.add_parser("fetch", help="Download and verify the binary from LifeTime")
    p.add_argument("--platform", choices=PLATFORMS, required=True)
    p.add_argument("--environment", default="", help="LifeTime environment name; empty uses config/app.json")
    p.add_argument("--out", default="build")
    p.add_argument("--dry-run", action="store_true", help="Check status and get the link, but do not download")
    p.set_defaults(func=cmd_fetch)

    sub.add_parser("status", help="Show the native build status in every LifeTime environment").set_defaults(func=cmd_status)

    p = sub.add_parser("inspect", help="Inspect a local .aab/.apk/.ipa")
    p.add_argument("file")
    p.set_defaults(func=cmd_inspect)

    sub.add_parser("check-config", help="Validate config/app.json").set_defaults(func=cmd_check_config)

    args = parser.parse_args(argv)
    try:
        args.func(args)
    except PipelineError as e:
        error(str(e))
        sys.exit(1)


if __name__ == "__main__":
    main()
